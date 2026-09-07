#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5 데이터(04_모델산출물/v4_fixed/*) 성능 전면 재측정.

원칙
----
* v4 와 직접 비교하지 않는다. group_key 가 8개 제형에서 바뀌어 폴드 분할이 달라졌으므로
  v5 절대값만 보고한다. (§10-D 의 v4 수치는 폐기 대상)
* 누출 피처(exclude_by_default=True) 기본 배제. 피처는 **컬럼명으로만** 선택한다
  (pc2_* 열 순서가 실행마다 달라지므로 위치 인덱싱 금지).
* 정규 베이스라인 고정: RF(n_estimators=300, class_weight='balanced', min_samples_leaf=2).
  하이퍼파라미터 탐색·임계값 튜닝 없음.
* CV: StratifiedGroupKFold(5, shuffle=True, random_state=seed) on group_key, seed 0..4.
* GHS CT 가산 공식은 건드리지 않는다(읽기만).

산출
----
04_모델산출물/v5_perf/performance_report_v5.json
04_모델산출물/v5_perf/*.csv
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

ROOT = Path("/Users/hanseoyun/Desktop/260830")
SRC = ROOT / "04_모델산출물" / "v4_fixed"          # 읽기 전용
OUT = ROOT / "04_모델산출물" / "v5_perf"
OUT.mkdir(parents=True, exist_ok=True)

SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
ENDPOINTS = ["eye", "skin", "sens"]
T1T2 = {"T1_evidence_contradicted", "T2_endpoint_no_data"}
FLIP = {"FLIP_pos_to_neg"}

_t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time()-_t0:7.1f}s] {msg}", flush=True)


def rf(seed: int) -> RandomForestClassifier:
    """정규 베이스라인. 변경 금지."""
    return RandomForestClassifier(
        n_estimators=300, class_weight="balanced", min_samples_leaf=2,
        n_jobs=-1, random_state=seed,
    )


# ---------------------------------------------------------------- 데이터 로드
log("데이터 로드")
X = pd.read_parquet(SRC / "X_formulation.parquet")
Y = pd.read_parquet(SRC / "y_formulation.parquet")
GRP = pd.read_parquet(SRC / "groups.parquet")
MAN = pd.read_csv(SRC / "feature_role_manifest.csv")
LCONF = pd.read_csv(SRC / "label_confidence_v5.csv")

assert (X["Formulation_ID"].values == Y["Formulation_ID"].values).all(), "행 정렬 불일치"
FID = X["Formulation_ID"].astype(str).to_numpy()
GK = Y["group_key"].astype(str).to_numpy()
assert (GRP.set_index("Formulation_ID")["group_key"].reindex(Y["Formulation_ID"]).astype(str).to_numpy() == GK).all()

MANF = MAN[MAN["sheet"] == "formulation"].copy()
xcols = set(X.columns)
assert set(MANF["column"]) == xcols, "manifest 와 X 열 집합 불일치"


def cols_by(pred) -> list[str]:
    """컬럼명 리스트를 manifest 순서가 아니라 **정렬된 이름 순서**로 확정 반환.
    (pc2_* 등 열 순서 실행간 변동에 대한 방어)"""
    sel = MANF[pred(MANF)]["column"].tolist()
    return sorted(c for c in sel if c != "Formulation_ID")


CHEM = cols_by(lambda d: d["feature_role"] == "chemistry")
AMBIG = cols_by(lambda d: d["feature_role"] == "ambiguous")
NOTEXCL = cols_by(lambda d: (~d["exclude_by_default"].astype(bool)))
ALLFEAT = cols_by(lambda d: d["column"] != "Formulation_ID")
# CT(성분 GHS 분류 가산) 유래 열 — chemistry 안에 들어있어 별도 표시용
CT_COLS = sorted(c for c in CHEM if c.startswith(("f_ct_", "ct_")))

FEATURE_SETS = {
    "S1_chemistry": CHEM,
    "S2_chem_plus_ambiguous": sorted(set(CHEM) | set(AMBIG)),
    "S3_all_minus_excluded": NOTEXCL,
    # 참고용(성능 주장 불가): 누출 피처 포함 전체 = v4 스타일 오염 상한
    "R_all_contaminated": ALLFEAT,
    # 진단용: chemistry 에서 CT 가산 유래 열 제거
    "D_chemistry_minus_CT": sorted(set(CHEM) - set(CT_COLS)),
}
log("피처세트 크기: " + ", ".join(f"{k}={len(v)}" for k, v in FEATURE_SETS.items()))
log(f"S1==S3 여부: {CHEM == NOTEXCL}  (CT 유래 열 {len(CT_COLS)}개가 chemistry 에 포함)")

# 지문 (formulation 수준 농도가중 pooled)
FPZ = np.load(SRC / "fp_form_pooled.npz", allow_pickle=True)
assert (np.asarray(FPZ["fid"]).astype(str) == FID).all(), "fp_form_pooled fid 정렬 불일치"
FP_LAYOUT = str(FPZ["layout"])
MORGAN = np.asarray(FPZ["morgan"], dtype=np.float32)   # [max(2048)|wmean(2048)]
MACCS = np.asarray(FPZ["maccs"], dtype=np.float32)     # [max(167)|wmean(167)]
MORGAN_NAMES = [f"morgan_max_{i}" for i in range(MORGAN.shape[1] // 2)] + \
               [f"morgan_wmean_{i}" for i in range(MORGAN.shape[1] // 2)]
MACCS_NAMES = [f"maccs_max_{i}" for i in range(MACCS.shape[1] // 2)] + \
              [f"maccs_wmean_{i}" for i in range(MACCS.shape[1] // 2)]
log(f"지문 layout: {FP_LAYOUT}  morgan{MORGAN.shape} maccs{MACCS.shape}")

# 라벨 tier (endpoint 별)
TIER = {}
for ep in ENDPOINTS:
    sub = LCONF[LCONF["endpoint"] == ep]
    TIER[ep] = sub.set_index("Formulation_ID")["tier"].astype(str)
    log(f"tier[{ep}]: " + str(sub["tier"].value_counts().to_dict()))


def matrix(cols: list[str], extra: np.ndarray | None = None) -> np.ndarray:
    M = X[cols].to_numpy(dtype=np.float64)
    if extra is not None:
        M = np.hstack([M, extra.astype(np.float64)])
    return np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0)


def label_mask(ep: str, label_set: str) -> np.ndarray:
    """평가 대상 행 마스크 (라벨 존재 + tier 필터)."""
    ok = Y[f"y_{ep}_bin"].notna().to_numpy()
    if label_set == "L_all":
        return ok
    t = TIER[ep].reindex(FID).fillna("NONE").to_numpy()
    drop = T1T2 if label_set == "L_drop_T1T2" else (T1T2 | FLIP)
    return ok & ~np.isin(t, list(drop))


# ------------------------------------------------------------------- 지표
def metrics_from_oof(y: np.ndarray, proba: np.ndarray, pred: np.ndarray,
                     dpred: np.ndarray) -> dict:
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
        "noinfo_roc_auc": 0.5,
        "noinfo_pr_auc": p,                       # 무정보 PR-AUC 기대값 = 유병률
        "noinfo_f1_all_positive": 2 * p / (1 + p),  # 전부 양성 예측시 F1
        "noinfo_f1_random_at_p": p,               # 확률 p 로 무작위 양성 예측시 F1 기대값
        "noinfo_balanced_accuracy": 0.5,
        "noinfo_mcc": 0.0,
    }


def run_cv(Xi: np.ndarray, y: np.ndarray, g: np.ndarray, seeds=SEEDS,
           zero_var_filter: bool = False) -> dict:
    """seed 별 OOF 예측 -> seed 별 지표 -> seed 간 mean/sd.

    zero_var_filter: 학습 폴드에서 분산 0 인 열을 제거(비지도, 지문용).
    """
    per_seed, fold_auc = [], []
    for sd in seeds:
        cv = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=sd)
        proba = np.full(len(y), np.nan)
        pred = np.full(len(y), -1)
        dpred = np.full(len(y), -1)
        for tr, te in cv.split(Xi, y, groups=g):
            Xtr, Xte = Xi[tr], Xi[te]
            if zero_var_filter:
                keep = Xtr.std(axis=0) > 0
                if keep.sum() == 0:
                    keep = np.ones(Xtr.shape[1], dtype=bool)
                Xtr, Xte = Xtr[:, keep], Xte[:, keep]
            clf = rf(sd).fit(Xtr, y[tr])
            proba[te] = clf.predict_proba(Xte)[:, list(clf.classes_).index(1)]
            pred[te] = clf.predict(Xte)
            dm = DummyClassifier(strategy="most_frequent").fit(Xtr, y[tr])
            dpred[te] = dm.predict(Xte)
            try:
                fold_auc.append(roc_auc_score(y[te], proba[te]))
            except ValueError:
                pass
        assert not np.isnan(proba).any()
        per_seed.append(metrics_from_oof(y, proba, pred, dpred))
    keys = per_seed[0].keys()
    agg = {}
    for k in keys:
        v = np.array([s[k] for s in per_seed], dtype=float)
        agg[k] = float(v.mean())
        agg[k + "_sd"] = float(v.std(ddof=1)) if len(v) > 1 else 0.0
    agg["fold_roc_auc_sd"] = float(np.std(fold_auc, ddof=1)) if len(fold_auc) > 1 else 0.0
    agg["n"] = int(len(y))
    agg["n_pos"] = int(y.sum())
    agg["n_groups"] = int(len(np.unique(g)))
    agg["n_features"] = int(Xi.shape[1])
    agg["seeds"] = list(seeds)
    return agg


def oof_predictions(Xi: np.ndarray, y: np.ndarray, g: np.ndarray,
                    seeds=SEEDS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """seed 평균 OOF proba, seed 별 오분류 횟수, seed 다수결 예측라벨."""
    P = np.zeros((len(seeds), len(y)))
    W = np.zeros((len(seeds), len(y)))
    L = np.zeros((len(seeds), len(y)))
    for i, sd in enumerate(seeds):
        cv = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=sd)
        for tr, te in cv.split(Xi, y, groups=g):
            clf = rf(sd).fit(Xi[tr], y[tr])
            P[i, te] = clf.predict_proba(Xi[te])[:, list(clf.classes_).index(1)]
            lab = clf.predict(Xi[te])
            L[i, te] = lab
            W[i, te] = (lab != y[te]).astype(int)
    return P.mean(axis=0), W.sum(axis=0), (L.mean(axis=0) >= 0.5).astype(int)


REPORT: dict = {
    "meta": {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_dir": str(SRC),
        "baseline": "RandomForestClassifier(n_estimators=300, class_weight='balanced', "
                    "min_samples_leaf=2) — 정규 베이스라인 고정, 튜닝 없음",
        "cv": f"StratifiedGroupKFold(n_splits={N_SPLITS}, shuffle=True, random_state=seed) on group_key",
        "seeds": SEEDS,
        "aggregation": "seed별 OOF 예측으로 지표 산출 후 seed 간 mean±sd(ddof=1)",
        "v4_comparison": "수행하지 않음. group_key 변경(8개 제형)으로 폴드 분할이 달라 직접 비교 불가. v5 절대값만 보고.",
        "track": "Track A(직접예측)만. Track B(CT 잔차)는 범위 외.",
        "feature_set_sizes": {k: len(v) for k, v in FEATURE_SETS.items()},
        "S1_equals_S3": CHEM == NOTEXCL,
        "ct_derived_cols_in_chemistry": CT_COLS,
        "fp_layout": FP_LAYOUT,
        "n_rows_total": int(len(X)),
        "n_groups_total": int(len(np.unique(GK))),
        "versions": {"pandas": pd.__version__, "numpy": np.__version__},
    },
    "main": {}, "label_sensitivity": {}, "eye_diagnostics": {},
}

# ============================================================ 1. 메인 표
log("=== 1. 메인: 엔드포인트 × 피처세트 (L_all) ===")
rows = []
for ep in ENDPOINTS:
    m = label_mask(ep, "L_all")
    y = Y.loc[m, f"y_{ep}_bin"].astype(int).to_numpy()
    g = GK[m]
    for fs, cols in FEATURE_SETS.items():
        Xi = matrix(cols)[m]
        r = run_cv(Xi, y, g)
        REPORT["main"].setdefault(ep, {})[fs] = r
        rows.append({"endpoint": ep, "feature_set": fs, **r})
        log(f"  {ep:4s} {fs:22s} n={r['n']:4d} nf={r['n_features']:4d} "
            f"AUC={r['roc_auc']:.3f}±{r['roc_auc_sd']:.3f} "
            f"PR={r['pr_auc']:.3f} F1={r['f1']:.3f}(더미{r['dummy_mf_f1']:.3f}) "
            f"BA={r['balanced_accuracy']:.3f} MCC={r['mcc']:.3f}")
pd.DataFrame(rows).to_csv(OUT / "table_main_by_featureset.csv", index=False)

# ============================================================ 2. 라벨 민감도
log("=== 2. 라벨 신뢰도 민감도 ===")
rows = []
for ep in ENDPOINTS:
    for ls in ("L_all", "L_drop_T1T2", "L_drop_flip"):
        m = label_mask(ep, ls)
        y = Y.loc[m, f"y_{ep}_bin"].astype(int).to_numpy()
        g = GK[m]
        for fs in ("S1_chemistry", "S2_chem_plus_ambiguous", "S3_all_minus_excluded"):
            Xi = matrix(FEATURE_SETS[fs])[m]
            r = run_cv(Xi, y, g)
            r["n_dropped_vs_L_all"] = int(label_mask(ep, "L_all").sum() - m.sum())
            REPORT["label_sensitivity"].setdefault(ep, {}).setdefault(ls, {})[fs] = r
            rows.append({"endpoint": ep, "label_set": ls, "feature_set": fs, **r})
            log(f"  {ep:4s} {ls:13s} {fs:22s} n={r['n']:4d}(-{r['n_dropped_vs_L_all']}) "
                f"AUC={r['roc_auc']:.3f}±{r['roc_auc_sd']:.3f} F1={r['f1']:.3f} "
                f"BA={r['balanced_accuracy']:.3f} MCC={r['mcc']:.3f}")
pd.DataFrame(rows).to_csv(OUT / "table_label_sensitivity.csv", index=False)

# ============================================================ 3. eye 진단
log("=== 3. eye 진단 ===")
EYE_M = label_mask("eye", "L_all")
y_eye = Y.loc[EYE_M, "y_eye_bin"].astype(int).to_numpy()
g_eye = GK[EYE_M]
fid_eye = FID[EYE_M]

# 3-1 학습곡선 (S1)
log(" 3-1 학습곡선(S1_chemistry)")
Xi_eye = matrix(CHEM)[EYE_M]
lc_rows = []
for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
    aucs, f1s, bas = [], [], []
    for sd in SEEDS:
        cv = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=sd)
        rng = np.random.default_rng(1000 + sd)
        for tr, te in cv.split(Xi_eye, y_eye, groups=g_eye):
            if frac < 1.0:
                # 그룹 단위 서브샘플 (그룹 누출 방지)
                gtr = np.unique(g_eye[tr])
                keep = set(rng.choice(gtr, size=max(2, int(round(len(gtr) * frac))),
                                      replace=False).tolist())
                tr = tr[np.isin(g_eye[tr], list(keep))]
            if len(np.unique(y_eye[tr])) < 2 or len(tr) < 20:
                continue
            clf = rf(sd).fit(Xi_eye[tr], y_eye[tr])
            pr = clf.predict_proba(Xi_eye[te])[:, list(clf.classes_).index(1)]
            aucs.append(roc_auc_score(y_eye[te], pr))
            pd_ = clf.predict(Xi_eye[te])
            f1s.append(f1_score(y_eye[te], pd_, zero_division=0))
            bas.append(balanced_accuracy_score(y_eye[te], pd_))
    lc_rows.append({"train_group_frac": frac,
                    "approx_n_train": int(round(len(y_eye) * 0.8 * frac)),
                    "roc_auc": float(np.mean(aucs)), "roc_auc_sd": float(np.std(aucs, ddof=1)),
                    "f1": float(np.mean(f1s)), "balanced_accuracy": float(np.mean(bas)),
                    "n_folds": len(aucs)})
    log(f"   frac={frac:.1f} AUC={np.mean(aucs):.3f}±{np.std(aucs,ddof=1):.3f} "
        f"F1={np.mean(f1s):.3f} BA={np.mean(bas):.3f}")
REPORT["eye_diagnostics"]["learning_curve_S1"] = lc_rows
pd.DataFrame(lc_rows).to_csv(OUT / "eye_learning_curve.csv", index=False)

# 3-2 표현 비교 (eye, skin, sens 모두 — eye 만 보면 비교 기준이 없다)
log(" 3-2 지문 표현 비교")
rep_rows = []
REPRS = {
    "desc_only(S1)": (CHEM, None),
    "morgan_only": ([], MORGAN),
    "maccs_only": ([], MACCS),
    "S1+morgan": (CHEM, MORGAN),
    "S1+maccs": (CHEM, MACCS),
    "S1+morgan+maccs": (CHEM, np.hstack([MORGAN, MACCS])),
}
for ep in ENDPOINTS:
    m = label_mask(ep, "L_all")
    y = Y.loc[m, f"y_{ep}_bin"].astype(int).to_numpy()
    g = GK[m]
    for name, (cols, extra) in REPRS.items():
        if not cols and extra is None:
            continue
        if cols:
            Xi = matrix(cols, extra)[m] if extra is not None else matrix(cols)[m]
        else:
            Xi = np.nan_to_num(extra.astype(np.float64))[m]
        r = run_cv(Xi, y, g, zero_var_filter=True)
        rep_rows.append({"endpoint": ep, "representation": name, **r})
        REPORT["eye_diagnostics"].setdefault("representation", {}).setdefault(ep, {})[name] = r
        log(f"   {ep:4s} {name:18s} nf={r['n_features']:5d} "
            f"AUC={r['roc_auc']:.3f}±{r['roc_auc_sd']:.3f} F1={r['f1']:.3f} "
            f"BA={r['balanced_accuracy']:.3f} MCC={r['mcc']:.3f}")
pd.DataFrame(rep_rows).to_csv(OUT / "table_representation.csv", index=False)

# 3-3 오분류 분석 (S1, eye)
log(" 3-3 eye 오분류 분석")
proba_eye, nmis, majpred_eye = oof_predictions(Xi_eye, y_eye, g_eye)
diag_cols = [c for c in ["n_ingredients", "f_smiles_coverage", "f_pct_with_structure",
                         "ph_best", "f_pct_active", "f_pct_surf_total", "f_max_pct",
                         "f_ct_eye_coverage", "f_ct_eye_ord", "f_pct_water"]
             if c in X.columns]
DG = pd.DataFrame({
    "Formulation_ID": fid_eye,
    "y": y_eye,
    "oof_proba_mean": proba_eye,
    "n_misclassified_of_5_seeds": nmis.astype(int),
    "oof_majority_pred": majpred_eye,
    "y_eye_src": Y.loc[EYE_M, "y_eye_src"].astype(str).to_numpy(),
    "label_confidence_eye": Y.loc[EYE_M, "label_confidence_eye"].astype(str).to_numpy(),
    "label_tier_eye": Y.loc[EYE_M, "label_tier_eye"].astype(str).to_numpy(),
    "y_eye_conflict": Y.loc[EYE_M, "y_eye_conflict"].astype(str).to_numpy(),
})
for c in diag_cols:
    DG[c] = X.loc[EYE_M, c].to_numpy()
# 제형타입 원핫 -> 단일 라벨
ftype_cols = [c for c in X.columns if c.startswith("formulation_type_")]
if ftype_cols:
    sub = X.loc[EYE_M, ftype_cols].to_numpy()
    DG["formulation_type"] = [ftype_cols[i].replace("formulation_type_", "")
                              for i in sub.argmax(axis=1)]
fcode_cols = [c for c in X.columns if c.startswith("form_code_best_")]
if fcode_cols:
    sub = X.loc[EYE_M, fcode_cols].to_numpy()
    DG["form_code_best"] = [fcode_cols[i].replace("form_code_best_", "")
                            for i in sub.argmax(axis=1)]
DG["always_wrong"] = DG["n_misclassified_of_5_seeds"] == len(SEEDS)
DG.sort_values(["n_misclassified_of_5_seeds", "oof_proba_mean"],
               ascending=[False, True]).to_csv(OUT / "eye_misclassification.csv", index=False)

num_cols = [c for c in diag_cols]
cmp_tbl = []
for grp_name, mask in (("always_wrong(5/5)", DG["always_wrong"].to_numpy()),
                       ("always_right(0/5)", (DG["n_misclassified_of_5_seeds"] == 0).to_numpy()),
                       ("all", np.ones(len(DG), dtype=bool))):
    row = {"group": grp_name, "n": int(mask.sum()),
           "positive_rate": float(DG.loc[mask, "y"].mean())}
    for c in num_cols:
        v = pd.to_numeric(DG.loc[mask, c], errors="coerce")
        row[c + "_mean"] = float(v.mean())
        row[c + "_median"] = float(v.median())
    cmp_tbl.append(row)
mis_cat = {}
for c in ["y_eye_src", "label_confidence_eye", "label_tier_eye", "formulation_type",
          "form_code_best", "y_eye_conflict"]:
    if c not in DG.columns:
        continue
    tab = DG.groupby(c, observed=True).agg(
        n=("y", "size"), pos_rate=("y", "mean"),
        mean_wrong_of5=("n_misclassified_of_5_seeds", "mean"),
        always_wrong_rate=("always_wrong", "mean"))
    tab = tab[tab["n"] >= 5].sort_values("always_wrong_rate", ascending=False)
    mis_cat[c] = tab.round(4).reset_index().to_dict(orient="records")
    log(f"   [{c}]\n" + tab.round(3).to_string())
REPORT["eye_diagnostics"]["misclassification_numeric_compare"] = cmp_tbl
REPORT["eye_diagnostics"]["misclassification_by_category"] = mis_cat
pd.DataFrame(cmp_tbl).to_csv(OUT / "eye_misclassification_numeric_compare.csv", index=False)
recs = []
for c, v in mis_cat.items():
    for r in v:
        recs.append({"variable": c, "level": r[c], **{k: x for k, x in r.items() if k != c}})
pd.DataFrame(recs).to_csv(OUT / "eye_misclassification_by_category.csv", index=False)

# 3-4 라벨 출처별 층화 성능 (동일 OOF 예측을 출처별로 잘라 평가)
log(" 3-4 eye 라벨출처별 층화 성능")
strat = []
for src, sm in DG.groupby("y_eye_src", observed=True):
    idx = sm.index.to_numpy()
    yy = DG.loc[idx, "y"].to_numpy()
    pp = DG.loc[idx, "oof_proba_mean"].to_numpy()
    pred = DG.loc[idx, "oof_majority_pred"].to_numpy().astype(int)
    row = {"y_eye_src": src, "n": len(idx), "prevalence": float(yy.mean())}
    row["dummy_mf_f1_within_stratum"] = float(
        f1_score(yy, np.full(len(yy), int(yy.mean() >= 0.5)), zero_division=0))
    if len(np.unique(yy)) == 2:
        row["roc_auc"] = float(roc_auc_score(yy, pp))
        row["pr_auc"] = float(average_precision_score(yy, pp))
        row["f1_majority_vote"] = float(f1_score(yy, pred, zero_division=0))
        row["balanced_accuracy_majority_vote"] = float(balanced_accuracy_score(yy, pred))
        row["mcc_majority_vote"] = float(matthews_corrcoef(yy, pred))
    else:
        row["note"] = "단일 클래스 — AUC 산출 불가"
    strat.append(row)
    log(f"   {src:24s} n={row['n']:4d} prev={row['prevalence']:.3f} "
        f"AUC={row.get('roc_auc', float('nan')):.3f}")
REPORT["eye_diagnostics"]["by_label_source"] = strat
pd.DataFrame(strat).to_csv(OUT / "eye_by_label_source.csv", index=False)

# 3-5 참고: 출처별 학습/평가 분리는 표본 부족 → 대신 ntp_measured 단독 학습
log(" 3-5 eye: 출처 단독(ntp_measured) 학습·평가")
src_only = []
for src in DG["y_eye_src"].value_counts().index:
    sel = (DG["y_eye_src"] == src).to_numpy()
    if sel.sum() < 120:
        src_only.append({"y_eye_src": src, "n": int(sel.sum()), "note": "표본<120 — 스킵"})
        continue
    yy = y_eye[sel]
    if len(np.unique(yy)) < 2 or min(np.bincount(yy)) < N_SPLITS:
        src_only.append({"y_eye_src": src, "n": int(sel.sum()), "note": "클래스 희소 — 스킵"})
        continue
    r = run_cv(Xi_eye[sel], yy, g_eye[sel])
    src_only.append({"y_eye_src": src, **r})
    log(f"   {src:24s} n={r['n']:4d} AUC={r['roc_auc']:.3f}±{r['roc_auc_sd']:.3f} "
        f"F1={r['f1']:.3f}(더미{r['dummy_mf_f1']:.3f}) BA={r['balanced_accuracy']:.3f}")
REPORT["eye_diagnostics"]["source_only_models"] = src_only
pd.DataFrame(src_only).to_csv(OUT / "eye_source_only_models.csv", index=False)

# 3-6 진단(헤드라인 성능 아님): tier 제한 학습·평가
# 오분류가 RECOVERED_negative / FLIP / CT_CONTRADICTION 에 집중되는지를 직접 검정한다.
# 주의: 테스트셋 자체가 바뀌므로 L_all 과 같은 조건의 비교가 아니다.
log(" 3-6 eye tier 제한 진단 (테스트셋 변동 — 헤드라인 아님)")
tier_eye = TIER["eye"].reindex(FID).fillna("NONE").to_numpy()
tier_variants = {
    "all_labelled": None,
    "tier_NONE_only": lambda t: t == "NONE",
    "drop_recovered_flip_ctcontra": lambda t: ~np.isin(
        t, ["RECOVERED_negative", "FLIP_pos_to_neg", "CT_CONTRADICTION",
            "T1_evidence_contradicted", "T2_endpoint_no_data"]),
    "drop_source_conflict": lambda t: t != "SOURCE_CONFLICT",
}
tier_rows = []
for name, fn in tier_variants.items():
    sel = EYE_M.copy()
    if fn is not None:
        sel = sel & fn(tier_eye)
    yy = Y.loc[sel, "y_eye_bin"].astype(int).to_numpy()
    if len(np.unique(yy)) < 2:
        continue
    r = run_cv(matrix(CHEM)[sel], yy, GK[sel])
    tier_rows.append({"variant": name, **r})
    log(f"   {name:30s} n={r['n']:4d} prev={r['prevalence']:.3f} "
        f"AUC={r['roc_auc']:.3f}±{r['roc_auc_sd']:.3f} F1={r['f1']:.3f}"
        f"(더미{r['dummy_mf_f1']:.3f}) BA={r['balanced_accuracy']:.3f} MCC={r['mcc']:.3f}")
REPORT["eye_diagnostics"]["tier_restricted_S1"] = tier_rows
pd.DataFrame(tier_rows).to_csv(OUT / "eye_tier_restricted.csv", index=False)

# ============================================================ 저장
with open(OUT / "performance_report_v5.json", "w", encoding="utf-8") as f:
    json.dump(REPORT, f, ensure_ascii=False, indent=1)
log(f"→ {OUT}/performance_report_v5.json")
log("완료")
