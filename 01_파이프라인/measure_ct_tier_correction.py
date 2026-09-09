#!/usr/bin/env python3
"""critic C1 — A3 arm 출처등급 오염의 정량화.

결함
----
`parse_cy()` 의 tier 판정은 분류 셀 문자열에서 `"ECHA C&L 통보"` 를 찾는다.
그런데 NC 보일러플레이트는
    분류대상아님(EU CLP Annex VI/ECHA C&L **기준** 해당 H코드 없음)
이라 `"통보"` 검사에 걸리지 않고 첫 분기로 떨어져 **NC 200건 전부가
`annex_vi`** 로 판정된다. 실측(2026-09-02):

    감작 NC 201건 중 메모에 'EU CLP Annex VI 존재' 기록이 있는 것 120건,
    **없는 것 81건** (ECHA C&L 통보 = 자가신고만 있음)
    눈 NC 161건 중 없는 것 54건 / 피부 NC 190건 중 없는 것 61건

즉 A3(`Annex VI 조화분류 전용`) arm 이 **자가신고 근거만 있는 CAS 를 조화분류로
받아들여** `n_known` 에 넣고 있었다. 조회했으나 자가신고 목록에 없는 것을
음성으로 쓴 것이며, 이 프로젝트가 금지한 '미시험을 음성으로' 결함이 형태를
바꿔 재발한 것이다. 팀 자체 QA(`채윤_GHS_검수요약.json`)가
`"AnnexVI_부존재_NC_영향_CAS수": 94` 로 이미 경고했으나 지표 파이프라인에
반영되지 않았다.

이 스크립트가 하는 일
--------------------
기존 값을 **덮지 않는다.** `build_input_v6.py` 가 병기한 교정 출처등급
`ing_ghs_indep_{ep}_tier_src`(메모 기록 기반)로 A3_strict arm 을 만들어,
`권고`(현행) vs `권고_교정`(sens A3→A3s) 을 동일 폴드·동일 규약으로 나란히
측정한다. 감작 CT 이득 중 얼마가 오염된 NC 에서 나온 것인지 분리하는 것이
목적이다. `_COV` assert 상수를 조용히 맞추는 일은 하지 않는다.

또한 critic M6 지적(커버리지 33.0/29.6/49.6 을 한 줄에 나란히 제시한 것이
arm 혼용)에 답하기 위해 **arm × endpoint 커버리지 격자**를 함께 낸다.
A2 는 NC 를 미지로 버리고 A3 는 NC 를 아는 값으로 세므로 두 arm 의 커버리지는
직접 비교할 수 없다.

읽기 전용. 산출은 v6_jurisdiction/ 에 추가(기존 파일 미수정).
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import matthews_corrcoef, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04_모델산출물" / "v4_fixed"
V6 = ROOT / "04_모델산출물" / "input_dataset_v6.xlsx"
OUT = ROOT / "04_모델산출물" / "v6_jurisdiction"
SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
NEGREC = "sds_v1_negative_recovered"
EPS = ("eye", "skin", "sens")
_t0 = time.time()
_LOGF = open(OUT / "run_ct_tier_correction.log", "w", encoding="utf-8")


def log(m):
    line = f"[{time.time()-_t0:7.1f}s] {m}"
    print(line, flush=True)
    _LOGF.write(line + "\n")
    _LOGF.flush()


def rf(seed):
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  min_samples_leaf=2, n_jobs=-1, random_state=seed)


# ==================================================================== 로드
X = pd.read_parquet(SRC / "X_formulation.parquet")
Y = pd.read_parquet(SRC / "y_formulation.parquet")
MAN = pd.read_csv(SRC / "feature_role_manifest.csv")
FID = X["Formulation_ID"].astype(str).to_numpy()
GK = Y["group_key"].astype(str).to_numpy()
N = len(FID)
MANF = MAN[MAN["sheet"] == "formulation"]
CHEM = sorted(c for c in MANF[(MANF["feature_role"] == "chemistry")
                              & (~MANF["exclude_by_default"].astype(bool))]["column"]
              if c != "Formulation_ID")
CT_COLS = sorted(c for c in CHEM if c.startswith(("f_ct_", "ct_")))
assert len(CT_COLS) == 26

xl = pd.ExcelFile(V6)
FORM_I = xl.parse("formulation").set_index("Formulation_ID")
ING = xl.parse("ingredient")
assert len(FORM_I) == 1675 and len(ING) == 5287, "v6 행수 이탈"
for ep in EPS:
    for k in ("cat", "tier", "tier_src"):
        assert f"ing_ghs_indep_{ep}_{k}" in ING.columns, \
            f"{ep}_{k} 없음 — build_input_v6.py 를 먼저 실행"

RAW = {ep: Y[f"y_{ep}"].fillna("").astype(str).to_numpy() for ep in EPS}
SRCD = {ep: Y[f"y_{ep}_src_detail"].fillna("").astype(str).to_numpy() for ep in EPS}

# ---- 오염 규모를 코드로 확정(보고용) ---------------------------------------
_cy = ING[ING["GHS조사_눈"].notna()]
assert len(_cy) == 331
CONTAM = {}
for ep in EPS:
    col = {"eye": "GHS조사_눈", "skin": "GHS조사_피부", "sens": "GHS조사_감작"}[ep]
    t = _cy[col].astype(str)
    nc = t.str.startswith("분류대상아님")
    memo_annex = _cy["GHS조사_메모"].astype(str).str.contains("EU CLP Annex VI 존재")
    CONTAM[ep] = {
        "NC_CAS": int(nc.sum()),
        "NC_중_AnnexVI기록_있음": int((nc & memo_annex).sum()),
        "NC_중_AnnexVI기록_없음": int((nc & ~memo_annex).sum()),
        "tier_불일치_CAS": int((_cy[f"ing_ghs_indep_{ep}_tier"].astype(str)
                            != _cy[f"ing_ghs_indep_{ep}_tier_src"].astype(str)).sum()),
    }
log("오염 규모: " + json.dumps(CONTAM, ensure_ascii=False))

# ---- L1 / L2 / 마스크 (기존 산출과 동일) ------------------------------------
CANON = {ep: RAW[ep].copy() for ep in EPS}
_fix = sum(1 for i, v in enumerate(CANON["skin"]) if v in ("2A", "2B"))
for i, v in enumerate(CANON["skin"]):
    if v in ("2A", "2B"):
        CANON["skin"][i] = "2"
assert _fix == 82
_UN_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 1, "NC": 0}
_EU_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 0, "NC": 0}
_UN_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 1, "NC": 0}
_EU_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 0, "NC": 0}
_SENS = {"1": 1, "1A": 1, "1B": 1, "NC": 0}
JUR = {"UN_GHS": {"eye": _UN_EYE, "skin": _UN_SKIN, "sens": _SENS},
       "EU_CLP": {"eye": _EU_EYE, "skin": _EU_SKIN, "sens": _SENS},
       "K_REACH": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS},
       "US_OSHA": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS}}
EPA_SKIN4 = (FORM_I["ntp_epa_skin_cat"].reindex(FID).astype(float) == 4).to_numpy()
assert int(EPA_SKIN4.sum()) == 444
IS_NEGREC = {ep: (SRCD[ep] == NEGREC) for ep in EPS}


def project(jn, ep):
    tab = JUR[jn][ep]
    b = np.full(N, -1, dtype=int)
    for i, v in enumerate(CANON[ep]):
        if v == "":
            continue
        b[i] = tab[v]
    keep = b >= 0
    if ep == "skin" and tab.get("3") == 1:
        keep = keep & ~EPA_SKIN4
    if ep == "eye":
        keep = keep & ~IS_NEGREC["eye"]
    return b, keep


LAYER = {(jn, ep): project(jn, ep) for jn in JUR for ep in EPS}
_EXP = {("eye", "UN_GHS"): 1044, ("eye", "EU_CLP"): 1044, ("skin", "UN_GHS"): 615,
        ("skin", "EU_CLP"): 1059, ("sens", "UN_GHS"): 716}
for (ep, jn), n in _EXP.items():
    assert int(LAYER[(jn, ep)][1].sum()) == n, f"{jn}/{ep} 행수 이탈"
log("L1/L2/마스크 재현 확인")

# ==================================================================== CT
CAT1, CAT2 = {"1", "1A", "1B", "1C"}, {"2", "2A", "2B"}
SEV_CT = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
          "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1,
                   "2A": 2, "2B": 1, "NC": 0},
          "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}


def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v)
    return None if s.lower() in ("nan", "none", "") else s


def ct_predict(pairs, endpoint):
    s1 = s2 = s3 = s1a = 0.0
    n = 0
    for cat, pct in pairs:
        if cat is None or pct is None:
            continue
        c = str(cat).strip().upper()
        if c in ("NOT CLASSIFIED", "NC", "-", ""):
            n += 1
            continue
        if c in CAT1:
            s1 += float(pct)
            if c == "1A":
                s1a += float(pct)
        elif c in CAT2:
            s2 += float(pct)
        elif c == "3":
            s3 += float(pct)
        else:
            continue
        n += 1
    if n == 0:
        return {"cat": None, "s1": None, "s2": None, "add": None, "n_known": 0}
    add = 10 * s1 + s2
    if endpoint == "eye":
        cat = "1" if s1 >= 3 else ("2A" if (s1 >= 1 or s2 >= 10 or add >= 10) else "NC")
    elif endpoint == "skin":
        cat = ("1" if s1 >= 5 else "2" if (s1 >= 1 or s2 >= 10 or add >= 10)
               else "3" if s3 >= 20 else "NC")
    else:
        cat = "1" if (s1 >= 1.0 or s1a >= 0.1) else "NC"
    return {"cat": cat, "s1": s1, "s2": s2, "add": add, "n_known": n}


ING_G = ING[["Formulation_ID", "cas", "ing_pct_best",
             "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens"]
            + [f"ing_ghs_indep_{ep}_{k}" for ep in EPS
               for k in ("cat", "tier", "tier_src")]].copy()
_GROUPED = list(ING_G.groupby("Formulation_ID", sort=False))


def build_ct(arm_by_ep):
    """arm: A0_base / A2_aug_nc_unknown / A3_aug_annexvi / A3s_aug_annexvi_strict"""
    rows = []
    for fid, g in _GROUPED:
        nrow = len(g)
        row = {"Formulation_ID": fid}
        for ep in EPS:
            arm = arm_by_ep[ep]
            cats, pcts = [], []
            for _, r in g.iterrows():
                cat = _clean(r[f"ing_ghs_{ep}"])
                if arm != "A0_base" and cat is None:
                    c = _clean(r[f"ing_ghs_indep_{ep}_cat"])
                    if c is not None:
                        if arm == "A2_aug_nc_unknown" and c == "NC":
                            c = None
                        elif arm == "A3_aug_annexvi":
                            if _clean(r[f"ing_ghs_indep_{ep}_tier"]) != "annex_vi":
                                c = None
                        elif arm == "A3s_aug_annexvi_strict":
                            if _clean(r[f"ing_ghs_indep_{ep}_tier_src"]) != "annex_vi":
                                c = None
                        cat = c
                cats.append(cat)
                pcts.append(r["ing_pct_best"])
            ct = ct_predict(list(zip(cats, pcts)), ep)
            row[f"f_ct_{ep}_cat"] = ct["cat"]
            row[f"f_ct_{ep}_s1"] = ct["s1"]
            row[f"f_ct_{ep}_s2"] = ct["s2"]
            row[f"f_ct_{ep}_add"] = ct["add"]
            row[f"f_ct_{ep}_n_known"] = ct["n_known"]
            row[f"f_ct_{ep}_coverage"] = (ct["n_known"] / nrow) if nrow else None
            o = SEV_CT[ep].get(ct["cat"]) if ct["cat"] is not None else None
            row[f"f_ct_{ep}_ord"] = o
            row[f"ct_{ep}_ord"] = o
        rows.append(row)
    return pd.DataFrame(rows).set_index("Formulation_ID").reindex(FID)


def ct_to_X(base_X, ct):
    Xn = base_X.copy()
    for c in CT_COLS:
        if c == "ct_not_applicable":
            continue
        m = re.match(r"f_ct_(eye|skin|sens)_cat_(.+)$", c)
        if m:
            ep, lev = m.group(1), m.group(2)
            cur = ct[f"f_ct_{ep}_cat"]
            want = None if lev == "__NA__" else lev
            Xn[c] = (cur.isna() if want is None
                     else (cur.astype(str) == want)).astype(int).to_numpy()
        else:
            Xn[c] = ct[c].to_numpy(dtype=float) if c in ct.columns else Xn[c]
    return Xn


X0 = X[CHEM].copy()

# ---- M6 답변: arm × endpoint 커버리지 격자 (동일 규칙으로 재계산) -----------
log("커버리지 격자 산출 (arm 4종 × endpoint 3종)")
ARMS = ["A0_base", "A2_aug_nc_unknown", "A3_aug_annexvi", "A3s_aug_annexvi_strict"]
CTS = {a: build_ct({ep: a for ep in EPS}) for a in ARMS}
COVG = pd.DataFrame([
    {"arm": a, **{ep: round(float(CTS[a][f"f_ct_{ep}_coverage"].mean()), 4)
                  for ep in EPS}} for a in ARMS])
log("\n" + COVG.to_string(index=False))
# A0 는 디스크와 일치해야 한다(드리프트 검출)
_mism = [c for c in CT_COLS if not np.allclose(
    pd.to_numeric(ct_to_X(X0, CTS["A0_base"])[c], errors="coerce").fillna(-999),
    pd.to_numeric(X0[c], errors="coerce").fillna(-999))]
assert not _mism, f"A0 재현 실패: {_mism[:5]}"
log("A0 == 디스크 — 전처리 드리프트 없음")

# ---- 권고 vs 권고_교정 -------------------------------------------------------
REC = {"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown",
       "sens": "A3_aug_annexvi"}
REC_FIX = dict(REC, sens="A3s_aug_annexvi_strict")
CT_REC, CT_FIX = build_ct(REC), build_ct(REC_FIX)
log(f"권고 감작 커버리지 {CT_REC['f_ct_sens_coverage'].mean():.4f} → "
    f"교정 {CT_FIX['f_ct_sens_coverage'].mean():.4f}")
XD = {"권고": ct_to_X(X0, CT_REC), "권고_교정": ct_to_X(X0, CT_FIX)}


def as_mat(df, im):
    M = df[CHEM].to_numpy(dtype=np.float64)
    return np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0) if im == "nan0" else M


XM = {(k, im): as_mat(df, im) for k, df in XD.items() for im in ("nan0", "native")}

GROUPS = []
for ep in EPS:
    seen = {}
    for jn in JUR:
        b, k = LAYER[(jn, ep)]
        seen.setdefault((b[k].tobytes(), k.tobytes()), []).append(jn)
    for _key, jns in seen.items():
        GROUPS.append((ep, jns))

RES = []
for ep, jns in GROUPS:
    b, k = LAYER[(jns[0], ep)]
    y = b[k]
    folds = [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True,
                                       random_state=sd).split(np.zeros((len(y), 1)),
                                                              y, GK[k]))
             for sd in SEEDS]
    for im in ("nan0", "native"):
        per = {}
        for arm in ("권고", "권고_교정"):
            Xi = XM[(arm, im)][k]
            aucs, mccs = [], []
            for si, sd in enumerate(SEEDS):
                p = np.full(len(y), np.nan)
                for tr, te in folds[si]:
                    p[te] = rf(sd).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
                aucs.append(roc_auc_score(y, p))
                mccs.append(matthews_corrcoef(y, (p >= 0.5).astype(int)))
            per[arm] = (np.array(aucs), np.array(mccs))
        d = per["권고_교정"][0] - per["권고"][0]
        t, pv = stats.ttest_rel(per["권고_교정"][0], per["권고"][0])
        RES.append({
            "endpoint": ep, "관할": "+".join(jns), "결측처리": im,
            "n": int(len(y)), "유병률": round(float(y.mean()), 4),
            "AUC_권고": round(float(per["권고"][0].mean()), 4),
            "AUC_권고_sd": round(float(per["권고"][0].std(ddof=1)), 4),
            "AUC_권고_교정": round(float(per["권고_교정"][0].mean()), 4),
            "AUC_권고_교정_sd": round(float(per["권고_교정"][0].std(ddof=1)), 4),
            "Δ(교정−현행)": round(float(d.mean()), 4),
            "t_df4": round(float(t), 2), "p": round(float(pv), 4),
            "시드양수": int((d > 0).sum()),
            "MCC_권고": round(float(per["권고"][1].mean()), 4),
            "MCC_권고_교정": round(float(per["권고_교정"][1].mean()), 4),
        })
        log(f"  {ep:4} {RES[-1]['관할']:26} [{im:6}] "
            f"{RES[-1]['AUC_권고']:.4f} → {RES[-1]['AUC_권고_교정']:.4f} "
            f"(Δ {RES[-1]['Δ(교정−현행)']:+.4f}, t={RES[-1]['t_df4']:+.2f}, "
            f"시드양수 {RES[-1]['시드양수']}/5)")

R = pd.DataFrame(RES)
R.to_csv(OUT / "C1_출처등급교정_영향.csv", index=False, encoding="utf-8-sig")
COVG.to_csv(OUT / "C1_커버리지_격자.csv", index=False, encoding="utf-8-sig")
with open(OUT / "C1_요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "결함": "parse_cy tier 판정이 NC 보일러플레이트('ECHA C&L 기준')를 "
              "'통보' 검사로 놓쳐 NC 전량을 annex_vi 로 판정. A3 arm 이 자가신고 "
              "근거만 있는 CAS 를 조화분류로 받아들였다.",
        "오염규모_CAS": CONTAM,
        "커버리지_격자": COVG.to_dict("records"),
        "커버리지_주의": "A2 는 NC 를 미지로 버리고 A3 는 NC 를 아는 값으로 센다. "
                    "두 arm 의 커버리지는 직접 비교할 수 없다(critic M6).",
        "감작_커버리지": {"권고(A3, 현행)": round(float(CT_REC["f_ct_sens_coverage"].mean()), 4),
                     "권고_교정(A3s)": round(float(CT_FIX["f_ct_sens_coverage"].mean()), 4)},
        "성능영향": RES,
        "주의": "기존 값을 덮지 않았다. _tier 는 재현용으로 보존되고 _tier_src 가 "
              "병기된다. arm 교체는 총책임자 판단 사안이다.",
    }, f, ensure_ascii=False, indent=2)
log(f"완료 → {OUT}/C1_출처등급교정_영향.csv")
