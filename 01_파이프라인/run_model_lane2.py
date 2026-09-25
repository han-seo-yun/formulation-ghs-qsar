#!/usr/bin/env python3
"""L2 노선 제형 모델 — eye · skin. 산출: 04_모델산출물/v8_노선2/

연구 질문: **GHS 혼합물 분류 규칙과 성분 위험정보로 실제 완제품 GHS 분류를 얼마나
재현할 수 있는가.** 1행 = 1제형, 라벨은 완제품 GHS(L1 정본 → L2 관할 투영).

v7 과 달라지는 것 4가지 (규약 05_제안/노선분리_비교규약.md)
  (1) 대표 관할 `K_REACH` → **`EU_CLP`**. 근거는 성능이 아니라 혼합물 가산식의
      출력 어휘 정합성이다 — 가산식은 눈에서 {1, 2A, NC} 만 출력하고 2B 를 낼 수
      없다. 2B 를 분류로 보는 기준에서는 눈 마스크 1044 행의 31.3%(327건)가 '규칙이
      원리적으로 예측할 수 없는 양성'이 되어 연구 질문 자체가 성립하지 않는다.
  (2) 임계값 0.5 고정 → **중첩 CV 내부 폴드에서 규칙으로 결정**. 0.5 는 확률이
      보정돼 있어야 의미가 있는데 class_weight="balanced" 가 그것을 깨뜨린다.
      주 작동점은 Youden's J(=BA) 최대화. 0.5 는 대조용으로만 함께 보고한다.
  (3) 피처를 **정보원(노선)별로 분리**. L2단독(42, 조성표·성분 위험정보만으로 계산
      가능한 열) 이 대표이고 L2+결합 · L2+L1 · L2+L3 · 전체는 점증 ablation 부가
      행이다. 성능이 좋다고 다른 노선 피처를 대표로 끌어오지 않는다 — 3인 비교
      설계가 붕괴한다. `L2+결합`(70) 은 계면활성제 전하별 농도·농도가중 디스크립터
      처럼 구조 정보가 함께 필요한 28열을 더한 arm 이다. 대표로 쓰지 않는 이유는
      성능이 아니라, 그 열들이 SMILES 없이 계산되지 않아 L2 가설을 반증 불가로
      만들기 때문이다(`audit_feature_lane.py` 의 L2L3결합 판정).
  (4) **층별·규칙판정행별 분리 보고 필수**. 누출감사 A-2 에서 라벨 출처 층 간
      유병률이 0.19 vs 0.76 로 갈리고 결측 패턴이 그 층의 대리변수임을 확인했다.
      전체 수치만 보면 라벨 출처 판별에 기댄 성능을 실력으로 오독한다.

폴드는 생성하지 않고 v8_공통/공통폴드.npz 를 읽는다 — 3노선이 같은 인덱스를 써야
비교가 성립한다. 부가 관할(K_REACH·UN_GHS)만 예외적으로 lib_model.make_folds 로
생성한다(관할마다 행 마스크가 달라 폴드를 공유할 수 없다. 노선 내부 대조용).

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*, v8_누출감사/*
"""
from __future__ import annotations

import json

import numpy as np
import openpyxl
import pandas as pd
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             matthews_corrcoef, roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold

import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"
CANON_ARM = "L2단독"
CANON_IMP = "native_복원"
N_INNER = 3                      # 내부 폴드 수. 임계값 선택 전용
AUC_GATE = 0.70                  # 1차 성능 게이트(규약 §2.4). 미달을 숨기지 않는다
COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
AUDIT = L.ROOT / "04_모델산출물" / "v8_누출감사"
OUT = L.ROOT / "04_모델산출물" / "v8_노선2"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")

# ------------------------------------------------------------------ 임계값 규칙
# 내부 폴드 OOF 확률에서만 고른다. 외부 OOF 에서 고르면 낙관 편향이고 그것이
# 임계값 선택 누출이다.


def tau_youden(y, p):
    """Youden's J = max(TPR - FPR). BA 최대화와 동일한 작동점."""
    fpr, tpr, thr = roc_curve(y, p)
    return float(thr[int(np.argmax(tpr - fpr))])


TAU_RULES = {
    "J_중첩CV": tau_youden,
    "고정0.5": None,             # 대조용. 규약이 대표로 쓰지 말라고 정한 값
}


def assemble_pred(p, tau_fold, n):
    """폴드별 τ 를 그 폴드의 검증행에만 적용해 예측 벡터를 조립한다.

    폴드 평균 τ 를 전 행에 적용하면 안 된다. 폴드는 행을 분할하므로 폴드 k 의
    검증행 i 는 나머지 4 폴드의 **학습분**에 들어간다. 즉 y_i 가 τ₁…τ₅ 중 4 개의
    선택에 참여했고, 평균 τ 를 i 에 적용하면 그 4 개를 통해 y_i 가 자기 예측에
    되먹임된다. 규약 §2.4 는 폴드별 τ 를 그 폴드 검증분에 적용해 혼동행렬을
    집계하라고 정하고 있다. 실측 편향은 MCC eye +0.005 / skin +0.016 로 전
    지표가 한 방향(낙관)이었다.
    """
    pred = np.full(n, -1, dtype=int)
    for te, tau in tau_fold:
        pred[te] = (p[te] >= tau).astype(int)
    assert (pred >= 0).all(), "예측 미채움 — 폴드가 전 행을 덮지 않았다"
    return pred


def metrics_pred(y, pred):
    """작동점 조건부 지표. 임계값 불변 지표는 따로 계산한다."""
    if len(set(y)) < 2:
        return None
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    rec = tp / (tp + fn) if tp + fn else np.nan
    spec = tn / (tn + fp) if tn + fp else np.nan
    return {"재현율": rec, "특이도": spec,
            "정밀도": tp / (tp + fp) if tp + fp else np.nan,
            "MCC": matthews_corrcoef(y, pred), "BA": (rec + spec) / 2,
            "정확도": (tp + tn) / len(y),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def oof_probs(Xi, y, g, folds, seed_idx):
    """외부 폴드 OOF 확률 + 폴드별 내부 선택 임계값.

    임계값은 각 외부 폴드의 **학습분 내부**에서 3-fold OOF 를 만들어 고른다.
    외부 검증분은 임계값 선택에 관여하지 않는다.
    """
    n = len(y)
    p = np.full(n, np.nan)
    tau_per_fold = {k: [] for k in TAU_RULES}   # name → [(te, tau), ...]
    for tr, te in folds:
        est = L.rf(seed_idx)
        p[te] = est.fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
        # 내부 폴드 — 학습분만 사용
        ytr, gtr = y[tr], g[tr]
        pin = np.full(len(tr), np.nan)
        inner = StratifiedGroupKFold(n_splits=N_INNER, shuffle=True,
                                     random_state=1000 + seed_idx)
        for itr, ite in inner.split(np.zeros((len(tr), 1)), ytr, gtr):
            pin[ite] = L.rf(seed_idx).fit(Xi[tr][itr], ytr[itr]) \
                                     .predict_proba(Xi[tr][ite])[:, 1]
        assert not np.isnan(pin).any(), "내부 OOF 미채움"
        for name, fn_ in TAU_RULES.items():
            tau_per_fold[name].append(
                (te, 0.5 if fn_ is None else fn_(ytr, pin)))
    assert not np.isnan(p).any(), "외부 OOF 미채움 — 폴드 구성 결함"
    return p, tau_per_fold


# ------------------------------------------------------------------ 입력
log("=== L2 노선 제형 모델 (eye · skin) ===")
log(f"대표: 관할 {CANON_JUR_V8} · 피처 {CANON_ARM} · 결측 {CANON_IMP} · 임계값 J_중첩CV")

# 농도 오버레이 (v8 실험 전용, v7/v6 재현에는 영향 없음)
OVERLAY = L.ROOT / "04_모델산출물" / "v8_농도채움" / "ING_농도오버레이.csv"
overlay_path = str(OVERLAY) if OVERLAY.exists() else None
if overlay_path:
    log(f"농도 오버레이 적용: {OVERLAY}")

Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
ARM = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))["arm"]
SUB = np.load(AUDIT / "부분집합마스크.npz")
data = L.FormulationData(log, overlay_path=overlay_path)
assert (data.FID == Z["Formulation_ID"]).all(), "행 정렬 이탈"
XM = data.matrices()             # (CT arm, 결측규약) → 전체 CHEM 행렬
CHEM_IDX = {c: i for i, c in enumerate(data.CHEM)}
LAYER = data.layers(EPS)


def arm_matrix(arm: str, imp: str) -> np.ndarray:
    """arm 의 열만 뽑은 행렬. CT 열은 대표 arm(CT_권고)을 쓴다."""
    cols = ARM[arm]
    missing = [c for c in cols if c not in CHEM_IDX]
    assert not missing, f"{arm}: CHEM 에 없는 열 {missing[:5]}"
    return XM[("CT_권고", imp)][:, [CHEM_IDX[c] for c in cols]]


# 측정 셀. 대표는 EU_CLP × L2단독 × native_복원 하나다.
CELLS = []
for ep in EPS:
    for arm in ("L2단독", "L2+결합", "L2+L1", "L2+L3", "전체_노선혼합"):
        CELLS.append((ep, CANON_JUR_V8, arm, CANON_IMP))
    CELLS.append((ep, CANON_JUR_V8, "L2단독", "nan0"))          # v6 규약 대조
    for jn in ("K_REACH", "UN_GHS"):                            # 관할 대조
        CELLS.append((ep, jn, "L2단독", CANON_IMP))
log(f"측정 셀 {len(CELLS)}개 · 셀당 {len(L.SEEDS)}시드 × "
    f"{L.N_SPLITS}폴드 × (외부1 + 내부{N_INNER}) 적합")

# 층별·규칙판정행별 분리 보고 대상
DOC_TIERS = ("문서독립", "문서공유_분류절", "문서공유_11절")   # 라벨 출처 층. 서로 배타
STRATA = DOC_TIERS + ("CT판정행_A0_base", "CT판정행_A2_aug_nc_unknown")


def auc_보정(per):
    """일치쌍 가중 층내부 AUC — 누출보정 대표값.

    pooled AUC 는 층 **간** 쌍(서로 다른 라벨 출처 층에 속한 양·음성 쌍)도 세는데,
    층별 유병률이 eye 0.191 vs 0.755 로 갈리므로 그 쌍의 상당수는 '어느 문서에서
    라벨을 읽었는가'만 맞혀도 정순위가 된다. 그 몫은 화학이 아니다.

    층 내부 쌍만 남기고 각 층을 일치쌍수(n_pos x n_neg)로 가중해 평균한다 —
    AUC 를 층별로 분해할 때 올바른 가중이다. 층은 배타이므로 이중계수가 없다.
    """
    num = den = 0.0
    for d in per:
        w = d["n"] * d["유병률"] * d["n"] * (1 - d["유병률"])   # n_pos x n_neg
        num += w * d["roc_auc"]
        den += w
    return (num / den) if den > 0 else float("nan")

RES = []
for ep, jn, arm, imp in CELLS:
    if jn == CANON_JUR_V8:
        m = Z[f"mask_{ep}"]
        y = Z[f"y_{ep}"].astype(int)
        g = Z[f"group_{ep}"]
        folds = [[(np.setdiff1d(np.arange(len(y)), Z[f"te_{ep}_s{si}_f{fi}"]),
                   Z[f"te_{ep}_s{si}_f{fi}"]) for fi in range(L.N_SPLITS)]
                 for si in range(len(L.SEEDS))]
        fold_src = "배포본(공통폴드.npz)"
        같은마스크 = 같은라벨 = True
    else:
        # 부가 관할. 마스크와 라벨을 실제로 대조해 폴드를 재사용할 수 있는지 판정한다.
        # 앞선 판(版)은 "관할마다 마스크가 달라 공유 불가" 라고 적었는데 사실이 아니었다
        # — eye 는 세 관할 모두 마스크가 동일하고(2B 스위치는 라벨만 바꾼다),
        # skin/K_REACH 는 마스크와 라벨이 대표 관할과 완전히 동일하다.
        # 실제 제약은 StratifiedGroupKFold 가 y 로 층화하므로 **라벨이 다르면**
        # 같은 폴드를 쓸 수 없다는 것이다. 마스크가 같으면 층별 측정은 가능하다.
        b, m = LAYER[(jn, ep)]
        y = b[m].astype(int)
        g = data.GK[m]
        같은마스크 = bool((m == Z[f"mask_{ep}"]).all())
        같은라벨 = 같은마스크 and bool((y == Z[f"y_{ep}"].astype(int)).all())
        if 같은라벨:
            folds = [[(np.setdiff1d(np.arange(len(y)), Z[f"te_{ep}_s{si}_f{fi}"]),
                       Z[f"te_{ep}_s{si}_f{fi}"]) for fi in range(L.N_SPLITS)]
                     for si in range(len(L.SEEDS))]
            fold_src = ("배포본(공통폴드.npz) — 투영표가 같아 마스크·라벨이 대표 관할과 "
                        "동일하다. 이 행은 대표 행과 같은 측정이며 독립 근거가 아니다")
        else:
            folds = L.make_folds(y, g)
            fold_src = ("자체생성 — 라벨 벡터가 달라 층화가 달라지므로 폴드를 공유할 수 "
                        "없다" + ("" if 같은마스크 else ". 행 마스크도 다르다"))
        if not 같은마스크:
            fold_src += ". 행 기준이 달라 층별 분리 측정은 하지 않는다"
    Xi = arm_matrix(arm, imp)[m]
    assert len(Xi) == len(y)

    per_seed, tau_all = [], {k: [] for k in TAU_RULES}
    strat_acc = {s: [] for s in STRATA}
    보정_acc = []
    for si in range(len(L.SEEDS)):
        p, tpf = oof_probs(Xi, y, g, folds[si], si)
        row = {"roc_auc": roc_auc_score(y, p), "pr_auc": average_precision_score(y, p)}
        PRED = {}
        for name in TAU_RULES:
            # 폴드별 τ 를 그 폴드 검증분에만 적용한다(규약 §2.4). 평균 τ 를 전 행에
            # 적용하지 않는다 — assemble_pred docstring 의 되먹임 때문이다.
            PRED[name] = assemble_pred(p, tpf[name], len(y))
            tau_all[name].extend(t for _, t in tpf[name])   # 25 개 개별값을 모은다
            mm = metrics_pred(y, PRED[name])
            row.update({f"{k}__{name}": v for k, v in mm.items()})
        per_seed.append(row)
        # 층별 — 같은 OOF 확률·같은 예측 벡터를 부분집합으로 잘라 평가한다(재학습 없음).
        # 부분집합마스크.npz 는 대표 관할의 행 기준(마스크 적용 후)으로 만들어졌으므로
        # 마스크가 동일한 관할에서만 유효하다. skin/UN_GHS(n=615)처럼 행 기준이 다르면
        # 측정하지 않고, 조용히 건너뛰지 않고 폴드출처 열에 사유를 남긴다.
        pc = PRED["J_중첩CV"]
        for s in (STRATA if 같은마스크 else ()):
            key = f"{ep}__{s}"
            if key not in SUB.files:
                continue
            k = SUB[key]
            assert len(k) == len(y), f"{key}: 마스크 길이 {len(k)} != n {len(y)}"
            if len(set(y[k])) < 2:
                continue
            sm = metrics_pred(y[k], pc[k])
            strat_acc[s].append({"n": int(k.sum()), "유병률": float(y[k].mean()),
                                 "roc_auc": roc_auc_score(y[k], p[k]),
                                 "MCC": sm["MCC"], "BA": sm["BA"],
                                 "재현율": sm["재현율"], "특이도": sm["특이도"]})
        # 누출보정 AUC — 라벨 출처 층 내부 쌍만. 이 값이 게이트 판정 기준이다.
        per_t = [d for t in DOC_TIERS for d in strat_acc[t][-1:]]
        if 같은마스크 and len(per_t) == len(DOC_TIERS):
            보정_acc.append(auc_보정(per_t))

    rec = {"대표": int(jn == CANON_JUR_V8 and arm == CANON_ARM and imp == CANON_IMP),
           "노선": "L2", "단위": "제형", "endpoint": ep, "관할": jn,
           "피처셋": arm, "열수": len(ARM[arm]), "결측처리": imp,
           "임계값규칙": "J_중첩CV(폴드별 τ → 폴드 검증분 적용)",
           "폴드출처": fold_src, "n": int(len(y)), "양성": int(y.sum()),
           "유병률": round(float(y.mean()), 4)}
    for k in per_seed[0]:
        v = [d[k] for d in per_seed]
        rec[k] = round(float(np.mean(v)), 4)
        rec[k + "_sd"] = round(float(np.std(v, ddof=1)), 4)
    for name in TAU_RULES:
        # sd 는 25 개 내부 선택값 전체에 대해 낸다(규약 §2.4). 시드 평균 5 개의 sd 로
        # 내면 폴드간 변동이 지워져 실제 퍼짐을 한 자릿수 축소 보고하게 된다.
        rec[f"tau_{name}"] = round(float(np.mean(tau_all[name])), 4)
        rec[f"tau_{name}_sd"] = round(float(np.std(tau_all[name], ddof=1)), 4)
        rec[f"tau_{name}_n"] = len(tau_all[name])
        rec[f"tau_{name}_최소"] = round(float(np.min(tau_all[name])), 4)
        rec[f"tau_{name}_최대"] = round(float(np.max(tau_all[name])), 4)
    # 게이트는 **누출보정 AUC** 로 판정한다. pooled 값은 라벨 출처 층 판별분을 포함해
    # 부풀려져 있어 이 노선의 화학적 신호를 대표하지 않는다(누출감사 A-2).
    if 보정_acc:
        rec["roc_auc_보정"] = round(float(np.mean(보정_acc)), 4)
        rec["roc_auc_보정_sd"] = round(float(np.std(보정_acc, ddof=1)), 4)
        rec["누출기여_AUC"] = round(rec["roc_auc"] - rec["roc_auc_보정"], 4)
        rec["누출기여_초과분비율"] = round(
            1 - (rec["roc_auc_보정"] - 0.5) / (rec["roc_auc"] - 0.5), 4)
        rec["AUC게이트_0.70"] = int(rec["roc_auc_보정"] >= AUC_GATE)
        rec["AUC게이트_기준"] = "보정"
    else:
        rec["AUC게이트_0.70"] = int(rec["roc_auc"] >= AUC_GATE)
        rec["AUC게이트_기준"] = "pooled(층 측정 불가 — 보정값 없음)"
    RES.append(rec)
    log(f"  {ep:4} {jn:8} {arm:12} [{imp:11}] n={rec['n']:5} p={rec['유병률']:.3f} "
        f"AUC={rec['roc_auc']:.4f}±{rec['roc_auc_sd']:.4f} "
        f"PR={rec['pr_auc']:.4f} τ={rec['tau_J_중첩CV']:.3f} "
        f"MCC={rec['MCC__J_중첩CV']:.3f} BA={rec['BA__J_중첩CV']:.3f} "
        f"재현율={rec['재현율__J_중첩CV']:.3f} 특이도={rec['특이도__J_중첩CV']:.3f} "
        f"| 0.5대조 MCC={rec['MCC__고정0.5']:.3f} 재현율={rec['재현율__고정0.5']:.3f}"
        + (f"\n       └ 누출보정 AUC={rec['roc_auc_보정']:.4f} "
           f"(pooled -{rec['누출기여_AUC']:.4f}, 초과분의 "
           f"{rec['누출기여_초과분비율']:.1%}가 라벨출처 판별) "
           f"게이트({rec['AUC게이트_기준']})="
           f"{'통과' if rec['AUC게이트_0.70'] else '미달'}"
           if "roc_auc_보정" in rec else ""))

    # 층별 표는 대표 셀과 ablation 셀 전부에 대해 남긴다
    for s, lst in strat_acc.items():
        if not lst:
            continue
        RES[-1][f"층_{s}_n"] = lst[0]["n"]
        for k in ("roc_auc", "MCC", "BA", "재현율", "특이도"):
            RES[-1][f"층_{s}_{k}"] = round(float(np.mean([d[k] for d in lst])), 4)
        RES[-1][f"층_{s}_유병률"] = round(float(lst[0]["유병률"]), 4)
    if arm == CANON_ARM and imp == CANON_IMP and jn == CANON_JUR_V8:
        for s in STRATA:
            if f"층_{s}_MCC" in RES[-1]:
                log(f"      층 {s:26} n={RES[-1][f'층_{s}_n']:4} "
                    f"p={RES[-1][f'층_{s}_유병률']:.3f} "
                    f"AUC={RES[-1][f'층_{s}_roc_auc']:.4f} "
                    f"MCC={RES[-1][f'층_{s}_MCC']:.3f}")

R = pd.DataFrame(RES)
front = ["대표", "노선", "단위", "endpoint", "관할", "피처셋", "열수", "결측처리",
         "임계값규칙", "n", "양성", "유병률",
         "roc_auc", "roc_auc_sd", "roc_auc_보정", "roc_auc_보정_sd",
         "누출기여_AUC", "누출기여_초과분비율", "pr_auc", "pr_auc_sd",
         "tau_J_중첩CV", "tau_J_중첩CV_sd", "MCC__J_중첩CV", "BA__J_중첩CV",
         "재현율__J_중첩CV", "특이도__J_중첩CV",
         "AUC게이트_0.70", "AUC게이트_기준"]
R = R[[c for c in front if c in R.columns]
      + [c for c in R.columns if c not in front]]
R.to_csv(OUT / "지표_노선2.csv", index=False, encoding="utf-8-sig")

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "노선2"
ws.append(list(R.columns))
for _, r in R.iterrows():
    ws.append(["" if (v is None or (isinstance(v, float) and np.isnan(v))) else v
               for v in r.tolist()])
ws.freeze_panes = "L2"
ws.auto_filter.ref = ws.dimensions
wb.save(OUT / "지표_노선2.xlsx")

CAN = R[R["대표"] == 1]
for _, r in CAN.iterrows():
    log(f"대표 ▶ L2/{r['endpoint']:4} {r['관할']} n={r['n']} p={r['유병률']} "
        f"pooled AUC={r['roc_auc']} / 누출보정 AUC={r['roc_auc_보정']} "
        f"MCC={r['MCC__J_중첩CV']} τ={r['tau_J_중첩CV']} "
        f"게이트({r['AUC게이트_기준']})={'통과' if r['AUC게이트_0.70'] else '미달'}")
log("주의 — 대표값으로 인용할 수치는 `roc_auc_보정` 이다. pooled 값은 라벨 출처 층 "
    "판별분을 포함한다(누출감사 A-2). 두 값을 함께 쓰지 않은 인용은 규약 §4.1 위반이다.")

with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "연구질문": "GHS 혼합물 분류 규칙과 성분 위험정보로 실제 완제품 GHS 분류를 "
                "얼마나 재현할 수 있는가",
        "규약": "05_제안/노선분리_비교규약.md",
        "단위": "제형(혼합물). 1행 = 1제형",
        "대표기준": {"관할": CANON_JUR_V8, "피처셋": CANON_ARM,
                 "열수": len(ARM[CANON_ARM]), "결측처리": CANON_IMP,
                 "임계값규칙": "J_중첩CV"},
        "관할선정근거": "혼합물 가산식의 출력 어휘 정합성. 가산식은 눈에서 {1,2A,NC} 만 "
                  "출력하고 2B 를 낼 수 없다. 2B 를 분류로 보는 기준(K_REACH·UN_GHS·"
                  "US_OSHA)에서는 눈 마스크 1044 행의 31.3%(327건)가 규칙이 원리적으로 "
                  "예측할 수 없는 양성이 되어 연구 질문이 성립하지 않는다. 성능으로 고른 것이 "
                  "아니다 — K_REACH·UN_GHS 결과도 부가 행으로 함께 남긴다.",
        "임계값": {
            "규칙": "각 외부 폴드의 학습분 안에서 내부 3-fold OOF 를 만들어 Youden's J "
                  "(=BA) 최대점을 고른다. 외부 검증분은 선택에 관여하지 않는다. "
                  "**고른 τ 는 그 폴드의 검증행에만 적용한다** — 폴드 평균 τ 를 전 행에 "
                  "적용하면 행 i 의 라벨이 나머지 4 폴드의 τ 선택에 참여했으므로 자기 "
                  "예측에 되먹임된다(규약 §2.4). 실측 편향 MCC eye +0.005 / skin +0.016, "
                  "8/8 지표가 낙관 방향이었다.",
            "0.5_를_쓰지_않는_이유": [
                "class_weight='balanced' 가 분할 기준과 잎 투표를 재가중하므로 모델 "
                "출력은 자연 유병률 하의 P(y=1|x) 추정치가 아니다. 0.5 에 확률적 의미가 없다.",
                "FN(유해 제품 무표시 출하)과 FP(불필요한 유해성 표시)의 비용이 다르다. "
                "이 프로젝트는 라벨 충돌 해소에 이미 규제 보수성 원칙을 쓰고 있다.",
                "세 노선 모델의 보정 상태가 달라 같은 0.5 는 서로 다른 실효 작동점이 된다.",
            ],
            "고정0.5_대조": "같은 OOF 확률에 0.5 를 적용한 값을 `__고정0.5` 접미 열로 "
                       "함께 남긴다. 대표로 쓰지 않는다.",
            "R_MIN_부작동점": "v1 보류. J 최적점과 중복되어 추가 정보가 없다(규약 §2.4).",
        },
        "성능게이트": {
            "기준": f"대표 셀 ROC-AUC >= {AUC_GATE}",
            "판정대상": "`roc_auc_보정`(일치쌍 가중 층내부 AUC). pooled 값으로 판정하지 "
                   "않는다 — 층 판별분을 포함해 이 노선의 화학적 신호를 대표하지 않는다.",
            "결과": {f"{r['endpoint']}": {
                "pooled": r["roc_auc"], "보정": r.get("roc_auc_보정"),
                "판정기준": r["AUC게이트_기준"],
                "게이트": "통과" if r["AUC게이트_0.70"] else "미달"}
                for _, r in CAN.iterrows()},
        },
        "누출보정_대표값": {
            "정의": "라벨 출처 층(문서독립·문서공유_분류절·문서공유_11절) 내부 AUC 를 "
                  "각 층의 일치쌍수(n_pos x n_neg)로 가중평균한 값.",
            "왜_이것이_대표인가": "pooled AUC 는 층 간 쌍도 센다. 층별 유병률이 eye "
                          "0.191 vs 0.755 로 갈리므로 그 쌍의 상당수는 '어느 문서에서 "
                          "라벨을 읽었는가'만 맞혀도 정순위가 된다. 그 몫은 화학이 "
                          "아니고 재현 대상도 아니다. 층은 배타 분할이라 이중계수가 없다.",
            "왜_열배제로_막을_수_없는가": "결측 패턴이 층의 대리변수다(누출감사 A-2, "
                            "L3 층간 결측률 최대차 0.32). 열을 빼도 남은 열의 관측 "
                            "가능성에 같은 정보가 남는다.",
            "해석": "pooled 와 보정값의 차(`누출기여_AUC`)가 라벨 출처 판별에 기댄 몫이다. "
                  "`누출기여_초과분비율` 은 그 몫이 0.5 초과분에서 차지하는 비율이다.",
            "한계": "층 내부에도 잔여 통로가 있을 수 있고, 이 보정은 라벨 출처 층이라는 "
                  "관측된 축 하나만 제거한다. 하한이 아니라 상한 쪽 추정이다.",
        },
        "층별_분리보고": {
            "이유": "누출감사 A-2 — 라벨 출처 층 간 유병률이 eye 0.191(문서독립) vs "
                  "0.755(문서공유_분류절)로 갈리고, 결측 패턴이 그 층의 대리변수다. "
                  "전체 수치만 보면 라벨 출처 판별에 기댄 성능을 실력으로 오독한다.",
            "방법": "같은 OOF 확률과 같은 예측 벡터를 부분집합으로 잘라 평가한다"
                  "(재학습하지 않는다). 예측은 폴드별 τ 로 조립된 것을 그대로 쓴다.",
            "층": list(STRATA),
            "층은_분할인가": "라벨 출처 3 층은 서로 배타이고 합이 전체다(eye 608+330+106="
                       "1044, skin 544+421+94=1059). CT판정행 2 층은 다른 축이며 "
                       "A0 ⊂ A2 이다 — 이 둘은 분할이 아니다.",
            "CT판정행_용도": "CT 규칙 단독 성능(누출감사 B)과 같은 n 에서 비교해 "
                       "'규칙이 설명하는 몫'과 '학습 증분'을 분리한다. 단 규칙 baseline "
                       "집계값은 성분커버리지가 낮은 행에 지배되므로 누출감사 B 의 "
                       "커버리지 구간별 행과 함께 읽는다.",
            "유의성": "층별 수치에 신뢰구간을 붙이지 않았다. 문서공유_11절은 eye n=106"
                   "(양성 21) · skin n=94(양성 14) 로 작아 층간 MCC 차이를 유의하다고 "
                   "읽을 근거가 없다.",
        },
        "폴드": {"대표관할": "v8_공통/공통폴드.npz 배포본을 읽는다(3노선 공유)",
               "부가관할": "라벨 벡터가 다르면 StratifiedGroupKFold 층화가 달라져 폴드를 "
                       "공유할 수 없어 lib_model.make_folds 로 자체 생성한다. 마스크가 "
                       "같으면(eye 전 관할, skin/K_REACH) 층별 측정은 그대로 한다. "
                       "행 마스크까지 다른 skin/UN_GHS(n=615)만 층별 측정을 생략한다.",
               "부가관할_중복": "투영표가 대표 관할과 동일한 행(skin/K_REACH)은 마스크와 "
                          "라벨이 완전히 같아 대표 행과 **같은 측정**이다. 독립 근거로 "
                          "인용하지 않는다. 폴드출처 열에 그 사실을 적어 둔다."},
        "결측처리": {"대표": CANON_IMP,
                 "복원열": list(L.ZERO_IS_MISSING),
                 "근거": "04_모델산출물/v7_결측감사/"},
        "감작": "학습 라벨로 쓰지 않는다. f_ct_sens_* 는 라벨이 아닌 혼합물 가산 "
              "디스크립터라 L2 arm 에 남는다(규약 §5-3 결정 대기)",
        "행": R.to_dict("records"),
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT.relative_to(L.ROOT)}/지표_노선2.csv / .xlsx / 요약.json")
