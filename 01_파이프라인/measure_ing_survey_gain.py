#!/usr/bin/env python3
"""성분 GHS 조사값 반영 대조 — 산출: 04_모델산출물/v8_노선2/조사반영_대조.csv

`collect_ing_ghs.py` 가 채운 성분 구분을 가산식 입력에 얹었을 때 L2 대표 셀의 성능이
실제로 어떻게 변하는지 잰다. 커버리지가 올랐다는 것(검수 C7)은 값이 많아졌다는 뜻일
뿐이므로 성능은 따로 재야 한다.

## 어느 칸에 넣는가

`ing_ghs_indep_{ep}_cat`·`_tier` 에 넣는다. `ing_ghs_{ep}`(A0 입력)에 넣지 않는다 —
A0 는 **성분 MSDS 선언값** 칸이고 이 조사는 외부 규제 DB(CLP Annex VI·ECHA C&L)
유래라서, A0 에 섞으면 정보원 귀속이 무너진다. `indep` 칸은 기존 A2 증강분과 출처
계열이 같다.

기존 값이 있는 칸은 덮지 않는다. 조사는 결측 칸만 채운다.

## 버전 고정 스크립트를 건드리지 않는다

`run_model_lane2.py`·`build_input_*`·`lib_model.py` 를 수정하지 않는다. 같은
규약(공통폴드 · 폴드별 τ · 층내부 보정 AUC)을 복제해 대표 2 셀만 돌린다.
`lib_model.FormulationData.matrices()` 는 A2 커버리지 검증값을 assert 로 박아 놨으므로
(`lib_model.py:404-409`) 조사값을 얹은 뒤에는 쓸 수 없다 — 기준선을 먼저 뽑아 두고,
이후에는 `build_ct` → `ct_to_X` 를 직접 불러 같은 `native_복원` 규약으로 행렬을 만든다.

## 대조군 3 개

| 이름 | 내용 |
|---|---|
| `조사전` | 현행. 배포된 대표 산출물과 같은 입력 |
| `조사후_전체` | 조사값 전부 반영 |
| `조사후_이름경로제외` | `동일성등급 == 이름_동의어일치` 를 뺀다 |

세 번째가 있는 이유: 이름 경로 값의 정확도는 **저장소 안 정답으로는 측정할 수 없다**
(이름 키를 쓰면서 기존값까지 가진 성분이 12 종뿐이다). 검수 C5 가 이것을 중대 결함으로
남겨 두었으므로, 정확도를 못 재는 대신 **하류 영향**을 재서 결정 근거를 만든다. 빼도
성능이 안 바뀌면 위험이 실질적으로 없고, 바뀌면 그 방향으로 정한다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*, v8_누출감사/*,
v8_성분조사/성분GHS_조사결과.csv
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             matthews_corrcoef, roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold

import lib_ct_nckeep as NCK
import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"
CANON_IMP = "native_복원"
N_INNER = 3
DOC_TIERS = ("문서독립", "문서공유_분류절", "문서공유_11절")
COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
AUDIT = L.ROOT / "04_모델산출물" / "v8_누출감사"
SURVEY = L.ROOT / "04_모델산출물" / "v8_성분조사" / "성분GHS_조사결과.csv"
OUT = L.ROOT / "04_모델산출물" / "v8_노선2"
log = L.make_logger(OUT / "조사반영_대조.log")

# 조사 tier → 기존 `_tier` 어휘. A3 arm 이 `annex_vi` 만 받으므로 어휘를 맞춘다
TIER_MAP = {"tier1_AnnexVI": "annex_vi", "tier2_ECHA_CL": "echa_cl"}


def tau_youden(y, p):
    fpr, tpr, thr = roc_curve(y, p)
    return float(thr[int(np.argmax(tpr - fpr))])


def assemble_pred(p, tau_fold, n):
    """폴드별 τ 를 그 폴드 검증행에만 적용(규약 §2.4)."""
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
    """일치쌍 가중 층내부 AUC(규약 §4.1-3). pooled 단독 인용은 규약 위반이다."""
    num = den = 0.0
    for d in per:
        w = d["n"] * d["p"] * d["n"] * (1 - d["p"])
        num += w * d["auc"]
        den += w
    return num / den if den > 0 else float("nan")


def has(s):
    return s.notna() & (~s.astype(str).str.strip().str.lower()
                        .isin(["nan", "none", ""]))


log("=== 성분 GHS 조사값 반영 대조 ===")
Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
ARM = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))["arm"]
SUB = np.load(AUDIT / "부분집합마스크.npz")
data = L.FormulationData(log)
assert (data.FID == Z["Formulation_ID"]).all(), "행 정렬 이탈"
CHEM_IDX = {c: i for i, c in enumerate(data.CHEM)}
BASE = ARM["L2단독"]
miss = [c for c in BASE if c not in CHEM_IDX]
assert not miss, f"CHEM 에 없는 열 {miss[:5]}"
COLS = [CHEM_IDX[c] for c in BASE]
# 이미 배포된 `조사반영_*.csv` 는 42열 기준이다. 2026-09-21 결정으로 arm 이 31열이
# 됐으므로 재실행분과 종전분을 같은 표에서 비교하지 않는다. 열 수를 로그에 남긴다.
log(f"기준 arm: L2단독 {len(BASE)}열 (피처노선_arm.json). 42열 기준 종전 산출과 "
    f"같은 표에서 비교하지 않는다")

# 기준선을 **먼저** 뽑는다. matrices() 의 A0 재현·커버리지 assert 는 조사값을 얹기
# 전에만 성립하므로, 이 호출이 곧 '조사 전 입력이 배포본과 같다'는 검증이다.
X_BASE = data.matrices()[("CT_권고", CANON_IMP)]

# --- 조사 결과 적재 -------------------------------------------------------
R = pd.read_csv(SURVEY)
assert "동일성등급" in R.columns, "조사 결과가 구버전이다 — 재조사 필요"
log(f"조사 결과 {len(R)}종 · 동일성등급 "
    f"{R['동일성등급'].value_counts(dropna=False).to_dict()}")
ING0 = data.ING.copy()                     # 원본 보존. 변형은 사본에만 한다
키 = ING0["ing_cas_best"].where(has(ING0["ing_cas_best"]),
                                ING0["ing_name_best"]).astype(str)


def overlay(이름경로포함: bool):
    """조사값을 `indep` 칸의 **결측에만** 채운 ING 사본을 돌려준다.

    **농도가 있는 성분행에만 채운다.** `ct_predict` 는 `pct is None` 만 걸러내고
    `NaN` 은 통과시킨다(`lib_model.py:439`). 그래서 농도가 `NaN` 인 행에 구분을
    넣으면 `s2` 가 `NaN` 이 되어 `add` 가 통째로 `NaN` 이 되고, `NaN >= 10` 은 거짓
    이므로 제형 판정이 조용히 `NC` 로 떨어진다. 실측: `ct_predict([('2',17.6),
    ('2A',nan)],'eye')` → `add=nan, cat='NC'` — 구분 하나를 **더했더니** 판정이
    2A 에서 NC 로 내려간다. 조사 대상 자체가 농도 보유 행이었으므로(수집기의
    `pct & 부족` 조건) 농도 없는 행에 값을 얹는 것은 애초에 범위 밖이다.
    `lib_model.py` 는 버전 고정이라 그쪽을 고치지 않고 이 자리에서 막는다.
    """
    sel = R if 이름경로포함 else R[R["동일성등급"] != "이름_동의어일치"]
    I = ING0.copy()
    적용 = {}
    pct_ok = has(I["ing_pct_best"]) & I["ing_pct_best"].notna()
    for ep in EPS:
        s = sel[sel[f"{ep}_구분"].notna()]
        cat = 키.map(dict(zip(s["키"].astype(str), s[f"{ep}_구분"].astype(str))))
        tier = 키.map(dict(zip(s["키"].astype(str),
                               s[f"{ep}_출처tier"].map(TIER_MAP))))
        빈칸 = ~has(I[f"ing_ghs_indep_{ep}_cat"]) & has(cat)
        버림 = int((빈칸 & ~pct_ok).sum())
        빈칸 &= pct_ok
        # 이미 값이 있는 칸은 덮지 않는다. 조사는 결측만 채운다
        I.loc[빈칸, f"ing_ghs_indep_{ep}_cat"] = cat[빈칸]
        I.loc[빈칸, f"ing_ghs_indep_{ep}_tier"] = tier[빈칸]
        적용[ep] = int(빈칸.sum())
        if 버림:
            log(f"  {ep}: 농도 결측 {버림} 성분행은 채우지 않았다 "
                f"(가산식이 NaN 으로 오염된다)")
    return I, 적용


# native_복원 규약 재현(`lib_model.py:411-427`). 빌드 단계에서 0 으로 굳은 결측을
# 조성미상 행에서만 되돌린다. 기준선과 같은 규약이어야 비교가 성립한다
ZI = [data.CHEM.index(c) for c in L.ZERO_IS_MISSING if c in data.CHEM]


def as_native(X):
    M = X[data.CHEM].to_numpy(dtype=np.float64)
    for j in ZI:
        assert not np.isnan(M[data.NOPCT, j]).any(), \
            f"{data.CHEM[j]}: 조성미상 행에 이미 결측 — 규약 전제 이탈"
        M[data.NOPCT, j] = np.nan
    return M


REC_ARM = {"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown",
           "sens": "A3_aug_annexvi"}


def build_ct_nc유지(I):
    """`lib_ct_nckeep` 의 공유 구현을 부른다.

    **이 함수를 여기서 다시 구현하지 않는다.** 앞판에서는 이 스크립트와
    `measure_a0_fill.py` 에 각자 복사본이 있었고, 한쪽에서 `sens` 의 A3 tier
    게이트가 빠져 **같은 이름의 arm 이 두 산출물에서 다른 숫자로 발표됐다**
    (eye `NC유지` 보정 AUC 0.6591 vs 0.6629). 정의는 한 곳에만 둔다.
    """
    return NCK.build_ct_nc유지(data, I)


def build(이름경로포함, nc유지=False):
    I, 적용 = overlay(이름경로포함)
    data.ING = I                            # build_ct 가 읽는 자리
    try:
        ct = (build_ct_nc유지(I) if nc유지
              else data.build_ct(REC_ARM, L.EPS_ALL))
        M = as_native(data.ct_to_X(ct, L.EPS_ALL))
    finally:
        data.ING = ING0                     # 어떤 경로로 끝나도 원본으로 되돌린다
    cov = {ep: round(float(ct[f"f_ct_{ep}_coverage"].mean()), 4) for ep in EPS}
    n판정 = {ep: int(ct[f"f_ct_{ep}_cat"].notna().sum()) for ep in EPS}
    return M, 적용, cov, n판정


VAR = [("조사전", X_BASE, None, None, None)]
for 이름, 포함, nc in (("조사후_전체", True, False),
                    ("조사후_이름경로제외", False, False),
                    ("조사후_NC유지", True, True)):
    M, 적용, cov, n판정 = build(포함, nc)
    log(f"{이름}: indep 결측 채움 {적용} · 성분커버리지 {cov} · 판정행 {n판정}")
    assert not np.allclose(np.nan_to_num(M), np.nan_to_num(X_BASE)), \
        f"{이름} 이 기준선과 동일하다 — 조사값이 반영되지 않았다"
    VAR.append((이름, M, 적용, cov, n판정))

# NC 유지의 효과와 조사의 효과를 가른다 — 조사값 없이 NC 만 유지한 기준선.
# 이것이 없으면 `조사후_NC유지` 의 변화가 조사 때문인지 NC 규약 때문인지 알 수 없다
ct_nc0 = build_ct_nc유지(ING0)
M_nc0 = as_native(data.ct_to_X(ct_nc0, L.EPS_ALL))
VAR.append(("조사전_NC유지", M_nc0, {ep: 0 for ep in EPS},
            {ep: round(float(ct_nc0[f"f_ct_{ep}_coverage"].mean()), 4)
             for ep in EPS},
            {ep: int(ct_nc0[f"f_ct_{ep}_cat"].notna().sum()) for ep in EPS}))
log(f"조사전_NC유지: 성분커버리지 {VAR[-1][3]} · 판정행 {VAR[-1][4]}")

# 기준선 커버리지도 같은 방식으로 찍어 표에 남긴다
ct0 = data.build_ct(REC_ARM, L.EPS_ALL)
COV0 = {ep: round(float(ct0[f"f_ct_{ep}_coverage"].mean()), 4) for ep in EPS}
N0 = {ep: int(ct0[f"f_ct_{ep}_cat"].notna().sum()) for ep in EPS}
log(f"조사전: 성분커버리지 {COV0} · 판정행 {N0}")
VAR[0] = ("조사전", X_BASE, {ep: 0 for ep in EPS}, COV0, N0)

# --- 측정 ---------------------------------------------------------------
RES = []
SEED = {}          # (ep, 조사반영) → 지표별 시드별 값. 대응차 밴드에 쓴다
for ep in EPS:
    m = Z[f"mask_{ep}"]
    y = Z[f"y_{ep}"].astype(int)
    g = Z[f"group_{ep}"]
    folds = [[(np.setdiff1d(np.arange(len(y)), Z[f"te_{ep}_s{si}_f{fi}"]),
               Z[f"te_{ep}_s{si}_f{fi}"]) for fi in range(L.N_SPLITS)]
             for si in range(len(L.SEEDS))]
    for 이름, M, 적용, cov, n판정 in VAR:
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
            per = []
            for t in DOC_TIERS:
                k = SUB[f"{ep}__{t}"]
                assert len(k) == len(y)
                per.append({"n": int(k.sum()), "p": float(y[k].mean()),
                            "auc": roc_auc_score(y[k], p[k])})
            보정.append(auc_보정(per))
        rec = {"endpoint": ep, "조사반영": 이름, "관할": CANON_JUR_V8,
               "n": int(len(y)), "유병률": round(float(y.mean()), 4),
               "indep_채움_성분행": 적용[ep], "성분커버리지": cov[ep],
               "CT판정행": n판정[ep]}
        for k in acc[0]:
            rec[k] = round(float(np.mean([d[k] for d in acc])), 4)
            rec[k + "_sd"] = round(float(np.std([d[k] for d in acc], ddof=1)), 4)
        rec["roc_auc_보정"] = round(float(np.mean(보정)), 4)
        rec["roc_auc_보정_sd"] = round(float(np.std(보정, ddof=1)), 4)
        rec["tau_평균"] = round(float(np.mean(taus)), 4)
        rec["tau_sd"] = round(float(np.std(taus, ddof=1)), 4)
        SEED[(ep, 이름)] = {k: [d[k] for d in acc] for k in acc[0]}
        SEED[(ep, 이름)]["roc_auc_보정"] = list(보정)
        RES.append(rec)
        log(f"  {ep:4} {이름:18} 커버리지={cov[ep]:.3f} "
            f"pooled AUC={rec['roc_auc']:.4f}±{rec['roc_auc_sd']:.4f} "
            f"보정 AUC={rec['roc_auc_보정']:.4f} MCC={rec['MCC']:.4f} "
            f"BA={rec['BA']:.4f} τ={rec['tau_평균']:.3f}")

pd.DataFrame(RES).to_csv(OUT / "조사반영_대조.csv", index=False,
                         encoding="utf-8-sig")

log("--- 판정 ---")
결론 = []
# NC 유지 변이는 NC 유지 기준선과 견준다. 아니면 조사 효과와 NC 규약 효과가 섞인다
쌍 = (("조사후_전체", "조사전"), ("조사후_이름경로제외", "조사전"),
     ("조사후_NC유지", "조사전_NC유지"), ("조사전_NC유지", "조사전"))
for ep in EPS:
    for 이름, 기준 in 쌍:
        a = next(r for r in RES if r["endpoint"] == ep
                 and r["조사반영"] == 기준)
        b = next(r for r in RES if r["endpoint"] == ep and r["조사반영"] == 이름)
        for k in ("roc_auc", "roc_auc_보정", "MCC", "BA"):
            # **대응차(paired) 밴드.** 두 arm 은 같은 5 개 시드로 돌았고 시드가 폴드
            # 배정과 RF 난수를 함께 정하므로 시드별 값이 강하게 상관한다. 각 arm 의
            # 주변 SD 를 밴드로 쓰면 그 공통 변동을 두 번 세어 밴드가 과대해지고
            # 모든 대조가 '재현 범위 안' 으로 나온다. 같은 시드끼리 뺀 차이가 맞다
            ds = (np.asarray(SEED[(ep, 이름)][k])
                  - np.asarray(SEED[(ep, 기준)][k]))
            # 차이도 **미반올림 시드차의 평균**으로 낸다. 4 자리로 반올림된 평균끼리
            # 빼고 미반올림 밴드와 견주면 `차이 == 잡음범위` 인데 판정이 '밖' 으로
            # 나오는 재현 불가 행이 생긴다. 자리수도 6 자리로 늘려 CSV 에서 검증 가능
            d = float(np.mean(ds))
            noise = 2 * float(np.std(ds, ddof=1))
            결론.append({"endpoint": ep, "대조": 이름, "지표": k,
                        "조사전": a[k], "조사후": b[k], "차이": round(d, 6),
                        "잡음범위_대응차SD2배": round(noise, 6),
                        "판정": "시드 재현 범위 밖 — 개선" if d > noise else
                                "시드 재현 범위 밖 — 손실" if d < -noise else
                                "시드 재현 범위 안 — 변화 없음"})
            log(f"  {ep:4} {이름:18} {k:12} {a[k]:.4f} → {b[k]:.4f} "
                f"({d:+.4f}, ±{noise:.4f}) → {결론[-1]['판정']}")
    # 게이트는 pooled 가 아니라 보정 AUC 로 판정한다(규약 §4.1-3).
    # 앞판은 루프에서 흘러나온 `a` 에 의존했다 — `쌍` 순서를 바꾸면 조용히 다른
    # arm 의 게이트를 찍는다. RES 에서 이름으로 직접 집는다
    for r in [x for x in RES if x["endpoint"] == ep]:
        log(f"    게이트 0.70 [{r['조사반영']}] 보정 AUC "
            f"{r['roc_auc_보정']:.4f} → "
            f"{'통과' if r['roc_auc_보정'] >= 0.70 else '미달'}")
pd.DataFrame(결론).to_csv(OUT / "조사반영_판정.csv", index=False,
                         encoding="utf-8-sig")
log("주의 — 커버리지 상승이 성능 상승을 보장하지 않는다. 보정 AUC 는 라벨 출처 층 "
    "내부 순위 지표이므로 층을 가로지르는 이득이 여기서는 잡히지 않는다. pooled 만 "
    "올랐다면 그것은 재현 능력 향상이 아닐 수 있다.")
log(f"완료 → {OUT.relative_to(L.ROOT)}/조사반영_대조.csv · 조사반영_판정.csv")
