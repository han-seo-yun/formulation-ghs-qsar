#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""타깃 정의 격자 실측 — 이진화 정의 × label_regime × 피처세트.

목적
----
critic 감사의 BLOCKER 2건(① 실측계열/규제계열 봉합 ② eye 2B=양성 한 줄)이
실제 성능에 어떤 영향을 주는지 **측정만** 한다. 정의를 바꿔서 성능이 나빠지면
나빠진 대로 보고한다.

절대 규칙 준수
--------------
* 기존 파일 무수정. measure_v5_performance.py 의 모델/CV/피처 설정을 그대로 복제.
* 라벨 이진화 변형은 **메모리 안에서만** 생성. 디스크의 y_*_bin 은 건드리지 않음.
* 하이퍼파라미터·임계값 튜닝 없음. GHS CT 가산 공식 무수정.
* pc2_* 등 열 순서 변동 방어 — 컬럼명 정렬 접근만 사용, 위치 인덱싱 금지.
* 다른 에이전트가 쓰는 v4_fixed/판정결과_A.csv, 문서연결_감사.* 는 읽지 않음.

산출
----
04_모델산출물/v5_perf_grid/target_definition_grid.csv   (long format)
04_모델산출물/v5_perf_grid/target_definition_grid.json  (요약)
04_모델산출물/v5_perf_grid/run.log
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    matthews_corrcoef,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04_모델산출물" / "v4_fixed"            # 읽기 전용
OUT = ROOT / "04_모델산출물" / "v5_perf_grid"
OUT.mkdir(parents=True, exist_ok=True)

SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
ENDPOINTS = ["eye", "skin", "sens"]

# 저신뢰 판정 기준
MIN_N = 150
MIN_CLASS_N = 25

_t0 = time.time()
_LOGF = open(OUT / "run.log", "w", encoding="utf-8")


def log(msg: str) -> None:
    line = f"[{time.time()-_t0:7.1f}s] {msg}"
    print(line, flush=True)
    _LOGF.write(line + "\n")
    _LOGF.flush()


def rf(seed: int) -> RandomForestClassifier:
    """정규 베이스라인 — measure_v5_performance.py:62-67 과 동일. 변경 금지."""
    return RandomForestClassifier(
        n_estimators=300, class_weight="balanced", min_samples_leaf=2,
        n_jobs=-1, random_state=seed,
    )


# ================================================================ 데이터 로드
log("데이터 로드")
X = pd.read_parquet(SRC / "X_formulation.parquet")
Y = pd.read_parquet(SRC / "y_formulation.parquet")
GRP = pd.read_parquet(SRC / "groups.parquet")
MAN = pd.read_csv(SRC / "feature_role_manifest.csv")

assert (X["Formulation_ID"].values == Y["Formulation_ID"].values).all(), "행 정렬 불일치"
FID = X["Formulation_ID"].astype(str).to_numpy()
GK = Y["group_key"].astype(str).to_numpy()
assert (GRP.set_index("Formulation_ID")["group_key"]
        .reindex(Y["Formulation_ID"]).astype(str).to_numpy() == GK).all()

MANF = MAN[MAN["sheet"] == "formulation"].copy()
assert set(MANF["column"]) == set(X.columns), "manifest 와 X 열 집합 불일치"

# 피처: feature_role=='chemistry' and exclude_by_default==False (현행 S1_chemistry)
CHEM = sorted(
    c for c in MANF[(MANF["feature_role"] == "chemistry")
                    & (~MANF["exclude_by_default"].astype(bool))]["column"]
    if c != "Formulation_ID"
)
CT_COLS = sorted(c for c in CHEM if c.startswith(("f_ct_", "ct_")))
CHEM_NOCT = sorted(set(CHEM) - set(CT_COLS))
FEATURE_SETS = {"S1_chemistry": CHEM, "S1_minus_CT26": CHEM_NOCT}
log(f"S1_chemistry={len(CHEM)}열, CT유래={len(CT_COLS)}열, S1_minus_CT26={len(CHEM_NOCT)}열")
assert len(CT_COLS) == 26, f"CT 열 수가 26이 아님: {len(CT_COLS)}"

XMAT = {k: np.nan_to_num(X[v].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        for k, v in FEATURE_SETS.items()}

# ================================================================ 타깃 정의
# 축 1: 이진화 정의 — 양성 코드 집합 (그 외 라벨값은 음성), drop 은 행 제외
POS_INCLUSIVE = {
    "eye": {"1", "2", "2A", "2B"},
    "skin": {"1", "1A", "1B", "1C", "2", "3", "2A", "2B"},
    "sens": {"1", "1A", "1B"},
}
POS_STRICT = {
    # 임무 명세 문자 그대로: eye 는 2B 제외, skin 은 3·2A·2B 제외
    "eye": {"1", "2", "2A"},
    "skin": {"1", "1A", "1B", "1C", "2"},
    "sens": {"1", "1A", "1B"},   # sens 는 inclusive 와 동일
}
# strict_drop: 아래 코드 행을 음성으로 옮기지 않고 아예 제외
DROP_CODES = {"eye": {"2B"}, "skin": {"3"}, "sens": set()}
# 명세 해석 보강: skin 의 strict 는 3 뿐 아니라 2A/2B 도 음성으로 만든다(명세 집합 그대로).
# 의도(=3만 음성)와 다를 수 있어 별도 변형을 추가 측정한다.
POS_STRICT_ONLY3 = {"skin": {"1", "1A", "1B", "1C", "2", "2A", "2B"}}

BINARIZATIONS: dict[str, dict] = {
    "inclusive": {"pos": POS_INCLUSIVE, "drop": {ep: set() for ep in ENDPOINTS}},
    "strict": {"pos": POS_STRICT, "drop": {ep: set() for ep in ENDPOINTS}},
    "strict_drop": {"pos": POS_STRICT, "drop": DROP_CODES},
    # skin 전용 보조 변형 (eye/sens 에는 적용하지 않음 — 아래 루프에서 skip)
    "strict_skin_only3": {"pos": {**POS_STRICT, "skin": POS_STRICT_ONLY3["skin"]},
                          "drop": {ep: set() for ep in ENDPOINTS}},
}

# 축 2: label_regime — y_{ep}_src_detail 값 집합
SDS_FAMILY = {"sds_v1", "sds_v1_negative_recovered", "sds_2nd",
              "sds_2nd_sec11", "phase1_sds"}
REGIMES: dict[str, set[str] | None] = {
    "all": None,
    "measured": {"ntp_measured"},
    "regulatory": set(SDS_FAMILY),
    "regulatory_minus_sdsv1": SDS_FAMILY - {"sds_v1", "sds_v1_negative_recovered"},
}

# 관측된 src_detail 값이 위 분류에 모두 들어가는지 확인 (미분류 값 조용히 누락 방지)
for ep in ENDPOINTS:
    obs = set(Y[f"y_{ep}_src_detail"].dropna().astype(str).unique())
    unknown = obs - SDS_FAMILY - {"ntp_measured"}
    log(f"src_detail[{ep}] 관측값={sorted(obs)}"
        + (f"  ※미분류={sorted(unknown)}" if unknown else ""))
    assert not unknown, f"{ep}: 미분류 src_detail {unknown}"


def build_target(ep: str, binz: str, regime: str):
    """(mask, y) 반환. 라벨은 메모리 안에서만 생성한다."""
    raw = Y[f"y_{ep}"].astype("object")
    have = raw.notna().to_numpy()
    spec = BINARIZATIONS[binz]
    codes = raw.fillna("").astype(str).to_numpy()

    m = have & ~np.isin(codes, list(spec["drop"][ep])) if spec["drop"][ep] else have.copy()

    rs = REGIMES[regime]
    if rs is not None:
        det = Y[f"y_{ep}_src_detail"].fillna("").astype(str).to_numpy()
        m = m & np.isin(det, list(rs))

    y = np.isin(codes, list(spec["pos"][ep])).astype(int)
    return m, y[m]


# 무결성 확인: inclusive/all 이 디스크의 y_*_bin 과 일치해야 한다
for ep in ENDPOINTS:
    m, y = build_target(ep, "inclusive", "all")
    disk = Y.loc[m, f"y_{ep}_bin"].astype(int).to_numpy()
    agree = float((y == disk).mean())
    log(f"무결성[{ep}] inclusive/all n={m.sum()} vs 디스크 y_{ep}_bin 일치율={agree:.4f}")
    assert agree == 1.0, f"{ep}: inclusive 재구성이 y_{ep}_bin 과 불일치"


# ==================================================================== 지표
def metrics_from_oof(y, proba, pred, dpred) -> dict:
    p = float(y.mean())
    return {
        "roc_auc": float(roc_auc_score(y, proba)),
        "pr_auc": float(average_precision_score(y, proba)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "mcc": float(matthews_corrcoef(y, pred)),
        "dummy_mf_f1": float(f1_score(y, dpred, zero_division=0)),
        "dummy_mf_balanced_accuracy": float(balanced_accuracy_score(y, dpred)),
        "dummy_mf_mcc": float(matthews_corrcoef(y, dpred)),
        "prevalence": p,
    }


def run_cv(Xi: np.ndarray, y: np.ndarray, g: np.ndarray) -> dict:
    """measure_v5_performance.py:174-216 과 동일 절차."""
    per_seed, fold_auc = [], []
    for sd in SEEDS:
        cv = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=sd)
        proba = np.full(len(y), np.nan)
        pred = np.full(len(y), -1)
        dpred = np.full(len(y), -1)
        for tr, te in cv.split(Xi, y, groups=g):
            clf = rf(sd).fit(Xi[tr], y[tr])
            proba[te] = clf.predict_proba(Xi[te])[:, list(clf.classes_).index(1)]
            pred[te] = clf.predict(Xi[te])
            dm = DummyClassifier(strategy="most_frequent").fit(Xi[tr], y[tr])
            dpred[te] = dm.predict(Xi[te])
            try:
                fold_auc.append(roc_auc_score(y[te], proba[te]))
            except ValueError:
                pass
        assert not np.isnan(proba).any()
        per_seed.append(metrics_from_oof(y, proba, pred, dpred))
    agg = {}
    for k in per_seed[0]:
        v = np.array([s[k] for s in per_seed], dtype=float)
        agg[k] = float(v.mean())
        agg[k + "_sd"] = float(v.std(ddof=1)) if len(v) > 1 else 0.0
    agg["fold_roc_auc_sd"] = float(np.std(fold_auc, ddof=1)) if len(fold_auc) > 1 else 0.0
    return agg


# ==================================================================== 격자 실행
log("=== 격자 실행 ===")
rows: list[dict] = []
skipped: list[dict] = []

for ep in ENDPOINTS:
    for binz in BINARIZATIONS:
        # sens: strict/strict_drop 은 inclusive 와 동일 -> 중복 측정 안 함
        if ep == "sens" and binz != "inclusive":
            skipped.append({"endpoint": ep, "binarization": binz, "regime": "*",
                            "reason": "sens 는 1A/1B 세부구분만 존재 — strict==inclusive, 중복"})
            continue
        if binz == "strict_skin_only3" and ep != "skin":
            continue
        if ep == "eye" and binz == "strict_skin_only3":
            continue
        for regime in REGIMES:
            m, y = build_target(ep, binz, regime)
            g = GK[m]
            n = int(m.sum())
            n_pos = int(y.sum())
            n_neg = n - n_pos
            n_groups = int(len(np.unique(g)))
            base = {
                "endpoint": ep, "binarization": binz, "label_regime": regime,
                "n": n, "n_pos": n_pos, "n_neg": n_neg, "n_groups": n_groups,
                "prevalence": (float(y.mean()) if n else float("nan")),
                "dummy_f1_formula_2p_over_1plusp": (
                    float(2 * y.mean() / (1 + y.mean())) if n else float("nan")),
            }
            if n == 0:
                skipped.append({**base, "reason": "행 0개 — 측정 불가"})
                log(f"  {ep:4s} {binz:17s} {regime:22s} n=0 → 측정 불가")
                continue
            if len(np.unique(y)) < 2 or min(n_pos, n_neg) < N_SPLITS or n_groups < N_SPLITS:
                skipped.append({**base, "reason": "클래스/그룹 부족 — CV 불가"})
                log(f"  {ep:4s} {binz:17s} {regime:22s} n={n} pos={n_pos} "
                    f"grp={n_groups} → CV 불가")
                continue
            low = (n < MIN_N) or (min(n_pos, n_neg) < MIN_CLASS_N)
            for fs, cols in FEATURE_SETS.items():
                r = run_cv(XMAT[fs][m], y, g)
                rec = {**base, "feature_set": fs, "n_features": len(cols),
                       "low_confidence": bool(low), "seeds": ",".join(map(str, SEEDS)),
                       **r}
                rows.append(rec)
                log(f"  {ep:4s} {binz:17s} {regime:22s} {fs:14s} "
                    f"n={n:4d} grp={n_groups:4d} p={rec['prevalence']:.3f} "
                    f"AUC={r['roc_auc']:.3f}±{r['roc_auc_sd']:.3f} "
                    f"PR={r['pr_auc']:.3f} MCC={r['mcc']:.3f}±{r['mcc_sd']:.3f} "
                    f"BA={r['balanced_accuracy']:.3f} "
                    f"F1={r['f1']:.3f}(더미실측{r['dummy_mf_f1']:.3f}/"
                    f"식{base['dummy_f1_formula_2p_over_1plusp']:.3f})"
                    + ("  [저신뢰]" if low else ""))

DF = pd.DataFrame(rows)
DF.to_csv(OUT / "target_definition_grid.csv", index=False)
log(f"→ {OUT/'target_definition_grid.csv'}  ({len(DF)}행)")

# ------------------------------------------------------------- 파생 비교표
def cell(ep, binz, regime, fs):
    q = DF[(DF.endpoint == ep) & (DF.binarization == binz)
           & (DF.label_regime == regime) & (DF.feature_set == fs)]
    return q.iloc[0].to_dict() if len(q) else None


deltas = []
for ep in ENDPOINTS:
    for regime in REGIMES:
        for fs in FEATURE_SETS:
            a = cell(ep, "inclusive", regime, fs)
            for binz in ("strict", "strict_drop", "strict_skin_only3"):
                b = cell(ep, binz, regime, fs)
                if a is None or b is None:
                    continue
                deltas.append({
                    "comparison": f"{binz}_vs_inclusive", "endpoint": ep,
                    "label_regime": regime, "feature_set": fs,
                    "n_inclusive": a["n"], "n_variant": b["n"],
                    "p_inclusive": a["prevalence"], "p_variant": b["prevalence"],
                    "n_groups_inclusive": a["n_groups"], "n_groups_variant": b["n_groups"],
                    "d_roc_auc": b["roc_auc"] - a["roc_auc"],
                    "sd_pooled_roc_auc": float(np.hypot(a["roc_auc_sd"], b["roc_auc_sd"])),
                    "d_mcc": b["mcc"] - a["mcc"],
                    "sd_pooled_mcc": float(np.hypot(a["mcc_sd"], b["mcc_sd"])),
                    "d_f1_minus_dummy": ((b["f1"] - b["dummy_mf_f1"])
                                         - (a["f1"] - a["dummy_mf_f1"])),
                    "low_confidence": bool(a["low_confidence"] or b["low_confidence"]),
                })
# CT26 제외 효과
for ep in ENDPOINTS:
    for binz in BINARIZATIONS:
        for regime in REGIMES:
            a = cell(ep, binz, regime, "S1_chemistry")
            b = cell(ep, binz, regime, "S1_minus_CT26")
            if a is None or b is None:
                continue
            deltas.append({
                "comparison": "minusCT26_vs_S1", "endpoint": ep,
                "label_regime": regime, "feature_set": f"{binz}",
                "n_inclusive": a["n"], "n_variant": b["n"],
                "p_inclusive": a["prevalence"], "p_variant": b["prevalence"],
                "n_groups_inclusive": a["n_groups"], "n_groups_variant": b["n_groups"],
                "d_roc_auc": b["roc_auc"] - a["roc_auc"],
                "sd_pooled_roc_auc": float(np.hypot(a["roc_auc_sd"], b["roc_auc_sd"])),
                "d_mcc": b["mcc"] - a["mcc"],
                "sd_pooled_mcc": float(np.hypot(a["mcc_sd"], b["mcc_sd"])),
                "d_f1_minus_dummy": ((b["f1"] - b["dummy_mf_f1"])
                                     - (a["f1"] - a["dummy_mf_f1"])),
                "low_confidence": bool(a["low_confidence"] or b["low_confidence"]),
            })
DD = pd.DataFrame(deltas)
DD.to_csv(OUT / "target_definition_deltas.csv", index=False)
log(f"→ {OUT/'target_definition_deltas.csv'}  ({len(DD)}행)")

# --------------------------------------------------------------- 요약 JSON
hi = DF[~DF.low_confidence]
best = (hi.sort_values("roc_auc", ascending=False).head(10)
        [["endpoint", "binarization", "label_regime", "feature_set", "n", "n_groups",
          "prevalence", "roc_auc", "roc_auc_sd", "mcc", "mcc_sd", "f1",
          "dummy_mf_f1", "dummy_f1_formula_2p_over_1plusp"]]
        .to_dict(orient="records"))
SUMMARY = {
    "meta": {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_dir": str(SRC),
        "purpose": "타깃 정의(이진화 × label_regime) 격자의 순수 효과 측정. 튜닝 없음.",
        "baseline": "RandomForestClassifier(n_estimators=300, class_weight='balanced', "
                    "min_samples_leaf=2) — measure_v5_performance.py 설정 그대로",
        "cv": f"StratifiedGroupKFold(n_splits={N_SPLITS}, shuffle=True, "
              "random_state=seed) on group_key",
        "seeds": SEEDS,
        "aggregation": "seed별 OOF 예측 -> 지표 -> seed간 mean±sd(ddof=1)",
        "feature_sets": {k: len(v) for k, v in FEATURE_SETS.items()},
        "ct_cols_excluded_in_S1_minus_CT26": CT_COLS,
        "binarization_positive_sets": {
            b: {ep: sorted(s["pos"][ep]) for ep in ENDPOINTS}
            for b, s in BINARIZATIONS.items()},
        "binarization_dropped_codes": {
            b: {ep: sorted(s["drop"][ep]) for ep in ENDPOINTS}
            for b, s in BINARIZATIONS.items()},
        "regimes_src_detail": {k: (sorted(v) if v else "all")
                               for k, v in REGIMES.items()},
        "low_confidence_rule": f"n<{MIN_N} 또는 어느 클래스 절대수<{MIN_CLASS_N}",
        "caveats": [
            "유병률이 셀마다 다르므로 F1 은 셀 간 직접 비교 불가. dummy F1=2p/(1+p) 이 "
            "함께 움직인다. 셀 간 비교는 ROC-AUC 와 MCC 로만 하고 F1 은 더미와의 격차로만 논한다.",
            "행 부분집합을 취하면 n_groups 와 폴드 구성이 달라진다. 따라서 regime 간 비교는 "
            "동일 폴드에서의 비교가 아니며 그만큼 해석에 한계가 있다.",
            "sens 는 ntp_measured 출처가 0행이므로 measured regime 측정 불가.",
            "sens 는 1A/1B 세부구분만 있어 strict==inclusive — 중복 측정하지 않았다.",
            "skin 의 strict 는 명세 집합대로 3 외에 2A/2B 도 음성으로 만든다. 의도가 3만이라면 "
            "strict_skin_only3 셀을 보라.",
        ],
    },
    "grid": DF.to_dict(orient="records"),
    "skipped_cells": skipped,
    "deltas": DD.to_dict(orient="records"),
    "top10_roc_auc_high_confidence": best,
    "max_roc_auc_high_confidence": (float(hi.roc_auc.max()) if len(hi) else None),
    "any_cell_near_0_9": bool(len(hi) and hi.roc_auc.max() >= 0.88),
}
with open(OUT / "target_definition_grid.json", "w", encoding="utf-8") as f:
    json.dump(SUMMARY, f, ensure_ascii=False, indent=1)
log(f"→ {OUT/'target_definition_grid.json'}")
log(f"고신뢰 셀 최대 ROC-AUC = {SUMMARY['max_roc_auc_high_confidence']}")
log("완료")
_LOGF.close()
