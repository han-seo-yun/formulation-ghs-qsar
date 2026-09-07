#!/usr/bin/env python3
"""현행 v6 상태의 전체 지표 보고 — ROC-AUC / PR-AUC / F1 / MCC / BA.

배경
----
앞서 F1 을 헤드라인 지표에서 뺐다(불균형 데이터에서 F1 은 진음성을 무시하고
유병률에 따라 값이 움직여 관할 간 비교가 불가하기 때문). 총책임자가 F1 을 포함해
전부 보고하라고 지시했으므로 산출한다. **F1 을 좋게 보이려고 임계값을 조정하지
않는다** — 임계값은 정규 베이스라인과 동일하게 0.5 고정이다. 임계값 민감도는
별도 열(F1_최적임계값)로 참고만 제시하되, 그 값은 OOF 에서 후향적으로 고른
낙관적 상한이므로 성능 주장에 쓸 수 없음을 산출물에 명시한다.

측정 대상 = 현행 상태
  * eye  : negrec 109행 **마스크 적용** (n=1044)
  * skin : UN 615 / EU·K·US 1059
  * sens : 716 (전 관할 동일)
  * CT   : A0(기본) / 권고(eye·skin A2, sens A3)
  * 결측 : nan0(정규 베이스라인, 주) / native(병기)
폴드는 (endpoint, 관할) 별로 1회 산출해 CT arm·결측규약 간 고정.

입력 통합(2026-09-02)
--------------------
이전 판은 `input_dataset_v5.xlsx` 와 `정채윤_GHS 조사.xlsx` 를 각각 읽고 정채윤
데이터를 런타임에 파싱했다. 이제 `input_dataset_v6.xlsx` 하나만 읽는다
(`build_input_v6.py` 가 팀원 신규열을 통합해 만든 파일).
`ing_ghs_indep_{ep}_cat/_tier` 가 런타임 파싱을 대체한다 — 값은 정의상 동등하다
(통합 시 동일한 parse_cy 로 만들었고 CAS 조회 결과를 그대로 컬럼화했다).
**이 리팩터로 수치가 바뀌면 결함이다.** 스크립트 말미에서 기존 산출과 대조한다.

읽기 전용. 산출은 v6_jurisdiction/ 에 추가.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                             confusion_matrix, f1_score, matthews_corrcoef,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path("/Users/hanseoyun/Desktop/260830")
SRC = ROOT / "04_모델산출물" / "v4_fixed"
V6 = ROOT / "04_모델산출물" / "input_dataset_v6.xlsx"   # 통합 단일 입력
OUT = ROOT / "04_모델산출물" / "v6_jurisdiction"
SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
NEGREC = "sds_v1_negative_recovered"
_t0 = time.time()
_LOGF = open(OUT / "run_full_metrics.log", "w", encoding="utf-8")


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
assert (X["Formulation_ID"].values == Y["Formulation_ID"].values).all()
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
# 통합본이 v5 와 같은 행집합·같은 식별열을 갖는지 확인(리팩터 안전성)
assert len(FORM_I) == 1675 and len(ING) == 5287, "v6 행수 이탈 — 통합본 손상"
for _c in ("ing_ghs_indep_eye_cat", "ing_ghs_indep_skin_cat",
           "ing_ghs_indep_sens_cat", "ing_ghs_indep_eye_tier"):
    assert _c in ING.columns, f"v6 통합열 {_c} 없음 — build_input_v6.py 를 먼저 실행"
EPS = ("eye", "skin", "sens")
RAW = {ep: Y[f"y_{ep}"].fillna("").astype(str).to_numpy() for ep in EPS}
SRCD = {ep: Y[f"y_{ep}_src_detail"].fillna("").astype(str).to_numpy() for ep in EPS}

# ---- L1 정본 / L2 투영 / 마스크 (앞선 산출과 동일 — assert 로 고정) ----------
CANON = {ep: RAW[ep].copy() for ep in EPS}
_fix = 0
for i, v in enumerate(CANON["skin"]):
    if v in ("2A", "2B"):
        CANON["skin"][i] = "2"
        _fix += 1
assert _fix == 82, f"L1 정정 {_fix} != 82"
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
assert {ep: int(IS_NEGREC[ep].sum()) for ep in EPS} == {"eye": 109, "skin": 128,
                                                        "sens": 211}


def project(jn, ep):
    tab = JUR[jn][ep]
    b = np.full(N, -1, dtype=int)
    for i, v in enumerate(CANON[ep]):
        if v == "":
            continue
        assert tab.get(v) is not None, f"{jn}/{ep} 투영표에 '{v}' 없음"
        b[i] = tab[v]
    keep = b >= 0
    if ep == "skin" and tab.get("3") == 1:
        keep = keep & ~EPA_SKIN4
    if ep == "eye":
        keep = keep & ~IS_NEGREC["eye"]          # 현행: negrec 마스크 적용
    return b, keep


LAYER = {(jn, ep): project(jn, ep) for jn in JUR for ep in EPS}
_EXP = {("eye", "UN_GHS"): 1044, ("eye", "EU_CLP"): 1044,
        ("skin", "UN_GHS"): 615, ("skin", "EU_CLP"): 1059,
        ("sens", "UN_GHS"): 716}
for (ep, jn), n in _EXP.items():
    got = int(LAYER[(jn, ep)][1].sum())
    assert got == n, f"{jn}/{ep} 행수 {got} != {n}"
log("L1/L2/마스크 재현 확인 — eye 1044(마스크 적용), skin 615/1059, sens 716")

# ==================================================================== CT
CAT1 = {"1", "1A", "1B", "1C"}
CAT2 = {"2", "2A", "2B"}
SEV_CT = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
          "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1,
                   "2A": 2, "2B": 1, "NC": 0},
          "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}
# 정채윤 성분 GHS 조사는 이제 통합본의 컬럼에서 읽는다(런타임 파싱 제거).
# build_input_v6.py 가 동일한 parse_cy 로 만들어 CAS 조회 결과를 컬럼화했으므로
# 이전 판의 `CY_MAP.get(cas)` 와 값이 동등하다. 재현 lock 은 아래 assert.
# 세 수를 구분해서 잠근다(내가 앞서 이 셋을 혼동해 assert 를 잘못 썼다):
#   331  = 조사 원문이 기입된 CAS 대표행 == 고유 조사 CAS 수
#   1550 = 그 331 CAS 를 성분으로 갖는 성분행 (CAS 조회 매칭 범위)
#   1355 = 그 중 파싱해 범주가 나온 성분행. 차 195행은 `정보없음` CAS 60종에서 온다
#          — parse_cy 가 (None,None,None) 을 주므로 cat·tier 모두 결측. 정보 손실이
#          아니라 "조사했으나 정보 없음"의 정직한 표현이다.
_cysrc = ING["GHS조사_눈"].notna()
assert int(_cysrc.sum()) == 331, f"정채윤 조사 대표행 {int(_cysrc.sum())} != 331"
_CY_CAS = set(ING.loc[_cysrc, "cas"].astype(str).str.strip())
assert len(_CY_CAS) == 331, f"정채윤 고유 CAS {len(_CY_CAS)} != 331 — 통합본 드리프트"
_cymatch = int(ING["cas"].astype(str).str.strip().isin(_CY_CAS).sum())
assert _cymatch == 1550, f"정채윤 CAS 매칭 성분행 {_cymatch} != 1550"
_cyparsed = int(ING["ing_ghs_indep_eye_cat"].notna().sum())
assert _cyparsed == 1355, f"정채윤 파싱성공 성분행 {_cyparsed} != 1355"
log(f"정채윤 통합열 확인 — 조사 CAS 331 / 매칭 {_cymatch}행 / 파싱성공 {_cyparsed}행 "
    f"(차 {_cymatch-_cyparsed}행 = 정보없음 CAS 60종)")


def cy_of(row, ep):
    """통합본 행에서 (범주, 출처등급) 을 읽는다. 미조사면 (None, None)."""
    c = row[f"ing_ghs_indep_{ep}_cat"]
    t = row[f"ing_ghs_indep_{ep}_tier"]
    c = None if (c is None or (isinstance(c, float) and math.isnan(c))
                 or str(c) in ("nan", "None", "")) else str(c)
    t = None if (t is None or (isinstance(t, float) and math.isnan(t))
                 or str(t) in ("nan", "None", "")) else str(t)
    return c, t


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
            + [f"ing_ghs_indep_{ep}_{k}" for ep in EPS for k in ("cat", "tier")]].copy()
ING_G["cas"] = ING_G["cas"].astype(str).str.strip()


def build_ct(arm_by_ep):
    rows = []
    for fid, g in ING_G.groupby("Formulation_ID", sort=False):
        n = len(g)
        row = {"Formulation_ID": fid}
        for ep in EPS:
            arm = arm_by_ep[ep]
            cats, pcts = [], []
            for _, r in g.iterrows():
                base = r[f"ing_ghs_{ep}"]
                base = None if (base is None
                                or (isinstance(base, float) and math.isnan(base))
                                or str(base).lower() in ("nan", "none", "")) else str(base)
                cat = base
                if arm != "A0_base" and cat is None:
                    c, tier = cy_of(r, ep)
                    if c is not None:
                        if arm == "A2_aug_nc_unknown" and c == "NC":
                            c = None
                        if arm == "A3_aug_annexvi" and tier != "annex_vi":
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
            row[f"f_ct_{ep}_coverage"] = (ct["n_known"] / n) if n else None
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
CT0 = build_ct({ep: "A0_base" for ep in EPS})
X_A0 = ct_to_X(X0, CT0)
_mism = [c for c in CT_COLS if not np.allclose(
    pd.to_numeric(X_A0[c], errors="coerce").fillna(-999),
    pd.to_numeric(X0[c], errors="coerce").fillna(-999))]
assert not _mism, f"A0 재현 실패: {_mism[:5]}"
CT_REC = build_ct({"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown",
                   "sens": "A3_aug_annexvi"})
X_REC = ct_to_X(X0, CT_REC)
_COV = {("A0", "eye"): 0.240, ("A0", "skin"): 0.241, ("A0", "sens"): 0.241,
        ("REC", "eye"): 0.330, ("REC", "skin"): 0.296, ("REC", "sens"): 0.496}
for tag, ct in (("A0", CT0), ("REC", CT_REC)):
    for ep in EPS:
        got = float(ct[f"f_ct_{ep}_coverage"].mean())
        assert abs(got - _COV[(tag, ep)]) < 0.002, f"{tag}/{ep} coverage {got:.3f} 이탈"
log("CT A0 == 디스크 / 커버리지 == 검증값 — 전처리 드리프트 없음")


def as_mat(df, im):
    M = df[CHEM].to_numpy(dtype=np.float64)
    return np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0) if im == "nan0" else M


XM = {(ct, im): as_mat(df, im)
      for ct, df in (("CT_A0", X_A0), ("CT_권고", X_REC))
      for im in ("nan0", "native")}


# ==================================================================== 지표
def all_metrics(y, p):
    """임계값은 정규 베이스라인과 동일하게 0.5 고정."""
    pred = (p >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "roc_auc": roc_auc_score(y, p),
        "pr_auc": average_precision_score(y, p),
        "f1_pos": f1_score(y, pred, pos_label=1, zero_division=0),
        "f1_neg": f1_score(y, pred, pos_label=0, zero_division=0),
        "f1_macro": f1_score(y, pred, average="macro", zero_division=0),
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "specificity": tn / (tn + fp) if (tn + fp) else float("nan"),
        "mcc": matthews_corrcoef(y, pred), "ba": balanced_accuracy_score(y, pred),
        "accuracy": (tp + tn) / len(y),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def f1_best(y, p):
    """참고용 상한 — OOF 에서 후향적으로 고른 값이므로 성능 주장에 쓸 수 없다."""
    ths = np.unique(np.round(p, 3))
    best = max(((f1_score(y, (p >= t).astype(int), zero_division=0), t) for t in ths),
               key=lambda x: x[0])
    return best


def make_folds(y, g):
    return [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True,
                                      random_state=sd).split(np.zeros((len(y), 1)), y, g))
            for sd in SEEDS]


# 관할 프로파일 중복 계산 방지 — 라벨이 동일한 관할끼리 묶는다
GROUPS = []
for ep in EPS:
    seen = {}
    for jn in JUR:
        b, k = LAYER[(jn, ep)]
        key = (b[k].tobytes(), k.tobytes())
        seen.setdefault(key, []).append(jn)
    for key, jns in seen.items():
        GROUPS.append((ep, jns))
log("계산 단위: " + "; ".join(f"{ep}:{'+'.join(j)}" for ep, j in GROUPS))

RES = []
for ep, jns in GROUPS:
    b, k = LAYER[(jns[0], ep)]
    y = b[k]
    folds = make_folds(y, GK[k])          # CT arm·규약 간 고정
    for im in ("nan0", "native"):
        for xn in ("CT_A0", "CT_권고"):
            Xi = XM[(xn, im)][k]
            per, oof_last = [], None
            for si, sd in enumerate(SEEDS):
                p = np.full(len(y), np.nan)
                for tr, te in folds[si]:
                    p[te] = rf(sd).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
                assert not np.isnan(p).any()
                per.append(all_metrics(y, p))
                oof_last = p
            fb, tb = f1_best(y, oof_last)
            row = {"endpoint": ep, "관할": "+".join(jns), "결측처리": im, "CT": xn,
                   "n": int(len(y)), "양성": int(y.sum()),
                   "유병률": round(float(y.mean()), 4)}
            for kk in per[0]:
                v = [d[kk] for d in per]
                if kk in ("tp", "fp", "fn", "tn"):
                    row[kk] = round(float(np.mean(v)), 1)
                else:
                    row[kk.upper() if kk in ("mcc", "ba") else kk] = \
                        round(float(np.mean(v)), 4)
                    row[(kk.upper() if kk in ("mcc", "ba") else kk) + "_sd"] = \
                        round(float(np.std(v, ddof=1)), 4)
            # 이 두 열만 5시드 평균이 아니라 **마지막 시드(seed=4) 단독**이다
            # (oof_last). 열 이름에 seed4 를 박아 다른 열과 짝지어 비교하는 것을
            # 막는다 — critic M8.
            row["F1_최적임계값_참고_seed4"] = round(float(fb), 4)
            row["최적임계값_참고_seed4"] = round(float(tb), 3)
            RES.append(row)
            log(f"  {ep:4} {row['관할']:26} [{im:6}] {xn:7} n={row['n']:5} "
                f"p={row['유병률']:.3f} AUC={row['roc_auc']:.4f} "
                f"F1={row['f1_pos']:.4f} PR-AUC={row['pr_auc']:.4f} "
                f"MCC={row['MCC']:.3f} 재현율={row['recall']:.3f} "
                f"정밀도={row['precision']:.3f}")

R = pd.DataFrame(RES)

# ---- 리팩터 재현 lock -------------------------------------------------------
# 입력을 v5+팀원파일 → v6 통합본으로 바꾼 것은 순수 리팩터다. 기존 산출이 있으면
# 전 지표가 동일해야 한다. 다르면 통합 과정에 결함이 있다는 뜻이므로 즉시 중단한다.
_prev = OUT / "전체지표_ROC_F1.csv"
if _prev.exists():
    _o = pd.read_csv(_prev)
    _key = ["endpoint", "관할", "결측처리", "CT"]
    _num = [c for c in _o.columns if c not in _key
            and pd.api.types.is_numeric_dtype(_o[c])]
    _m = _o.merge(R, on=_key, suffixes=("_old", "_new"), how="outer", indicator=True)
    assert (_m["_merge"] == "both").all(), "리팩터 후 측정 셀 구성이 달라졌다"
    _bad, _skip = [], []
    for c in _num:
        if f"{c}_old" in _m and f"{c}_new" in _m:
            d = (_m[f"{c}_old"] - _m[f"{c}_new"]).abs().max()
            if pd.notna(d) and d > 1e-9:
                _bad.append(f"{c}(최대차 {d:.3e})")
        else:
            _skip.append(c)          # 열 이름이 바뀌어 대조 못 한 것 — 조용히 넘기지 않는다
    assert not _bad, ("v6 통합 리팩터가 수치를 바꿨다 — 결함: " + ", ".join(_bad))
    log(f"재현 lock 통과: 기존 산출과 {len(_num)-len(_skip)}개 지표 × {len(_m)}셀 "
        f"전부 동일 (허용오차 1e-9)")
    if _skip:
        log(f"  ※ 대조 못 한 열 {len(_skip)}개(이름 변경): {', '.join(_skip)}")
else:
    log("재현 lock: 기존 산출 없음 — 대조 생략")

COLS = ["endpoint", "관할", "결측처리", "CT", "n", "양성", "유병률",
        "roc_auc", "roc_auc_sd", "pr_auc", "pr_auc_sd",
        "f1_pos", "f1_pos_sd", "f1_neg", "f1_macro",
        "precision", "recall", "specificity", "MCC", "BA", "accuracy",
        "tp", "fp", "fn", "tn", "F1_최적임계값_참고_seed4",
        "최적임계값_참고_seed4"]
R = R[[c for c in COLS if c in R.columns]]
R.to_csv(OUT / "전체지표_ROC_F1.csv", index=False, encoding="utf-8-sig")

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "전체지표"
ws.append(list(R.columns))
for _, r in R.iterrows():
    ws.append(["" if v is None else v for v in r.tolist()])
ws.freeze_panes = "H2"
ws.auto_filter.ref = ws.dimensions
wb.save(OUT / "전체지표_ROC_F1.xlsx")

with open(OUT / "전체지표_요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "대상": "현행 v6 상태 (eye negrec 109행 마스크 적용, n=1044)",
        "임계값": "0.5 고정 — 정규 베이스라인과 동일. F1 을 좋게 보이려는 조정 없음",
        "F1_최적임계값_참고_seed4": "OOF 에서 후향적으로 고른 낙관적 상한. 성능 주장에 쓸 수 없다. 또한 이 열만 5시드 평균이 아니라 마지막 시드(seed=4) 단독 값이므로 다른 열과 짝지어 비교할 수 없다",
        "임계값_부적정_경고": [
            "0.5 는 이 데이터에서 최적이 아니다. BA(균형정확도)가 전 셀에서 "
            "0.59~0.71 로 ROC-AUC 보다 뚜렷이 낮고, OOF 후향 최적 임계값은 "
            "전 셀에서 0.5 미만(0.298~0.400)이다 — 모델 확률이 낮은 쪽으로 "
            "압축되어 있다는 뜻이다.",
            "그럼에도 0.5 를 유지한 이유: 임계값은 과소분류(미분류 독성물질 출하) "
            "대 과대분류의 비용비를 총책임자가 정한 뒤, 평가 폴드가 아니라 "
            "**학습 폴드 내부에서만** 결정해야 한다. 평가에 쓴 OOF 에서 고른 "
            "임계값을 성능으로 보고하면 낙관 편향이 들어간다.",
            "따라서 이 파일의 F1·정밀도·재현율·특이도·MCC·정확도·혼동행렬은 "
            "모두 '임계값 0.5 조건부' 값이며, 임계값을 정하면 달라진다. "
            "임계값에 불변인 지표는 ROC-AUC 와 PR-AUC 뿐이다.",
        ],
        "F1_해석_경고": [
            "F1 은 진음성(TN)을 무시하므로 유병률이 다른 관할 간 비교가 불가하다. "
            "유병률이 높으면 F1 이 자동으로 커진다 — 눈 UN형(p=0.683)의 높은 F1 은 "
            "성능이 아니라 유병률의 결과다.",
            "class_weight='balanced' 를 쓰므로 모델이 양성 쪽으로 기울어 재현율이 "
            "정밀도보다 높게 나온다. F1 은 그 편향을 관대하게 평가한다.",
            "관할·엔드포인트 간 비교에는 ROC-AUC 와 MCC 를 쓰는 것이 맞다.",
        ],
        "행": RES,
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT}/전체지표_ROC_F1.csv / .xlsx")
