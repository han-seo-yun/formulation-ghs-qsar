#!/usr/bin/env python3
"""A0 칸(성분 MSDS 선언값) 결측을 규제 DB 값으로 채웠을 때 — 산출: v8_노선2/A0채움_*.csv

## 무엇을 재는가

지금까지 조사값은 `ing_ghs_indep_*`(독립조사 칸)에만 넣었다. 이 스크립트는 그 값을
**`ing_ghs_{ep}`(A0 = 성분 MSDS 선언값) 칸의 결측에도 넣었을 때** 성능이 달라지는지
잰다. 사용자가 요청한 실험이며, 아래 대가를 안고 하는 것이다.

## 대가 — 정보원 귀속이 흐려진다

L2 노선의 정의는 '성분별 MSDS + 조성·농도로 완제품 GHS 를 재현한다' 다. A0 칸에 외부
규제 DB(CLP Annex VI·ECHA C&L) 값을 넣으면 그 칸은 더 이상 MSDS 선언값이 아니다.
그래서 **기본 산출물에는 반영하지 않고 여기서 측정만 한다.** 채택 여부는 이 수치를
보고 정한다.

또 하나: A0 칸에 값이 들어가면 `build_ct` 가 그것을 `base` 로 먼저 쓰므로
(`lib_model.py:335` 부근) A2 arm 의 **NC 폐기(`lib_model.py:346-347`)를 우회한다**.
즉 A0 채움은 '규제 DB 값을 A0 로 옮기는 것' 과 'NC 를 가산식에 살리는 것' 두 변화를
동시에 일으킨다. **A0 채움분의 대부분이 `NC` 다** — 실측 eye 1108/1632(68%) ·
skin 1300/1660(78%). 그래서 `A0채움_전체` 를 `반영후_NC유지` 와 견주는 것으로는
둘을 가를 수 없다. **양쪽 다 NC 를 살린 arm** 이기 때문이다.

가르는 것은 `A0채움_유해만`(NC 를 뺀 채움)뿐이다. `A0채움_유해만 vs 반영후` 가
교란 없는 'A0 이동' 대조이고, `반영후_NC유지 vs 반영후` 가 'NC 살리기' 단독 대조다.

## 대조군

| 이름 | 내용 |
|---|---|
| `반영후` | 조사값을 `indep` 에만 넣은 현행 상태. 이 실험의 기준선 |
| `반영후_NC유지` | A0 는 그대로. A2 의 NC 폐기만 뺀다 |
| `A0채움_전체` | `indep` 값을 A0 결측에 복사 |
| `A0채움_유해만` | 그중 `NC` 가 아닌 것만 복사 — **A0 이동 효과를 홀로 재는 유일한 arm** |
| `A0채움_tier표기_annexvi` | `_tier` 열이 `annex_vi` 인 값만 복사 |

**`A0채움_tier표기_annexvi` 를 '근거가 가장 강한 것' 으로 읽지 않는다.** v6 의 기존
`indep` 칸은 **Annex VI 부재를 `NC` 로 읽었다** — 실측 교차표에서 `annex_vi` tier 에
`NC` 가 eye 892 · skin 1069 건인데 `echa_cl` tier 에는 `NC` 가 **0 건**이다. 그런데
`collect_ing_ghs.decide()` 는 정확히 그 추론을 반증까지 붙여 금지한다(Annex VI 는
부분 목록이라 부재가 무해를 뜻하지 않는다). 그래서 이 arm 채움의 eye 73% ·
skin 81% 가 그 `NC` 이고, 이 파이프라인 기준으로는 **무효인 값**이다. arm 이름을
`조화분류만` 에서 `tier표기_annexvi` 로 바꾼 이유가 이것이다 — 열 문자열이
`annex_vi` 라는 사실만 뜻하고, 조화분류로 확정된 값이라는 뜻이 아니다.

**정보원도 이 arm 들의 다수가 이번 조사가 아니다.** A0 에 들어가는 값의 약 70% 는
v6 에 이미 있던 기존 독립조사값이고 이번 오버레이 유래는 30% 뿐이다(arm 별 실측은
`A0채움_구성.csv`). 채택 판단이 정보원 귀속에 달려 있으므로 그 표를 함께 낸다.

농도 결측 행은 어느 대조군에서도 **A0 에** 채우지 않는다 — `ct_predict` 가 `NaN`
농도를 통과시켜 `add` 를 `NaN` 으로 만들고 제형 판정이 조용히 `NC` 로 내려간다
(`lib_model.py:439`, 실측 `ct_predict([('2',17.6),('2A',nan)],'eye')` → `cat='NC'`).

**단, 이 가드로 막히지 않는 경로가 있다.** `lib_model.build_ct` 의 A2 fallback
(`lib_model.py:343-350`)에는 농도 가드가 **없어서** 같은 행의 `indep` 값이 그대로
들어간다. 실측: 농도 결측 행에 `indep` 유해 구분이 있는 성분행 eye 73 · skin 45 개
때문에 **제형 eye 23 · skin 16 개의 CT 판정이 실제로 `NC` 로 내려가 있다**(구분을
지우면 그 제형들이 `NC` 를 벗어난다 — eye 1 개는 `2A`, skin 은 `2` 2 개 · `1` 1 개,
나머지는 판정 없음으로 정직하게 결측이 된다). `lib_model.py` 는 버전 고정이라 여기서
고칠 수 없다. **모든 arm 과 v6·v7 기준선에 공통으로 있는 오염**이므로 arm 간 대조는
보호되지만, 절대 수준을 인용할 때는 이 1.2% 를 함께 적는다.

버전 고정 스크립트를 수정하지 않는다. 같은 규약(공통폴드 · 폴드별 τ · 층내부 보정
AUC)을 복제해 대표 2 셀만 돌린다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*, v8_누출감사/*,
v8_성분조사/ING_조사반영_오버레이.csv
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             matthews_corrcoef, roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold

import apply_ing_ghs_survey as A
import lib_ct_nckeep as NCK
import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"
CANON_IMP = "native_복원"
N_INNER = 3
DOC_TIERS = ("문서독립", "문서공유_분류절", "문서공유_11절")
COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
AUDIT = L.ROOT / "04_모델산출물" / "v8_누출감사"
OUT = L.ROOT / "04_모델산출물" / "v8_노선2"
log = L.make_logger(OUT / "A0채움.log")
REC_ARM = {"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown",
           "sens": "A3_aug_annexvi"}


def has(s):
    return s.notna() & (~s.astype(str).str.strip().str.lower()
                        .isin(["nan", "none", ""]))


def tau_youden(y, p):
    fpr, tpr, thr = roc_curve(y, p)
    return float(thr[int(np.argmax(tpr - fpr))])


def assemble_pred(p, tau_fold, n):
    pred = np.full(n, -1, dtype=int)
    for te, tau in tau_fold:
        pred[te] = (p[te] >= tau).astype(int)
    assert (pred >= 0).all(), "예측 미채움"
    return pred


def oof_probs(Xi, y, g, folds, seed_idx):
    n = len(y)
    p = np.full(n, np.nan)
    tpf = []
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
        tpf.append((te, tau_youden(ytr, pin)))
    assert not np.isnan(p).any(), "외부 OOF 미채움"
    return p, tpf


def mcc_ba(y, pred):
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    rec = tp / (tp + fn) if tp + fn else np.nan
    spec = tn / (tn + fp) if tn + fp else np.nan
    return {"MCC": matthews_corrcoef(y, pred), "BA": (rec + spec) / 2,
            "재현율": rec, "특이도": spec}


def auc_보정(per):
    num = den = 0.0
    for d in per:
        w = d["n"] * d["p"] * d["n"] * (1 - d["p"])
        num += w * d["auc"]
        den += w
    return num / den if den > 0 else float("nan")


log("=== A0 칸 결측을 규제 DB 값으로 채운 효과 ===")
Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
ARM = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))["arm"]
SUB = np.load(AUDIT / "부분집합마스크.npz")
data = L.FormulationData(log)
assert (data.FID == Z["Formulation_ID"]).all(), "행 정렬 이탈"
CHEM_IDX = {c: i for i, c in enumerate(data.CHEM)}
COLS = [CHEM_IDX[c] for c in ARM["L2단독"]]
# 기준 열 수를 로그와 요약에 남긴다. 이미 배포된 `A0채움_*.csv` 는 42열 기준이고,
# 2026-09-21 결정으로 arm 이 31열이 됐으므로 재실행분과 섞어 읽으면 안 된다.
# 여기에 열 수 assert 를 박지는 않는다 — arm 은 `피처노선_arm.json` 이 정본이고
# 이 스크립트가 그 값을 되받아 검사하면 순환이 된다.
log(f"기준 arm: L2단독 {len(COLS)}열 (피처노선_arm.json). 42열 기준 종전 산출과 "
    f"같은 표에서 비교하지 않는다")

# 조사 반영 **전** 기준선을 먼저 뽑는다. `matrices()` 의 A0 재현·A2 커버리지 assert 는
# 조사값을 얹기 전에만 성립하므로, 이 호출이 곧 '입력이 배포본과 같다'는 검증이다
X_V0 = data.matrices()[("CT_권고", CANON_IMP)]
ING0 = data.ING.copy()
ING1 = A.apply(ING0, log)                 # 반영 상태 = 이 실험의 기준선
# 정보원 귀속용 — 어느 성분행이 이번 조사 유래인지. `A0채움_구성.csv` 에서 쓴다
OVL = pd.read_csv(A.OVERLAY, dtype={"구분": str, "tier": str})

ZI = [data.CHEM.index(c) for c in L.ZERO_IS_MISSING if c in data.CHEM]


def as_native(X):
    M = X[data.CHEM].to_numpy(dtype=np.float64)
    for j in ZI:
        assert not np.isnan(M[data.NOPCT, j]).any(), \
            f"{data.CHEM[j]}: 조성미상 행에 이미 결측 — 규약 전제 이탈"
        M[data.NOPCT, j] = np.nan
    return M


def build_ct_nc유지(I):
    """`lib_ct_nckeep` 의 공유 구현을 부른다.

    **이 함수를 여기서 다시 구현하지 않는다.** 앞판에서는 이 스크립트와
    `measure_ing_survey_gain.py` 에 각자 복사본이 있었고, 한쪽에서 `sens` 의 A3
    tier 게이트가 빠져 **같은 이름의 arm 이 두 산출물에서 다른 숫자로 발표됐다**.
    정의는 한 곳에만 둔다.
    """
    return NCK.build_ct_nc유지(data, I)


def a0_fill(I, 모드: str):
    """`indep` 값을 A0 결측에 복사한 ING 사본. 농도 있는 행만.

    `모드` = `전체` / `유해만`(NC 제외) / `tier표기_annexvi`.

    **구성표를 함께 돌려준다.** 무엇이 들어갔는지 없이는 결과를 읽을 수 없다 —
    채움의 68~78% 가 `NC` 이고 약 70% 가 이번 조사가 아닌 v6 기존값이다.
    """
    J = I.copy()
    pct_ok = has(J["ing_pct_best"])
    적용, 구성 = {}, []
    for ep in EPS:
        src = J[f"ing_ghs_indep_{ep}_cat"]
        if 모드 == "유해만":
            src = src.where(src.astype(str) != "NC")
        elif 모드 == "tier표기_annexvi":
            src = src.where(J[f"ing_ghs_indep_{ep}_tier"].astype(str)
                            == "annex_vi")
        빈칸 = ~has(J[f"ing_ghs_{ep}"]) & has(src) & pct_ok
        J.loc[빈칸, f"ing_ghs_{ep}"] = src[빈칸].astype(str)
        적용[ep] = int(빈칸.sum())
        # 정보원 귀속 — 이번 조사 오버레이 유래인지 v6 에 이미 있던 값인지
        신규 = J.index.isin(OVL.loc[OVL.endpoint == ep, "행번호"])
        nc = src.astype(str) == "NC"
        구성.append({"모드": 모드, "endpoint": ep, "채움": int(빈칸.sum()),
                     "NC": int((빈칸 & nc).sum()),
                     "유해구분": int((빈칸 & ~nc).sum()),
                     "이번조사_오버레이유래": int((빈칸 & 신규).sum()),
                     "v6_기존독립조사값": int((빈칸 & ~신규).sum()),
                     "tier_annex_vi표기":
                         int((빈칸 & (J[f"ing_ghs_indep_{ep}_tier"].astype(str)
                                     == "annex_vi")).sum())})
    return J, 적용, 구성


def measure_input(I, nc유지=False):
    data.ING = I
    try:
        ct = (build_ct_nc유지(I) if nc유지
              else data.build_ct(REC_ARM, L.EPS_ALL))
        M = as_native(data.ct_to_X(ct, L.EPS_ALL))
    finally:
        data.ING = ING0
    cov = {ep: round(float(ct[f"f_ct_{ep}_coverage"].mean()), 4) for ep in EPS}
    n판정 = {ep: int(ct[f"f_ct_{ep}_cat"].notna().sum()) for ep in EPS}
    return M, cov, n판정


VAR = [("반영후", ING1, False, {ep: 0 for ep in EPS}),
       ("반영후_NC유지", ING1, True, {ep: 0 for ep in EPS})]
구성표 = []
for 모드 in ("전체", "유해만", "tier표기_annexvi"):
    J, 적용, 구성 = a0_fill(ING1, 모드)
    VAR.append((f"A0채움_{모드}", J, False, 적용))
    구성표 += 구성
pd.DataFrame(구성표).to_csv(OUT / "A0채움_구성.csv", index=False,
                           encoding="utf-8-sig")
for r in 구성표:
    log(f"  구성 A0채움_{r['모드']:16} {r['endpoint']:4} 채움 {r['채움']:5} = "
        f"NC {r['NC']} + 유해 {r['유해구분']} · 정보원 이번조사 "
        f"{r['이번조사_오버레이유래']} / v6기존 {r['v6_기존독립조사값']}")

RESULT = []
for 이름, I, nc, 적용 in VAR:
    M, cov, n판정 = measure_input(I, nc)
    log(f"{이름}: A0 채움 {적용} · 성분커버리지 {cov} · 판정행 {n판정}")
    RESULT.append((이름, M, 적용, cov, n판정))

# 반영 전 상태도 표에 남긴다 — 조사 자체의 효과와 A0 채움의 효과를 가른다
M0, cov0, n0 = measure_input(ING0, False)
# `nan_to_num` 을 씌워 비교하면 한쪽의 `NaN` 과 다른 쪽의 `0` 이 같다고 나온다.
# 결측이 0 으로 바뀌는 것은 이 파이프라인에서 정확히 막으려는 사고다(`ZERO_IS_MISSING`)
assert np.array_equal(X_V0, M0, equal_nan=True), \
    "반영 전 재현 실패 — build_ct 경로가 matrices() 와 다르다"
RESULT.insert(0, ("반영전", X_V0, {ep: 0 for ep in EPS}, cov0, n0))
log(f"반영전: 성분커버리지 {cov0} · 판정행 {n0}")

# --- arm 항등 검사 ---------------------------------------------------------
# `A0채움_유해만` 은 `반영후` 와 **행렬까지 완전히 같아야 한다**. A2 fallback 이
# A0 결측일 때 이미 `indep` 의 비-`NC` 값을 끌어다 쓰므로(`lib_model.py:343-350`),
# 그 값을 A0 로 옮기는 것은 `ct_predict` 입력 벡터를 한 원소도 바꾸지 않는다.
# 즉 이 arm 은 'A0 이동 효과를 재는 대조' 가 아니라 **정의상 무연산**이다.
#
# 이것을 assert 로 박아 두는 이유: 항등이 나왔을 때 그것이 **의도된 항등**인지
# **조사값이 반영되지 않은 실수**인지 코드가 구별해야 한다. 사후에 사람이 추론하는
# 것을 유일한 방어로 두면 다음 수정에서 조용히 틀린다
_M = {이름: M for 이름, M, *_ in RESULT}
assert np.array_equal(_M["A0채움_유해만"], _M["반영후"], equal_nan=True), \
    "A2 하에서 비-NC 값의 A0 이동은 무연산이어야 한다 — 달라졌다면 a0_fill 이 " \
    "A2 fallback 범위 밖을 건드렸다"
for 이름 in ("반영후_NC유지", "A0채움_전체", "A0채움_tier표기_annexvi"):
    assert not np.array_equal(_M[이름], _M["반영후"], equal_nan=True), \
        f"{이름} 이 `반영후` 와 동일하다 — 이 arm 의 변화가 반영되지 않았다"
for 이름 in ("반영후", "반영후_NC유지"):
    assert not np.array_equal(_M[이름], X_V0, equal_nan=True), \
        f"{이름} 이 반영 전과 동일하다 — 조사값이 반영되지 않았다"
log("arm 항등 검사 통과 — `A0채움_유해만` 은 `반영후` 와 항등(정의상 무연산), "
    "나머지 arm 은 모두 상이")

RES = []
SEED = {}          # (ep, 대조군) → 지표별 시드별 값. 대응차 밴드에 쓴다
for ep in EPS:
    m, y = Z[f"mask_{ep}"], Z[f"y_{ep}"].astype(int)
    g = Z[f"group_{ep}"]
    folds = [[(np.setdiff1d(np.arange(len(y)), Z[f"te_{ep}_s{si}_f{fi}"]),
               Z[f"te_{ep}_s{si}_f{fi}"]) for fi in range(L.N_SPLITS)]
             for si in range(len(L.SEEDS))]
    for 이름, M, 적용, cov, n판정 in RESULT:
        Xi = M[:, COLS][m]
        assert len(Xi) == len(y)
        acc, 보정, taus = [], [], []
        for si in range(len(L.SEEDS)):
            p, tpf = oof_probs(Xi, y, g, folds[si], si)
            pred = assemble_pred(p, tpf, len(y))
            taus.extend(t for _, t in tpf)
            acc.append({"roc_auc": roc_auc_score(y, p),
                        "pr_auc": average_precision_score(y, p),
                        **mcc_ba(y, pred)})
            per = [{"n": int(SUB[f"{ep}__{t}"].sum()),
                    "p": float(y[SUB[f"{ep}__{t}"]].mean()),
                    "auc": roc_auc_score(y[SUB[f"{ep}__{t}"]],
                                         p[SUB[f"{ep}__{t}"]])}
                   for t in DOC_TIERS]
            보정.append(auc_보정(per))
        rec = {"endpoint": ep, "대조군": 이름, "관할": CANON_JUR_V8,
               "n": int(len(y)), "유병률": round(float(y.mean()), 4),
               "A0_채움_성분행": 적용[ep], "성분커버리지": cov[ep],
               "CT판정행": n판정[ep]}
        for k in acc[0]:
            rec[k] = round(float(np.mean([d[k] for d in acc])), 4)
            rec[k + "_sd"] = round(float(np.std([d[k] for d in acc], ddof=1)), 4)
        rec["roc_auc_보정"] = round(float(np.mean(보정)), 4)
        rec["roc_auc_보정_sd"] = round(float(np.std(보정, ddof=1)), 4)
        rec["tau_평균"] = round(float(np.mean(taus)), 4)
        SEED[(ep, 이름)] = {k: [d[k] for d in acc] for k in acc[0]}
        SEED[(ep, 이름)]["roc_auc_보정"] = list(보정)
        RES.append(rec)
        log(f"  {ep:4} {이름:18} 커버리지={cov[ep]:.3f} "
            f"pooled AUC={rec['roc_auc']:.4f}±{rec['roc_auc_sd']:.4f} "
            f"보정 AUC={rec['roc_auc_보정']:.4f} MCC={rec['MCC']:.4f} "
            f"BA={rec['BA']:.4f}")
pd.DataFrame(RES).to_csv(OUT / "A0채움_대조.csv", index=False,
                         encoding="utf-8-sig")

log("--- 판정 ---")
결론 = []
# A0 채움은 **반영후**와 견준다. NC 유지분은 NC 유지 기준선과 견준다
쌍 = (("반영후", "반영전"), ("반영후_NC유지", "반영후"),
     ("A0채움_전체", "반영후"), ("A0채움_유해만", "반영후"),
     ("A0채움_tier표기_annexvi", "반영후"),
     ("A0채움_전체", "반영후_NC유지"))
# **대응차(paired) 밴드를 쓴다.** 두 arm 은 **같은 5 개 시드**로 돌았고 시드는
# 폴드 배정과 RF 난수를 함께 정한다. 그래서 두 arm 의 시드별 값은 강하게 상관하고,
# 각 arm 의 주변 SD 를 밴드로 쓰면 그 공통 변동을 잡음으로 두 번 센다 — 실제보다
# 넓은 밴드가 되어 검정력이 죽는다(모든 것이 '잡음 범위' 로 나온다). 같은 시드끼리
# 뺀 차이의 SD 가 이 설계에 맞는 밴드다. 시드 5 개뿐이므로 여전히 보수적이다
for ep in EPS:
    for 이름, 기준 in 쌍:
        a = next(r for r in RES if r["endpoint"] == ep and r["대조군"] == 기준)
        b = next(r for r in RES if r["endpoint"] == ep and r["대조군"] == 이름)
        for k in ("roc_auc", "roc_auc_보정", "MCC", "BA"):
            # 모든 arm 이 SEED 에 있다 — `반영전` 도 RES 루프를 함께 돌았다.
            # 앞판의 `else` 주변SD 분기는 죽은 코드였으므로 없앤다
            ds = (np.asarray(SEED[(ep, 이름)][k])
                  - np.asarray(SEED[(ep, 기준)][k]))
            # 차이도 **미반올림 시드차의 평균**으로 낸다. 4 자리로 반올림된 평균끼리
            # 빼고 미반올림 밴드와 견주면 `차이 == 잡음범위` 인데 판정이 '밖' 으로
            # 나오는 재현 불가 행이 생긴다. 자리수도 6 자리로 늘린다
            d = float(np.mean(ds))
            noise = 2 * float(np.std(ds, ddof=1))
            결론.append({"endpoint": ep, "대조": f"{이름} vs {기준}", "지표": k,
                        "기준": a[k], "대조군": b[k], "차이": round(d, 6),
                        "잡음범위_대응차SD2배": round(noise, 6),
                        "판정": "시드 재현 범위 밖 — 개선" if d > noise else
                                "시드 재현 범위 밖 — 손실" if d < -noise else
                                "시드 재현 범위 안 — 변화 없음"})
            log(f"  {ep:4} {이름:18} vs {기준:12} {k:12} {a[k]:.4f} → {b[k]:.4f} "
                f"({d:+.4f}, ±{noise:.4f}) → {결론[-1]['판정']}")
    for r in [x for x in RES if x["endpoint"] == ep]:
        log(f"    게이트 0.70 [{r['대조군']}] 보정 AUC {r['roc_auc_보정']:.4f} → "
            f"{'통과' if r['roc_auc_보정'] >= 0.70 else '미달'}")
pd.DataFrame(결론).to_csv(OUT / "A0채움_판정.csv", index=False,
                         encoding="utf-8-sig")
log("주의 — A0 채움이 개선을 내더라도 그 A0 칸은 더 이상 '성분 MSDS 선언값' 이 "
    "아니다. L2 노선 정의가 바뀌는 것이므로 성능만으로 채택을 정할 수 없다. "
    "`A0채움_전체 vs 반영후_NC유지` 가 잡음이면 개선의 정체는 NC 살리기이지 "
    "A0 채움이 아니다. 'A0 이동' 효과를 홀로 보려면 `A0채움_유해만 vs 반영후` 를 "
    "읽는다. arm 별 NC·정보원 구성은 `A0채움_구성.csv`.")
log(f"완료 → {OUT.relative_to(L.ROOT)}/A0채움_대조.csv · A0채움_판정.csv")
