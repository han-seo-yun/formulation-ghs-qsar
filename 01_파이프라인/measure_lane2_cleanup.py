#!/usr/bin/env python3
"""노선2 `L2단독` arm 피처 정리 — 산출: 04_모델산출물/v8_노선2정리/

총책임자가 지적한 설계 결함 3건은 **`L2단독` 42열을 훑어서** 나온 것이다. 인용된
중요도(감작 CT 합 눈 14.2% · 피부 18.2%)도 `v8_리포트/피처중요도_L2단독42.csv` 의
값이다. 즉 결함의 현장은 제형 221열 모델이 아니라 노선2 arm 이고, arm 정의는
`lib_model.CHEM` 이 아니라 `v8_공통/피처노선_arm.json` 이 갖고 있다. 여기를 정리하지
않으면 오독을 부르는 중요도 표가 그대로 남는다.

하는 일 세 가지
  1) 42 → 38(중복쌍 한쪽) → 37(죽은 열) → 31(감작 CT) 누적 제외의 비용을 **동결
     러너 `run_model_lane2.py` 의 프로토콜을 그대로 복제해** 잰다. 규약 §5-3 이
     공표한 42 대 35(감작 7열 단독 제외) 표와 이어 읽히도록 35열 행도 함께 낸다.
  2) 42열 행이 동결 산출 `v8_노선2/지표_노선2.csv` 의 대표 셀과 일치하는지 **지표
     전수로 assert** 한다. 이게 프로토콜 복제의 유일한 검증 경로다 — 중요도 표
     재현은 폴드·τ·보정 AUC 를 하나도 거치지 않으므로 그 역할을 못 한다.
  3) 정리 후 31열의 중요도 표를 다시 만든다. 42열 표를 먼저 재현해 **중요도 산출**
     방식이 같은지 확인한 다음 31열 표를 낸다.

`run_model_lane2.py` 와 `피처노선_arm.json` 을 건드리지 않는다 — 전자는 동결 러너고,
후자는 `05_제안/배포_노선분리/v8_공통/` 과 md5 동일한 배포 원본이다. 배포 로더
`load_common.py` 가 파일명을 하드코딩하므로 곁파일은 노선1·3 에게 보이지 않는다.
정리된 arm 은 이 스크립트의 산출 디렉터리에 **제안본**으로만 쓴다. 승인되면 소유
스크립트(`audit_feature_lane.py`)로 원본을 재생성하는 것이 정본 절차다.

## 이 스크립트는 지금 그대로는 다시 돌지 않는다 (2026-09-21)

측정이 끝난 뒤 총책임자 결정으로 `피처노선_arm.json` 의 `L2단독` 이 42 → 31 열로
재생성됐다. 그래서 `assert len(BASE) == 42` 와 단계별 열 수 assert
(`COUNTS == [42,38,37,31,35]`)가 **첫 실행에서 걸린다**. 이것은 고장이 아니라
**의도된 일회성**이다 — 이 스크립트의 존재 이유가 '42열 기준선 대 31열' 비용을 한
번 재는 것이었고, 그 산출(`v8_노선2정리/`)이 결정의 근거로 이미 인용돼 있다.
assert 를 느슨하게 풀지 않는다. 기준선이 사라진 뒤에 조용히 다른 폭으로 돌면
발표된 표와 다른 숫자가 같은 파일명으로 덮인다.

재검증이 필요하면 42열 arm 을 복원해서 돌린다. 두 경로 모두 정확하다.
  · `sorted(set(현행 L2단독) | set(lib_model.FEAT_DROP_ALL))` — 31+11=42 이고
    원본과 **순서까지 일치**함을 확인했다(`CHEM` 이 sorted 라서 성립한다)
  · `git show <2026-09-21 이전 커밋>:04_모델산출물/v8_공통/피처노선_arm.json`

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*, v8_누출감사/*,
              v8_노선2/지표_노선2.csv, v8_리포트/피처중요도_L2단독42.csv
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             matthews_corrcoef, roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold

import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"
CANON_IMP = "native_복원"
N_INNER = 3
AUC_GATE = 0.70          # run_model_lane2.py:50 과 같은 값. 보정 AUC 로 판정한다.
DOC_TIERS = ("문서독립", "문서공유_분류절", "문서공유_11절")
STRATA = DOC_TIERS + ("CT판정행_A0_base", "CT판정행_A2_aug_nc_unknown")
COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
AUDIT = L.ROOT / "04_모델산출물" / "v8_누출감사"
LANE2 = L.ROOT / "04_모델산출물" / "v8_노선2"
REPORT = L.ROOT / "04_모델산출물" / "v8_리포트"
OUT = L.ROOT / "04_모델산출물" / "v8_노선2정리"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "노선2정리.log")

# 누적 단계. 제외 열 목록의 정본은 lib_model.FEAT_DROP 이다 — 여기서 다시 적지 않는다.
STEP_GROUPS = [("② 중복쌍정리", "중복쌍"), ("③ +죽은열제외", "죽은열"),
               ("④ +감작CT제외", "감작CT")]
# 판정에 쓰는 지표. 이름은 동결 산출 `지표_노선2.csv` 와 같다.
MET = ("roc_auc", "roc_auc_보정", "pr_auc", "MCC__J_중첩CV", "BA__J_중첩CV",
       "재현율__J_중첩CV", "특이도__J_중첩CV", "MCC__고정0.5", "BA__고정0.5")


def tau_youden(y, p):
    """Youden's J = max(TPR - FPR)."""
    fpr, tpr, thr = roc_curve(y, p)
    return float(thr[int(np.argmax(tpr - fpr))])


TAU_RULES = {"J_중첩CV": tau_youden, "고정0.5": None}


def assemble_pred(p, tau_fold, n):
    """폴드별 τ 를 그 폴드 검증행에만 적용(규약 §2.4)."""
    pred = np.full(n, -1, dtype=int)
    for te, tau in tau_fold:
        pred[te] = (p[te] >= tau).astype(int)
    assert (pred >= 0).all(), "예측 미채움"
    return pred


def metrics_pred(y, pred):
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
    n = len(y)
    p = np.full(n, np.nan)
    tau_per_fold = {k: [] for k in TAU_RULES}
    for tr, te in folds:
        p[te] = L.rf(seed_idx).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
        ytr, gtr = y[tr], g[tr]
        pin = np.full(len(tr), np.nan)
        inner = StratifiedGroupKFold(n_splits=N_INNER, shuffle=True,
                                     random_state=1000 + seed_idx)
        for itr, ite in inner.split(np.zeros((len(tr), 1)), ytr, gtr):
            pin[ite] = L.rf(seed_idx).fit(Xi[tr][itr], ytr[itr]) \
                                     .predict_proba(Xi[tr][ite])[:, 1]
        assert not np.isnan(pin).any(), "내부 OOF 미채움"
        for name, fn_ in TAU_RULES.items():
            tau_per_fold[name].append((te, 0.5 if fn_ is None else fn_(ytr, pin)))
    assert not np.isnan(p).any(), "외부 OOF 미채움"
    return p, tau_per_fold


def auc_보정(per):
    """일치쌍 가중 층내부 AUC(규약 §4.1-3)."""
    num = den = 0.0
    for d in per:
        w = d["n"] * d["p"] * d["n"] * (1 - d["p"])
        num += w * d["auc"]
        den += w
    return num / den if den > 0 else float("nan")


def grp(c):
    """`build_feature_design_figs.py` 의 묶음 분류와 같아야 한다."""
    if "_cat_" in c:
        return "① 규칙 판정 (원-핫)"
    if c.endswith("_ord"):
        return "② 규칙 판정 (서수)"
    if c.startswith("f_ct_"):
        return "③ 가산식 원값"
    if c in ("f_shannon", "f_simpson"):
        return "⑤ 조성 다양성"
    if c in ("ct_not_applicable", "f_has_synergist"):
        return "⑥ 이진 플래그"
    return "④ 역할별 농도"


GRP_ORDER = ["① 규칙 판정 (원-핫)", "② 규칙 판정 (서수)", "③ 가산식 원값",
             "④ 역할별 농도", "⑤ 조성 다양성", "⑥ 이진 플래그"]

log("=== 노선2 L2단독 arm 피처 정리 대조 ===")
log(f"규약: 관할 {CANON_JUR_V8} · 결측 {CANON_IMP} · CT_권고 · 공통폴드 · "
    f"학습폴드내 중첩 {N_INNER}-fold Youden-J τ(+고정0.5 대조) · 보정 AUC "
    f"게이트 {AUC_GATE} · 층 {len(STRATA)}종")

Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
ARM_SRC = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))
SUB = np.load(AUDIT / "부분집합마스크.npz")
data = L.FormulationData(log)
assert (data.FID == Z["Formulation_ID"]).all(), "행 정렬 이탈"
XM = data.matrices()
CHEM_IDX = {c: i for i, c in enumerate(data.CHEM)}
M = XM[("CT_권고", CANON_IMP)]


def mat(cols):
    miss = [c for c in cols if c not in CHEM_IDX]
    assert not miss, f"CHEM 에 없는 열 {miss[:5]}"
    return M[:, [CHEM_IDX[c] for c in cols]]


BASE = ARM_SRC["arm"]["L2단독"]
assert len(BASE) == 42, f"L2단독 {len(BASE)} != 42 — arm 정의가 바뀌었다"
missing = [c for c in L.FEAT_DROP_ALL if c not in BASE]
assert not missing, f"제외 대상이 L2단독 에 없다: {missing}"
log(f"L2단독 {len(BASE)}열 — lib_model.FEAT_DROP 11열이 모두 이 arm 안에 있다")

# --- 제외 전제 재검증 -------------------------------------------------------
# `L.FEAT_DROP` 을 그냥 믿지 않는다. 중복 전제가 깨진 상태에서 돌면 정보를 가진 열의
# 제외 비용을 "중복 제거라 무비용" 으로 보고하게 되고, 그게 그대로 결정 근거가 된다.
PAIRS = [("ct_eye_ord", "f_ct_eye_ord"), ("ct_skin_ord", "f_ct_skin_ord"),
         ("ct_sens_ord", "f_ct_sens_ord"),
         ("f_pct_active", "f_pct_active_presumed")]
for a, b in PAIRS:
    assert a in BASE and b in BASE, f"{a}/{b} 가 L2단독 에 없다"
    va, vb = M[:, CHEM_IDX[a]], M[:, CHEM_IDX[b]]
    assert np.array_equal(va, vb, equal_nan=True), f"{a} != {b} — 중복 전제 이탈"
assert {a for a, _ in PAIRS} == set(L.FEAT_DROP["중복쌍"]), \
    "제외하는 쪽이 검증한 중복쌍의 한쪽이 아니다"
log("중복 4쌍 값 동일성 재확인(결측 위치까지): " +
    " · ".join(f"{a}={b}" for a, b in PAIRS))
vd = M[:, CHEM_IDX["ct_not_applicable"]]
dist = pd.Series(vd).value_counts(dropna=False)
log(f"ct_not_applicable 값분포 {dist.to_dict()} · 최빈 {dist.max()/len(vd)*100:.2f}%")
assert dist.max() / len(vd) > 0.95, "죽은 열 전제 이탈 — 최빈 95% 미만"

SENS7 = [c for c in BASE if "sens" in c]
assert len(SENS7) == 7 and not [c for c in SENS7 if c not in L.FEAT_DROP_ALL], \
    f"감작 열 {len(SENS7)} — 7열 전부가 제외 목록에 있어야 한다"

# 누적 단계 구성. 열 **순서는 원본 arm 순서를 그대로 보존**한다(재정렬 금지 —
# RF 는 열 순서에 민감해서 같은 집합도 순서를 바꾸면 수치가 흔들린다).
STEPS, drop = [("① L2단독_원본", ())], []
for label, key in STEP_GROUPS:
    drop = drop + list(L.FEAT_DROP[key])
    STEPS.append((label, tuple(drop)))
# ⑤ 는 누적 단계가 아니라 **규약 §5-3 공표 표(42 대 35)와 이어 읽기 위한 대조 행**이다.
# `ct_sens_ord` 가 중복쌍이라 ② 에서 먼저 빠지므로, ④ 의 증분은 37 기준 6열이고
# "감작 7열 제외 비용" 은 이 35열 행에서만 42열과 직접 비교된다.
STEPS.append(("⑤ 감작7열단독제외(§5-3 대조)", tuple(SENS7)))
COUNTS = [len([c for c in BASE if c not in set(d)]) for _, d in STEPS]
for (label, d), n in zip(STEPS, COUNTS):
    log(f"  {label:22} {n:2}열 (제외 {len(d)})")
assert COUNTS == [42, 38, 37, 31, 35], f"단계별 열 수 {COUNTS} != [42,38,37,31,35]"
for label, d in STEPS:
    kept = [c for c in BASE if c not in set(d)]
    assert kept == sorted(kept), f"{label}: 열 순서가 정렬 상태를 벗어났다"

# --- 1) 단계별 성능 ---------------------------------------------------------
RES = []
for ep in EPS:
    m = Z[f"mask_{ep}"]
    y = Z[f"y_{ep}"].astype(int)
    g = Z[f"group_{ep}"]
    folds = [[(np.setdiff1d(np.arange(len(y)), Z[f"te_{ep}_s{si}_f{fi}"]),
               Z[f"te_{ep}_s{si}_f{fi}"]) for fi in range(L.N_SPLITS)]
             for si in range(len(L.SEEDS))]
    for label, d in STEPS:
        cols = [c for c in BASE if c not in set(d)]
        Xi = mat(cols)[m]
        assert len(Xi) == len(y)
        per_seed, tau_all = [], {k: [] for k in TAU_RULES}
        strat_acc = {s: [] for s in STRATA}
        보정_acc = []
        for si in range(len(L.SEEDS)):
            p, tpf = oof_probs(Xi, y, g, folds[si], si)
            row = {"roc_auc": roc_auc_score(y, p),
                   "pr_auc": average_precision_score(y, p)}
            PRED = {}
            for name in TAU_RULES:
                PRED[name] = assemble_pred(p, tpf[name], len(y))
                tau_all[name].extend(t for _, t in tpf[name])
                row.update({f"{k}__{name}": v
                            for k, v in metrics_pred(y, PRED[name]).items()})
            per_seed.append(row)
            # 층별 — 같은 OOF 확률·같은 예측 벡터를 부분집합으로 자른다(재학습 없음).
            pc = PRED["J_중첩CV"]
            for s in STRATA:
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
            per_t = [d2 for t in DOC_TIERS for d2 in strat_acc[t][-1:]]
            assert len(per_t) == len(DOC_TIERS), "라벨출처 층 측정 누락"
            보정_acc.append(auc_보정([{"n": x["n"], "p": x["유병률"],
                                    "auc": x["roc_auc"]} for x in per_t]))
        rec = {"노선": "L2", "단위": "제형", "endpoint": ep, "단계": label,
               "피처셋": "L2단독", "열수": len(cols), "제외열수": len(d),
               "관할": CANON_JUR_V8, "결측처리": CANON_IMP,
               "임계값규칙": "J_중첩CV(폴드별 τ → 폴드 검증분 적용)",
               "폴드출처": "배포본(공통폴드.npz)", "n": int(len(y)),
               "양성": int(y.sum()), "유병률": round(float(y.mean()), 4)}
        for k in per_seed[0]:
            v = [x[k] for x in per_seed]
            rec[k] = round(float(np.mean(v)), 4)
            rec[k + "_sd"] = round(float(np.std(v, ddof=1)), 4)
        for name in TAU_RULES:
            # sd 는 25개 내부 선택값 전체로 낸다(규약 §2.4) — 시드 평균 5개의 sd 로
            # 내면 폴드간 변동이 지워져 실제 퍼짐을 축소 보고한다.
            rec[f"tau_{name}"] = round(float(np.mean(tau_all[name])), 4)
            rec[f"tau_{name}_sd"] = round(float(np.std(tau_all[name], ddof=1)), 4)
            rec[f"tau_{name}_n"] = len(tau_all[name])
            rec[f"tau_{name}_최소"] = round(float(np.min(tau_all[name])), 4)
            rec[f"tau_{name}_최대"] = round(float(np.max(tau_all[name])), 4)
        rec["roc_auc_보정"] = round(float(np.mean(보정_acc)), 4)
        rec["roc_auc_보정_sd"] = round(float(np.std(보정_acc, ddof=1)), 4)
        rec["누출기여_AUC"] = round(rec["roc_auc"] - rec["roc_auc_보정"], 4)
        rec["누출기여_초과분비율"] = round(
            1 - (rec["roc_auc_보정"] - 0.5) / (rec["roc_auc"] - 0.5), 4)
        rec["AUC게이트_0.70"] = int(rec["roc_auc_보정"] >= AUC_GATE)
        rec["AUC게이트_기준"] = "보정"
        for s, lst in strat_acc.items():
            if not lst:
                continue
            rec[f"층_{s}_n"] = lst[0]["n"]
            for k in ("roc_auc", "MCC", "BA", "재현율", "특이도"):
                rec[f"층_{s}_{k}"] = round(float(np.mean([x[k] for x in lst])), 4)
            rec[f"층_{s}_유병률"] = round(float(lst[0]["유병률"]), 4)
        RES.append(rec)
        log(f"  {ep:4} {label:22} {len(cols):2}열 "
            f"AUC={rec['roc_auc']:.4f}±{rec['roc_auc_sd']:.4f} "
            f"보정={rec['roc_auc_보정']:.4f}±{rec['roc_auc_보정_sd']:.4f}"
            f"[게이트 {'통과' if rec['AUC게이트_0.70'] else '미달'}] "
            f"MCC={rec['MCC__J_중첩CV']:.4f} BA={rec['BA__J_중첩CV']:.4f} "
            f"τ={rec['tau_J_중첩CV']:.3f} | 0.5대조 MCC={rec['MCC__고정0.5']:.4f}")
        for s in DOC_TIERS:
            log(f"       └ 층 {s:14} n={rec[f'층_{s}_n']:4} "
                f"p={rec[f'층_{s}_유병률']:.3f} AUC={rec[f'층_{s}_roc_auc']:.4f} "
                f"MCC={rec[f'층_{s}_MCC']:.4f} BA={rec[f'층_{s}_BA']:.4f}")

R = pd.DataFrame(RES)
R.to_csv(OUT / "노선2정리_대조.csv", index=False, encoding="utf-8-sig")

# --- 프로토콜 복제 검증: 42열 행이 동결 산출과 같아야 한다 --------------------
# 중요도 표 재현은 폴드·τ·보정 AUC·부분집합마스크를 하나도 거치지 않으므로 이 검증을
# 대신할 수 없다. 여기서 실패하면 38/37/31/35 비용 전체가 다른 기준선 기준이 된다.
OLD = pd.read_csv(LANE2 / "지표_노선2.csv", float_precision="round_trip")
CHK = ["roc_auc", "roc_auc_보정", "pr_auc", "누출기여_AUC", "MCC__J_중첩CV",
       "BA__J_중첩CV", "재현율__J_중첩CV", "특이도__J_중첩CV", "정밀도__J_중첩CV",
       "MCC__고정0.5", "BA__고정0.5", "재현율__고정0.5", "특이도__고정0.5",
       "tau_J_중첩CV", "tau_J_중첩CV_sd", "AUC게이트_0.70"] + \
      [f"층_{s}_{k}" for s in STRATA
       for k in ("n", "유병률", "roc_auc", "MCC", "BA", "재현율", "특이도")]
n_chk = 0
for ep in EPS:
    o = OLD[(OLD["피처셋"] == "L2단독") & (OLD["관할"] == CANON_JUR_V8) &
            (OLD["결측처리"] == CANON_IMP) & (OLD["endpoint"] == ep)]
    assert len(o) == 1, f"{ep}: 동결 산출 대표 셀 {len(o)}행"
    o = o.iloc[0]
    new = next(r for r in RES if r["endpoint"] == ep and r["단계"] == "① L2단독_원본")
    assert int(o["열수"]) == new["열수"] == 42
    for k in CHK:
        assert k in new, f"{k} 를 계산하지 않았다 — 동결 산출에는 있다"
        d = abs(float(new[k]) - float(o[k]))
        assert d < 5e-5, f"{ep}/{k}: {new[k]} != {o[k]} — 노선2 규약 복제 실패"
        n_chk += 1
log(f"프로토콜 복제 검증 통과: 42열 행이 지표_노선2.csv 대표 셀과 "
    f"{n_chk}개 지표 전부 일치 (허용오차 5e-5)")

# --- 판정: 단계별 증분 + 최종 -----------------------------------------------
# 잡음 범위 기준은 `measure_sens_drop_lane2.py` 와 같다(시드 SD 2배). 제형측
# `measure_feature_cleanup.py` 는 `sd_a + sd_b` 를 쓰므로 두 표의 '잡음 범위' 건수를
# 같은 문장에서 비교하면 안 된다. 어느 쪽도 유의성 검정이 아니다.
def 판정(a, b, k):
    d = b[k] - a[k]
    noise = 2 * max(a.get(k + "_sd", 0) or 0, b.get(k + "_sd", 0) or 0)
    return d, noise, ("유의한 손실" if d < -noise else
                      "유의한 개선" if d > noise else "잡음 범위")


CUM = [s for s in STEPS if not s[0].startswith("⑤")]
JUD = []
log("--- 단계별 증분 (직전 단계 대비) ---")
for ep in EPS:
    S = {r["단계"]: r for r in RES if r["endpoint"] == ep}
    for i in range(1, len(CUM)):
        a, b = S[CUM[i - 1][0]], S[CUM[i][0]]
        for k in MET:
            d, noise, v = 판정(a, b, k)
            JUD.append({"endpoint": ep, "구간": f"{a['열수']}→{b['열수']}열",
                        "단계": b["단계"], "지표": k, "직전": a[k], "현재": b[k],
                        "차이": round(d, 4), "잡음범위_시드SD2배": round(noise, 4),
                        "판정": v})
            if v != "잡음 범위":
                log(f"  ※ {ep:4} {b['단계']:22} {k:14} {a[k]:.4f} → {b[k]:.4f} "
                    f"({d:+.4f}, ±{noise:.4f}) → {v}")
J = pd.DataFrame(JUD)
J.to_csv(OUT / "노선2정리_판정.csv", index=False, encoding="utf-8-sig")
n_out = int((J["판정"] != "잡음 범위").sum())
log(f"판정 {len(J)}건 중 잡음 범위 {len(J)-n_out}건 · 범위 밖 {n_out}건")

FIN = []
for 제목, 단계 in (("최종 (42열 → 31열 전부정리)", "④ +감작CT제외"),
                ("§5-3 대조 (42열 → 35열, 감작 7열만 제외)", "⑤ 감작7열단독제외(§5-3 대조)")):
    log(f"--- {제목} ---")
    for ep in EPS:
        S = {r["단계"]: r for r in RES if r["endpoint"] == ep}
        a, z = S["① L2단독_원본"], S[단계]
        for k in MET + tuple(f"층_{s}_{x}" for s in DOC_TIERS
                             for x in ("roc_auc", "MCC", "BA")):
            d, noise, v = 판정(a, z, k)
            FIN.append({"대조": 제목, "endpoint": ep, "지표": k,
                        "원본열수": a["열수"], "정리열수": z["열수"],
                        "원본": a[k], "정리": z[k], "차이": round(d, 4),
                        "잡음범위_시드SD2배": round(noise, 4), "판정": v})
            if k in MET:
                log(f"  {ep:4} {k:14} {a[k]:.4f} → {z[k]:.4f} "
                    f"({d:+.4f}, ±{noise:.4f}) → {v}")
        log(f"  {ep:4} 게이트({AUC_GATE}) 보정 AUC {a['roc_auc_보정']:.4f}"
            f"[{'통과' if a['AUC게이트_0.70'] else '미달'}] → {z['roc_auc_보정']:.4f}"
            f"[{'통과' if z['AUC게이트_0.70'] else '미달'}]")
pd.DataFrame(FIN).to_csv(OUT / "노선2정리_최종대조.csv", index=False,
                         encoding="utf-8-sig")

# --- 2) 중요도 표 재작성 -----------------------------------------------------
# 방식은 `build_feature_design_figs.py` 와 동일하다: 전체 학습행에 RF 를 시드 5개로
# 적합해 feature_importances_ 평균. 교차검증 채점과 무관한 **설계 검토용 순위**다.
log("--- 중요도 (전체 학습행 · 시드 5개 평균) ---")
CLEAN = [c for c in BASE if c not in set(L.FEAT_DROP_ALL)]
IMP = {}
for tag, cols in (("42", BASE), ("31", CLEAN)):
    A = mat(cols)
    IMP[tag] = pd.DataFrame(
        {ep: np.mean([L.rf(si).fit(A[Z[f"mask_{ep}"]], Z[f"y_{ep}"].astype(int))
                      .feature_importances_ for si in range(len(L.SEEDS))], axis=0)
         for ep in EPS}, index=cols)

old_imp = REPORT / "피처중요도_L2단독42.csv"
imp_chk = "미실행 — 기존 표 없음"
if old_imp.exists():
    O = pd.read_csv(old_imp, index_col=0, float_precision="round_trip")
    A42 = IMP["42"].reindex(O.index)
    assert not A42.isna().any().any(), \
        f"기존 표에만 있는 열 {sorted(set(O.index) - set(IMP['42'].index))}"
    mx = float(np.abs(A42[list(EPS)].values - O[list(EPS)].values).max())
    imp_chk = f"기존 v8_리포트/피처중요도_L2단독42.csv 와 최대 차이 {mx:.2e} (< 1e-9)"
    log(f"중요도 산출 재현 확인: {imp_chk}")
    assert mx < 1e-9, f"42열 중요도 재현 실패 (최대 차이 {mx:.2e})"
else:
    log(f"※ {old_imp.name} 없음 — 중요도 재현 확인 생략")

SUMMARY, SENS_SHARE = {}, {}
for tag in ("42", "31"):
    T = IMP[tag]
    g = pd.Series({c: grp(c) for c in T.index})
    B = (T.assign(묶음=g).groupby("묶음").sum() * 100).round(2)
    B["열수"] = g.value_counts().reindex(B.index).astype(int)
    B = B.reindex([x for x in GRP_ORDER if x in B.index])
    SUMMARY[tag] = B
    ordered = [c for x in GRP_ORDER for c in T.index if g[c] == x]
    T.reindex(ordered).assign(묶음=[g[c] for c in ordered]) \
     .to_csv(OUT / f"피처중요도_L2단독{tag}{'_재현' if tag == '42' else ''}.csv",
             encoding="utf-8-sig")
    sc = [c for c in T.index if "sens" in c]
    SENS_SHARE[tag] = {ep: round(float(T.loc[sc, ep].sum()) * 100, 2) if sc else 0.0
                       for ep in EPS}
    log(f"[{tag}열] 감작 열 {len(sc)}개 중요도 합 — "
        + " · ".join(f"{ep} {SENS_SHARE[tag][ep]:.1f}%" for ep in EPS))
    log(f"[{tag}열] 묶음별 합(%)\n{B.to_string()}")

# 31열 상위 10 — 감작 CT 가 떠받치던 몫이 어디로 갔는지 보여 준다.
for ep in EPS:
    top = IMP["31"][ep].sort_values(ascending=False).head(10)
    log(f"[31열] {ep} 상위 10: " +
        ", ".join(f"{c} {v*100:.1f}%" for c, v in top.items()))

# 묶음 이동 표. 문서 표 4·5 를 대체하려면 열 수와 열당 % 가 함께 있어야 한다 —
# 없으면 보고서 작성자가 손으로 재집계하고, 그 손집계가 이미 한 번 갈렸다.
SH = []
for x in GRP_ORDER:
    r = {"묶음": x}
    for tag in ("42", "31"):
        has = x in SUMMARY[tag].index
        n = int(SUMMARY[tag].loc[x, "열수"]) if has else 0
        r[f"열수_{tag}"] = n
        for ep, ko in (("eye", "눈"), ("skin", "피부")):
            v = float(SUMMARY[tag].loc[x, ep]) if has else 0.0
            r[f"{ko}_{tag}"] = round(v, 2)
            r[f"{ko}_열당_{tag}"] = round(v / n, 2) if n else 0.0
    SH.append(r)
SHIFT = pd.DataFrame(SH)
SHIFT.to_csv(OUT / "중요도_묶음이동.csv", index=False, encoding="utf-8-sig")
log(f"묶음 이동\n{SHIFT.to_string(index=False)}")

# --- 3) 정리된 arm 정의 — 제안본만 (배포 원본을 덮지 않는다) -------------------
# 출력이 `v8_공통/` 이 아니라 이 스크립트의 산출 디렉터리인 이유: `v8_공통/` 은
# `05_제안/배포_노선분리/v8_공통/` 과 md5 동일한 배포 원본이고, 배포 로더
# `load_common.py` 는 파일명 `피처노선_arm.json` 을 하드코딩한다. 곁파일을 그 옆에
# 두면 로더에 보이지 않는 두 번째 정의가 배포 원본에 들어앉아 단일 진실 원천이 깨진다.
MEASURED = ("L2단독",)
ARM_CLEAN = {c: [x for x in v if x not in set(L.FEAT_DROP_ALL)]
             for c, v in ARM_SRC["arm"].items()}
with open(OUT / "피처노선_arm_정리_제안.json", "w", encoding="utf-8") as f:
    json.dump({
        "성격": "제안본. 배포 정본이 아니다 — 승인되면 소유 스크립트"
              "(audit_feature_lane.py)로 v8_공통/피처노선_arm.json 을 재생성하고 "
              "05_제안/배포_노선분리/v8_공통/ 을 재미러하는 것이 정본 절차다",
        "생성": "measure_lane2_cleanup.py — 피처노선_arm.json 에서 설계 결함 11열만 제외",
        "원본": {"경로": "04_모델산출물/v8_공통/피처노선_arm.json",
               "md5": "e6c5a1c6883df9436efc704abc412798",
               "git": "ef154e2",
               "덮어쓰지_않은_이유": "05_제안/배포_노선분리/v8_공통/ 과 md5 동일한 배포 "
                              "원본이고, load_common.py 가 파일명을 하드코딩한다"},
        "규약": "열 순서는 원본 arm 순서를 보존한다(재정렬하면 RF 수치가 흔들린다)",
        "제외열": {k: list(v) for k, v in L.FEAT_DROP.items()},
        "제외열수": len(L.FEAT_DROP_ALL),
        "측정한_arm": list(MEASURED),
        "미측정_경고": "이 제안본은 8개 arm 전부에 같은 제외를 적용하지만, 비용을 "
                  "측정한 것은 L2단독 뿐이다. 나머지는 미측정 상태이며 그대로 "
                  "채택하면 안 된다",
        "arm": {k: {"열": v, "열수_원본": len(ARM_SRC["arm"][k]), "열수": len(v),
                    "측정": k in MEASURED,
                    "변경": len(ARM_SRC["arm"][k]) != len(v)}
                for k, v in ARM_CLEAN.items()},
    }, f, ensure_ascii=False, indent=2)
log("arm 열 수 원본 → 제안: " + ", ".join(
    f"{k} {len(ARM_SRC['arm'][k])}→{len(ARM_CLEAN[k])}"
    + ("(측정)" if k in MEASURED else "(미측정)")
    for k in ARM_SRC["arm"] if len(ARM_SRC["arm"][k]) != len(ARM_CLEAN[k])))

with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "목적": "설계 결함 3건(중복쌍 4 · 죽은 열 1 · 감작 CT 6)을 노선2 L2단독 arm "
              "에서 빼는 비용을 노선2 규약으로 측정하고, 오독을 부른 중요도 표를 "
              "정리 후 기준으로 다시 만든다",
        "성격": "측정 산출물. 규약 §6 제출 산출물 목록에 없는 신설 디렉터리이며, "
              "대표 산출물 v8_노선2/ 를 대체하지 않는다",
        "규약": {"관할": CANON_JUR_V8, "결측처리": CANON_IMP, "CT": "CT_권고",
               "폴드": "v8_공통/공통폴드.npz (run_model_lane2 와 동일)",
               "임계값": f"학습폴드 안 중첩 {N_INNER}-fold Youden-J, 해당 폴드 "
                      "검증행에만 적용. 고정 0.5 대조 동반",
               "게이트": f"보정 AUC >= {AUC_GATE}",
               "층": list(STRATA), "시드": L.SEEDS, "외부폴드": L.N_SPLITS},
        "프로토콜_복제_검증": f"42열 행이 v8_노선2/지표_노선2.csv 대표 셀과 {n_chk}개 "
                      "지표(층별 포함) 전부 5e-5 이내 일치",
        "단계": {n: {"열수": len([c for c in BASE if c not in set(d)]),
                  "제외누적": list(d)} for n, d in STEPS},
        "⑤_단계_성격": "누적 단계가 아니라 규약 §5-3 이 공표한 42 대 35 표와 이어 읽기 "
                  "위한 대조 행이다. ct_sens_ord 가 중복쌍이라 ② 에서 먼저 빠지므로 "
                  "④ 의 증분은 37열 기준 6열이다",
        "판정기준": "|차이| <= 시드 SD 2배면 잡음 범위. 유의성 검정이 아니라 시드 "
                "변동 대비 크기 비교다. 제형측 measure_feature_cleanup.py 는 "
                "sd_a+sd_b 를 쓰므로 두 표의 '잡음 범위' 건수를 직접 비교하면 안 된다",
        "전제_재검증": {
            "중복4쌍": "결측 위치까지 값 동일함을 arm 행렬에서 다시 확인",
            "죽은열": f"ct_not_applicable 최빈 {dist.max()/len(vd)*100:.2f}%",
            "감작7열": SENS7,
        },
        "중요도": {
            "방식": "전체 학습행 RF 시드 5개 평균 feature_importances_ — "
                  "build_feature_design_figs.py 와 같다. 교차검증 채점과 무관한 "
                  "설계 검토용 순위",
            "재현확인": imp_chk,
            "묶음합_%": {t: SUMMARY[t].to_dict() for t in ("42", "31")},
            "감작열_중요도_%": SENS_SHARE,
            "주의": "정리 후에는 감작 열이 없으므로 0% 다. 그 몫은 사라지는 것이 아니라 "
                  "남은 묶음으로 재배분된다 — 중요도는 합이 1 로 정규화된 값이다",
        },
        "arm_제안본": "피처노선_arm_정리_제안.json — 배포 원본을 덮지 않았다. "
                 "L2단독 외 4개 arm 은 미측정 상태로 표시했다",
        "미결정": "`L2단독` 대표 피처셋을 42 → 31 로 승격할지는 회의 결정 사항이다. "
                "이 스크립트는 근거만 만든다. 승인 시 선행 작업: "
                "build_feature_design_figs.py 의 중복쌍 assert·출력 파일명 수정 → "
                "audit_feature_lane.py 로 arm 원본 재생성 → 배포본 재미러 → "
                "동결 run_model_lane2.py 재실행",
        "행": RES,
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT.relative_to(L.ROOT)}/ 노선2정리_대조.csv · 노선2정리_판정.csv · "
    f"노선2정리_최종대조.csv · 피처중요도_L2단독{{42_재현,31}}.csv · "
    f"중요도_묶음이동.csv · 피처노선_arm_정리_제안.json · 요약.json")
