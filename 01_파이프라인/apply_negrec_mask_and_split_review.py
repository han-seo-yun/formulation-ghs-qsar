#!/usr/bin/env python3
"""눈 negrec 109행 학습 마스크 적용 + 수동검토/최종판단 작업파일 분리.

총책임자 지시
------------
(1) 눈 negrec 109셀을 학습에서 마스크한다.
(2) '부분확인'이라 수동 검토·최종 판단이 필요한 부분을 작업파일로 별도 분리한다.

(1) 의 근거와 범위
-----------------
M3 교차전이에서 눈 negrec 의 위치지수가 0.596(nan0)/0.602(native)였다 — 다른 출처의
음성보다 양성 쪽에 더 가깝고, 양성 예측률이 40.7% 대 20.2% 로 2.0배다. 두 결측 규약에서
값이 같아 규약 인공물도 아니다. 따라서 '정보 없이 유병률만 왜곡하는 행'으로 보고 학습에서
제외한다. **눈만** 마스크한다 — 피부(0.36–0.38)는 근거가 약하고 감작은 규약에 따라
0.412↔0.157 로 흔들려 판정 유보다. 피부·감작 negrec 는 마스크하지 않고 **플래그만** 달아
나중에 결정할 수 있게 남긴다.

마스크는 라벨 값을 지우는 것이 아니다. L0 원본·L1 정본·L2 관할범주는 그대로 두고
`y_eye_usable__{관할}` 만 0 으로 내린다. 되돌릴 수 있어야 한다.

측정 설계 — 마스크 전후를 그냥 비교하면 안 된다
---------------------------------------------
마스크는 행 수를 1153 → 1044 로 줄이므로 AUC 를 직접 비교하면 다른 문제의 비교가 된다.
그래서 둘로 나눈다.
  M4 동일평가행 : 평가는 항상 신뢰되는 1044행에서 하고, 학습에만 negrec 를 넣거나 뺀다.
                 폴드는 1044행에서 1회 산출해 두 조건 간 고정. 이것이 마스크의 순효과다.
                 (그룹 누설 차단 — negrec 행은 그 group_key 가 학습 폴드에 있을 때만 추가)
  M5 마스크 후  : 마스크된 레이어의 실제 성능. n·유병률이 바뀌므로 **1153행 수치와
                 직접 비교 불가**임을 산출물에 명시한다.
눈의 4 관할은 라벨 프로파일이 2종(UN형 = UN/K_REACH/US_OSHA, EU형)뿐이므로 프로파일
단위로 계산하고 관할 4행으로 펼친다(중복 계산 방지 — 값은 동일해야 한다).

무수정 원칙: 입력 전부 읽기 전용. 기존 산출물 덮어쓰지 않는다. CT 공식 변경 없음.
결측 규약은 nan0(정규 베이스라인, 주) / native(병기) 양쪽 다 낸다.
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

import v6_integrated as v6
from openpyxl.styles import Alignment, Font, PatternFill
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (balanced_accuracy_score, matthews_corrcoef,
                             roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04_모델산출물" / "v4_fixed"
V6 = v6.V6                                    # 통합 단일 입력(읽기 전용)
AUDIT = (ROOT / "formulation_audit_team_share_highlighted_only_20260630"
         / "ingredient_source_audit" / "source_manifest.csv")
JUR_OUT = ROOT / "04_모델산출물" / "v6_jurisdiction"
REV_OUT = ROOT / "04_모델산출물" / "v6_수동검토"       # 작업파일 별도 분리 위치
REV_OUT.mkdir(parents=True, exist_ok=True)

SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
NEGREC = "sds_v1_negative_recovered"
_t0 = time.time()
_LOGF = open(REV_OUT / "run.log", "w", encoding="utf-8")


def log(msg: str) -> None:
    line = f"[{time.time()-_t0:7.1f}s] {msg}"
    print(line, flush=True)
    _LOGF.write(line + "\n")
    _LOGF.flush()


def rf(seed: int) -> RandomForestClassifier:
    """정규 베이스라인 — measure_v5_performance.py 와 동일. 변경 금지."""
    return RandomForestClassifier(
        n_estimators=300, class_weight="balanced", min_samples_leaf=2,
        n_jobs=-1, random_state=seed)


# ==================================================================== 로드
log("데이터 로드")
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
assert len(CT_COLS) == 26, f"CT 열 수 {len(CT_COLS)} != 26"

xl = pd.ExcelFile(V6)
FORM = xl.parse("formulation")
ING = xl.parse("ingredient")
FORM_I = FORM.set_index("Formulation_ID")

EPS = ("eye", "skin", "sens")
RAW = {ep: Y[f"y_{ep}"].fillna("").astype(str).to_numpy() for ep in EPS}
SRCD = {ep: Y[f"y_{ep}_src_detail"].fillna("").astype(str).to_numpy() for ep in EPS}
RAW_SNAP = {ep: RAW[ep].copy() for ep in EPS}

# ==================================================================== L1 정본
VALID = {"eye": {"1", "2", "2A", "2B", "NC"},
         "skin": {"1", "1A", "1B", "1C", "2", "3", "NC"},
         "sens": {"1", "1A", "1B", "NC"}}
CANON_FIX = {
    ("skin", "2A"): ("2", "2A 는 눈 전용 범주. 출처 EPA 피부 II(72h 심한 자극) → "
                          "GHS Skin Irrit. 2. 어느 관할에도 피부 2A 는 없다"),
    ("skin", "2B"): ("2", "2B 는 눈 전용 범주. 출처 EPA 피부 III(72h 중등도 자극) → "
                          "GHS Skin Irrit. 2. 어느 관할에도 피부 2B 는 없다"),
}
CANON, fixlog = {}, []
for ep in EPS:
    out = RAW[ep].copy()
    for i, v in enumerate(out):
        if v and (ep, v) in CANON_FIX:
            new, why = CANON_FIX[(ep, v)]
            out[i] = new
            fixlog.append({"Formulation_ID": FID[i], "endpoint": ep, "원본": v,
                           "정본": new, "정정근거": why, "출처": SRCD[ep][i]})
    CANON[ep] = out
    bad = sorted({v for v in out if v not in VALID[ep] and v != ""})
    assert not bad, f"{ep} 정본에 GHS 무효 범주 잔존: {bad}"
FIXLOG = pd.DataFrame(fixlog)
assert len(FIXLOG) == 82, f"L1 정정 {len(FIXLOG)} != 82 — 앞선 산출과 불일치"
for ep in EPS:
    assert (RAW[ep] == RAW_SNAP[ep]).all(), f"L0 y_{ep} 오염"
log(f"L1 정본 재현 확인 (정정 {len(FIXLOG)}셀), L0 불변 검증 통과")

# ==================================================================== L2 관할 투영
_UN_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 1, "NC": 0}
_EU_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 0, "NC": 0}
_UN_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 1, "NC": 0}
_EU_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 0, "NC": 0}
_SENS = {"1": 1, "1A": 1, "1B": 1, "NC": 0}
JUR = {
    "UN_GHS":  {"eye": _UN_EYE, "skin": _UN_SKIN, "sens": _SENS, "확인상태": "확인",
                "근거": "UN GHS Rev.10 3.3(눈 2A/2B 선택범주 포함), "
                      "3.2(피부 Cat 3 선택범주 포함)"},
    "EU_CLP":  {"eye": _EU_EYE, "skin": _EU_SKIN, "sens": _SENS, "확인상태": "확인",
                "근거": "규칙 (EC) 1272/2008 부속서 I — Eye Irrit. 2 단일범주"
                      "(2B/H320 없음), Skin Irrit. 2 단일범주(Cat 3/H316 없음)"},
    "K_REACH": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS, "확인상태": "부분확인",
                "근거": "고용노동부 고시 화학물질 분류·표시 — 눈 자극성 2A/2B 세분 채택, "
                      "피부 자극성 구분 2 단일(구분 3 미채택). **고시 원문 대조 미완**"},
    "US_OSHA": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS, "확인상태": "부분확인",
                "근거": "OSHA HCS 2012 부속서 A.3(눈 2A/2B), A.2(피부 Cat 2, "
                      "Cat 3 미채택). **원문 대조 미완**"},
}
EPA_SKIN4 = (FORM_I["ntp_epa_skin_cat"].reindex(FID).astype(float) == 4).to_numpy()
assert int(EPA_SKIN4.sum()) == 444, f"EPA 피부 IV {int(EPA_SKIN4.sum())} != 444"


def project(jname: str, ep: str):
    tab = JUR[jname][ep]
    cat = np.array(["" for _ in range(N)], dtype=object)
    bin_ = np.full(N, -1, dtype=int)
    for i, v in enumerate(CANON[ep]):
        if v == "":
            continue
        b = tab.get(v)
        assert b is not None, f"{jname}/{ep} 투영표에 정본범주 '{v}' 없음"
        bin_[i] = b
        cat[i] = v if b == 1 else "Not classified"
    keep = bin_ >= 0
    if ep == "skin" and tab.get("3") == 1:
        keep = keep & ~EPA_SKIN4
        cat[EPA_SKIN4] = "판별불가(EPA IV ↔ Cat3/무범주 경계)"
    return cat, bin_, keep


LAYER = {(jn, ep): project(jn, ep) for jn in JUR for ep in EPS}
_EXPECT_N = {("eye", "UN_GHS"): 1153, ("eye", "EU_CLP"): 1153,
             ("skin", "UN_GHS"): 615, ("skin", "EU_CLP"): 1059,
             ("sens", "UN_GHS"): 716}
for (ep, jn), n in _EXPECT_N.items():
    got = int(LAYER[(jn, ep)][2].sum())
    assert got == n, f"{jn}/{ep} 행수 {got} != {n} — 앞선 산출과 불일치"
log("L2 관할 투영 재현 확인 (행수가 앞선 산출과 일치)")

# ==================================================================== 마스크
# negrec 플래그 — 세 엔드포인트 전부 계산하되 마스크는 눈만 적용한다
IS_NEGREC = {ep: (SRCD[ep] == NEGREC) for ep in EPS}
_NR = {ep: int(IS_NEGREC[ep].sum()) for ep in EPS}
assert _NR == {"eye": 109, "skin": 128, "sens": 211}, f"negrec 행수 이탈: {_NR}"
for ep in EPS:
    b = LAYER[("EU_CLP", ep)][1]
    assert (b[IS_NEGREC[ep]] == 0).all(), f"{ep} negrec 에 양성 혼입 — 전제 불성립"
log(f"negrec: {_NR} (전부 음성 확인). 마스크 적용 = eye 만")

MASKED = {}          # (관할, ep) -> keep 마스크 적용판
for (jn, ep), (c, b, k) in LAYER.items():
    MASKED[(jn, ep)] = (k & ~IS_NEGREC[ep]) if ep == "eye" else k
for jn in JUR:
    log(f"  {jn:8}/eye 사용가능 {int(LAYER[(jn,'eye')][2].sum())} → "
        f"{int(MASKED[(jn,'eye')].sum())} (유병률 "
        f"{LAYER[(jn,'eye')][1][LAYER[(jn,'eye')][2]].mean():.3f} → "
        f"{LAYER[(jn,'eye')][1][MASKED[(jn,'eye')]].mean():.3f})")

# ---- 마스크 적용 라벨 레이어 산출 ------------------------------------------
LAB = pd.DataFrame({"Formulation_ID": FID, "group_key": GK})
for ep in EPS:
    LAB[f"y_{ep}__L0_raw"] = np.where(RAW[ep] == "", None, RAW[ep])
    LAB[f"y_{ep}__L1_canon"] = np.where(CANON[ep] == "", None, CANON[ep])
    LAB[f"y_{ep}__L1_fixed"] = (RAW[ep] != CANON[ep]).astype(int)
    LAB[f"y_{ep}__negrec"] = IS_NEGREC[ep].astype(int)
    # 마스크 사유는 행 단위로 남긴다 — 되돌릴 수 있어야 한다
    if ep == "eye":
        LAB["y_eye__mask_reason"] = np.where(
            IS_NEGREC["eye"], "negrec_교차전이_위치지수0.60_학습제외", "")
    for jn in JUR:
        c, b, k = LAYER[(jn, ep)]
        km = MASKED[(jn, ep)]
        LAB[f"y_{ep}_cat__{jn}"] = np.where(c == "", None, c)
        LAB[f"y_{ep}_bin__{jn}"] = np.where(k, b, None)      # 라벨값은 보존
        LAB[f"y_{ep}_usable__{jn}"] = km.astype(int)          # 마스크는 여기만
        LAB[f"y_{ep}_usable_premask__{jn}"] = k.astype(int)
LAB.to_csv(JUR_OUT / "라벨레이어_관할별_negrec마스크.csv", index=False,
           encoding="utf-8-sig")
log(f"→ {JUR_OUT}/라벨레이어_관할별_negrec마스크.csv")

# ==================================================================== CT
CAT1 = {"1", "1A", "1B", "1C"}
CAT2 = {"2", "2A", "2B"}
SEV_CT = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
          "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1,
                   "2A": 2, "2B": 1, "NC": 0},
          "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}
# 정채윤 조사는 통합본 컬럼에서 읽는다 — 파싱 로직을 스크립트마다 복제하지 않는다.
# (그 복제가 §0 결함 1 을 낳았다: 331 필터·중복제거 누락으로 591행이 들어가
#  중복 CAS 의 마지막 행이 앞 행을 덮어씀.) v6_integrated.cy_map 이 동일한 필터·
#  중복제거·불변 assert(331/1550/1355)를 한 곳에서 수행한다. 값은 정의상 동등하며,
#  바뀌면 결함이므로 아래 재현 대조로 확인한다.
CY_MAP = v6.cy_map(ING)
log(f"정채윤 조사 CAS={len(CY_MAP)} (통합본 컬럼, 파싱 복제 제거)")


def ct_predict(pairs, endpoint):
    """lib_parse.ct_predict 와 동일 로직. 공식 변경 없음."""
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
        return {"cat": None, "s1": None, "s2": None, "s3": None, "add": None,
                "n_known": 0}
    add = 10 * s1 + s2
    if endpoint == "eye":
        cat = "1" if s1 >= 3 else ("2A" if (s1 >= 1 or s2 >= 10 or add >= 10) else "NC")
    elif endpoint == "skin":
        cat = ("1" if s1 >= 5 else "2" if (s1 >= 1 or s2 >= 10 or add >= 10)
               else "3" if s3 >= 20 else "NC")
    else:
        cat = "1" if (s1 >= 1.0 or s1a >= 0.1) else "NC"
    return {"cat": cat, "s1": s1, "s2": s2, "s3": s3, "add": add, "n_known": n}


ING_G = ING[["Formulation_ID", "cas", "ing_pct_best",
             "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens"]].copy()
ING_G["cas"] = ING_G["cas"].astype(str).str.strip()


def build_ct(arm_by_ep: dict) -> pd.DataFrame:
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
                    cy = CY_MAP.get(r["cas"])
                    if cy is not None:
                        c, tier = cy[ep]        # pct 는 통보자 신고농도로 CT 에 쓰이지 않는다
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
            for kk in ("s1", "s2", "add", "n_known"):
                row[f"f_ct_{ep}_{kk}"] = ct[kk]
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
assert not _mism, f"A0 재현 실패 — 불일치 {_mism[:5]}"
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


def as_mat(df, impute):
    M = df[CHEM].to_numpy(dtype=np.float64)
    return np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0) if impute == "nan0" else M


XM = {(ct, im): as_mat(df, im)
      for ct, df in (("CT_A0", X_A0), ("CT_권고", X_REC))
      for im in ("nan0", "native")}
IMPUTES = ["nan0", "native"]


# ==================================================================== CV
def make_folds(y, g):
    return [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True,
                                      random_state=sd).split(np.zeros((len(y), 1)), y, g))
            for sd in SEEDS]


def metrics(y, proba):
    pred = (proba >= 0.5).astype(int)
    return {"roc_auc": roc_auc_score(y, proba), "mcc": matthews_corrcoef(y, pred),
            "ba": balanced_accuracy_score(y, pred)}


def run_cv(Xi, y, folds):
    per = []
    for si in range(len(SEEDS)):
        proba = np.full(len(y), np.nan)
        for tr, te in folds[si]:
            proba[te] = rf(SEEDS[si]).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
        assert not np.isnan(proba).any(), "OOF 미충족"
        per.append(metrics(y, proba))
    out = {"_per_seed": per}
    for k in per[0]:
        v = [d[k] for d in per]
        out[k], out[k + "_sd"] = float(np.mean(v)), float(np.std(v, ddof=1))
    out.update(n=int(len(y)), prevalence=float(y.mean()))
    return out


def paired(a, b, key="roc_auc"):
    d = np.array([x[key] - y[key] for x, y in zip(a["_per_seed"], b["_per_seed"])])
    sd = float(np.std(d, ddof=1))
    return {"delta": float(d.mean()), "sd_paired": sd,
            "t_df4": float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else None,
            "시드양수": int((d > 0).sum())}


# 눈의 관할 프로파일은 2종뿐 — 프로파일 단위로 계산하고 관할로 펼친다
PROFILE = {"UN형(UN_GHS/K_REACH/US_OSHA)": ["UN_GHS", "K_REACH", "US_OSHA"],
           "EU형(EU_CLP)": ["EU_CLP"]}
for pname, jns in PROFILE.items():
    ref = LAYER[(jns[0], "eye")][1]
    for jn in jns[1:]:
        assert (LAYER[(jn, "eye")][1] == ref).all(), f"{pname} 프로파일 동일성 위반"
log("눈 관할 프로파일 2종 동일성 검증 통과 — 프로파일 단위 계산 후 관할 확장")

RES = []

# ---- M4 동일평가행: 학습에 negrec 포함 vs 제외 ------------------------------
log("=" * 66)
log("M4 동일평가행 — 평가는 항상 신뢰 1044행, 학습에만 negrec 포함/제외")
M4 = {}
for pname, jns in PROFILE.items():
    jn0 = jns[0]
    c, b, kfull = LAYER[(jn0, "eye")]
    isneg = IS_NEGREC["eye"] & kfull
    ev = kfull & ~isneg                      # 평가·기본학습 행집합 (1044)
    yev = b[ev]
    folds = make_folds(yev, GK[ev])           # 1회 산출 → 두 조건 간 고정
    idx_neg = np.where(isneg)[0]
    for im in IMPUTES:
        for xn in ("CT_A0", "CT_권고"):
            Xi = XM[(xn, im)]
            Xe = Xi[ev]
            r_out, r_in = [], []
            for si in range(len(SEEDS)):
                sd = SEEDS[si]
                p_out = np.full(len(yev), np.nan)
                p_in = np.full(len(yev), np.nan)
                for tr, te in folds[si]:
                    p_out[te] = rf(sd).fit(Xe[tr], yev[tr]).predict_proba(Xe[te])[:, 1]
                    # negrec 는 그 group_key 가 학습 폴드에 있을 때만 추가 (누설 차단)
                    trg = set(GK[ev][tr])
                    add = idx_neg[np.isin(GK[isneg], list(trg))]
                    Xtr = np.vstack([Xe[tr], Xi[add]]) if len(add) else Xe[tr]
                    ytr = (np.concatenate([yev[tr], b[add]]) if len(add) else yev[tr])
                    p_in[te] = rf(sd).fit(Xtr, ytr).predict_proba(Xe[te])[:, 1]
                r_out.append(metrics(yev, p_out))
                r_in.append(metrics(yev, p_in))
            A = {"_per_seed": r_out}
            B = {"_per_seed": r_in}
            d = paired(A, B)                  # 마스크(제외) − 포함
            for jn in jns:
                RES.append({
                    "측정": "M4_동일평가행", "프로파일": pname, "관할": jn,
                    "결측처리": im, "CT": xn, "평가행_n": int(ev.sum()),
                    "negrec_학습추가_n": int(isneg.sum()),
                    "유병률": round(float(yev.mean()), 4),
                    "AUC_negrec포함학습": round(float(np.mean(
                        [x["roc_auc"] for x in r_in])), 4),
                    "AUC_negrec마스크": round(float(np.mean(
                        [x["roc_auc"] for x in r_out])), 4),
                    "ΔAUC_마스크효과": round(d["delta"], 4),
                    "sd_paired": round(d["sd_paired"], 4),
                    "t_df4": round(d["t_df4"], 2) if d["t_df4"] else None,
                    "시드양수": d["시드양수"],
                    "MCC_마스크": round(float(np.mean([x["mcc"] for x in r_out])), 4),
                    "MCC_포함": round(float(np.mean([x["mcc"] for x in r_in])), 4)})
            M4[(pname, im, xn)] = RES[-1]
            log(f"  {pname:26} [{im:6}] {xn:7} 포함={RES[-1]['AUC_negrec포함학습']:.4f} "
                f"마스크={RES[-1]['AUC_negrec마스크']:.4f} "
                f"Δ={d['delta']:+.4f} t={RES[-1]['t_df4']} 시드양수={d['시드양수']}/5")

# ---- M5 마스크 후 최종 성능 (1153행 수치와 직접 비교 불가) -------------------
log("=" * 66)
log("M5 마스크 후 최종 성능 — n·유병률이 바뀌므로 1153행 수치와 직접 비교 불가")
for pname, jns in PROFILE.items():
    jn0 = jns[0]
    c, b, kfull = LAYER[(jn0, "eye")]
    km = MASKED[(jn0, "eye")]
    y = b[km]
    fo = make_folds(y, GK[km])
    for im in IMPUTES:
        b0 = None
        for xn in ("CT_A0", "CT_권고"):
            r = run_cv(XM[(xn, im)][km], y, fo)
            d = paired(r, b0) if b0 is not None else {}
            if b0 is None:
                b0 = r
            for jn in jns:
                RES.append({
                    "측정": "M5_마스크후_최종", "프로파일": pname, "관할": jn,
                    "결측처리": im, "CT": xn, "평가행_n": r["n"],
                    "유병률": round(r["prevalence"], 4),
                    "ROC_AUC": round(r["roc_auc"], 4),
                    "AUC_sd": round(r["roc_auc_sd"], 4),
                    "MCC": round(r["mcc"], 4), "BA": round(r["ba"], 4),
                    "ΔAUC_CT권고_vs_A0": round(d["delta"], 4) if d else None,
                    "t_df4": round(d["t_df4"], 2) if d and d["t_df4"] else None})
            log(f"  {pname:26} [{im:6}] {xn:7} n={r['n']} p={r['prevalence']:.3f} "
                f"AUC={r['roc_auc']:.4f}±{r['roc_auc_sd']:.4f} MCC={r['mcc']:.3f}"
                + (f" Δ(CT)={d['delta']:+.4f}" if d else ""))

PERF = pd.DataFrame(RES)
PERF.to_csv(JUR_OUT / "negrec마스크_성능영향.csv", index=False, encoding="utf-8-sig")
log(f"→ {JUR_OUT}/negrec마스크_성능영향.csv")

# ==================================================================== 레지스트리
REG = []
for jn, d in JUR.items():
    for ep in EPS:
        for cat, bb in d[ep].items():
            REG.append({"관할": jn, "endpoint": ep, "정본범주": cat, "이진": bb,
                        "처리": "양성" if bb else "음성(해당 관할 미분류)",
                        "확인상태": d["확인상태"], "근거": d["근거"]})
for jn in JUR:
    a3 = JUR[jn]["skin"].get("3") == 1
    REG.append({"관할": jn, "endpoint": "skin", "정본범주": "NC(EPA IV 유래)",
                "이진": None if a3 else 0,
                "처리": "마스크(판별불가)" if a3 else "음성",
                "확인상태": JUR[jn]["확인상태"],
                "근거": ("EPA 피부 IV(72h 경미)는 GHS Cat 3(1.5–<2.3)과 무범주(<1.5)에 "
                       "걸쳐 있어 Cat 3 채택 관할에서 범주 확정 불가. 40 CFR 156.62 표1"
                       if a3 else
                       "Cat 3 미채택 관할이므로 Cat 3 와 무범주가 모두 '분류되지 않음'으로 "
                       "수렴 → 음성 확정. 40 CFR 156.62 표1")})
REGD = pd.DataFrame(REG)
# 기존 레지스트리(범주 단위 66행 중 EPA IV 2행 제외 64행)와 값이 어긋나지 않는지 확인
_old = pd.read_csv(JUR_OUT / "관할매핑_레지스트리.csv")
_o = _old[~_old["정본범주"].astype(str).str.contains("EPA IV")].reset_index(drop=True)
_n = REGD[~REGD["정본범주"].astype(str).str.contains("EPA IV")].reset_index(drop=True)
assert len(_o) == len(_n) == 64, f"레지스트리 행수 {len(_o)}/{len(_n)}"
for col in ("관할", "endpoint", "정본범주", "확인상태"):
    assert (_o[col].astype(str).values == _n[col].astype(str).values).all(), \
        f"레지스트리 {col} 가 기존 산출과 어긋남 — 투영표 드리프트"
assert (_o["이진"].astype(float).values == _n["이진"].astype(float).values).all()
REGD.to_csv(JUR_OUT / "관할매핑_레지스트리_보정.csv", index=False, encoding="utf-8-sig")
log(f"레지스트리 보정 — EPA IV 행을 4 관할 전부 기록 ({len(REGD)}행, 기존 64행 값 일치 확인)")

# ==================================================================== 작업파일
log("=" * 66)
log("수동검토/최종판단 작업파일 생성")

# --- A. 관할 규정 확정 (부분확인 → 원문 대조 필요) --------------------------
# 결정 단위는 '관할 × 선택범주 채택여부'다. 66행 반복이 아니라 결정 6건으로 낸다.
n_eye2b = int(((CANON["eye"] == "2B")).sum())
n_skin3 = int(((CANON["skin"] == "3")).sum())
A_ROWS = [
    {"항목ID": "A1", "관할": "K_REACH", "endpoint": "eye",
     "결정사항": "Eye Irrit. 2B (H320) 세분범주를 채택하는가?",
     "현재_가정": "채택 (2B → 양성)", "확인상태": "부분확인",
     "영향행수": n_eye2b,
     "영향": f"채택 취소 시 눈 {n_eye2b}행이 양성→음성. 유병률 0.618→0.335 수준으로 이동",
     "대조할_원문": "고용노동부 고시 「화학물질의 분류·표시 및 물질안전보건자료에 관한 기준」 "
                "별표1 눈 손상성/눈 자극성 구분",
     "확인방법": "고시 별표1 에서 '눈 자극성 구분 2A / 2B' 표기가 있는지 확인. "
              "구분 2 단일이면 미채택.",
     "확인결과(채택/미채택)": "", "근거조항": "", "확인자": "", "확인일": "", "비고": ""},
    {"항목ID": "A2", "관할": "K_REACH", "endpoint": "skin",
     "결정사항": "Skin Irrit. 3 (H316) 선택범주를 채택하는가?",
     "현재_가정": "미채택 (Cat 3 → 음성)", "확인상태": "부분확인",
     "영향행수": n_skin3,
     "영향": f"채택 시 피부 {n_skin3}행이 음성→양성, 그리고 EPA 피부 IV "
           f"{int(EPA_SKIN4.sum())}행이 판별불가로 마스크됨(행 수 1059→615)",
     "대조할_원문": "고용노동부 고시 별표1 피부 부식성/자극성 구분",
     "확인방법": "'피부 자극성 구분 3' 항목 존재 여부 확인. 없으면 현재 가정이 맞다.",
     "확인결과(채택/미채택)": "", "근거조항": "", "확인자": "", "확인일": "", "비고": ""},
    {"항목ID": "A3", "관할": "K_REACH", "endpoint": "sens",
     "결정사항": "Skin Sens. 1A/1B 세분을 채택하는가? (현재 투영은 1/1A/1B 모두 양성)",
     "현재_가정": "1/1A/1B 모두 양성 — 이진에서는 세분 여부가 결과를 바꾸지 않음",
     "확인상태": "부분확인", "영향행수": 0,
     "영향": "이진 분류에서는 영향 없음. 다범주 확장 시에만 필요",
     "대조할_원문": "고용노동부 고시 별표1 피부 과민성",
     "확인방법": "구분 1 단일인지 1A/1B 세분인지 확인. 이진 목표에서는 확인 후행 가능.",
     "확인결과(채택/미채택)": "", "근거조항": "", "확인자": "", "확인일": "", "비고": ""},
    {"항목ID": "A4", "관할": "US_OSHA", "endpoint": "eye",
     "결정사항": "Eye Irrit. 2B (H320) 세분범주를 채택하는가?",
     "현재_가정": "채택 (2B → 양성)", "확인상태": "부분확인", "영향행수": n_eye2b,
     "영향": f"채택 취소 시 눈 {n_eye2b}행이 양성→음성",
     "대조할_원문": "29 CFR 1910.1200 Appendix A.3 (Serious Eye Damage/Eye Irritation)",
     "확인방법": "A.3 표에서 Category 2A / 2B 구분 표기 확인.",
     "확인결과(채택/미채택)": "", "근거조항": "", "확인자": "", "확인일": "", "비고": ""},
    {"항목ID": "A5", "관할": "US_OSHA", "endpoint": "skin",
     "결정사항": "Skin Irrit. 3 (H316) 선택범주를 채택하는가?",
     "현재_가정": "미채택 (Cat 3 → 음성)", "확인상태": "부분확인", "영향행수": n_skin3,
     "영향": f"채택 시 피부 {n_skin3}행 음성→양성 + EPA IV {int(EPA_SKIN4.sum())}행 마스크",
     "대조할_원문": "29 CFR 1910.1200 Appendix A.2 (Skin Corrosion/Irritation)",
     "확인방법": "A.2 표에 Category 3 행이 있는지 확인. HCS 2012 는 Cat 3 를 "
              "채택하지 않는 것으로 알려져 있으나 원문 대조가 필요하다.",
     "확인결과(채택/미채택)": "", "근거조항": "", "확인자": "", "확인일": "", "비고": ""},
    {"항목ID": "A6", "관할": "US_OSHA", "endpoint": "sens",
     "결정사항": "Skin Sens. 1A/1B 세분을 채택하는가?",
     "현재_가정": "1/1A/1B 모두 양성", "확인상태": "부분확인", "영향행수": 0,
     "영향": "이진 분류에서는 영향 없음",
     "대조할_원문": "29 CFR 1910.1200 Appendix A.4",
     "확인방법": "구분 1 단일 / 1A·1B 세분 여부 확인. 이진 목표에서는 확인 후행 가능.",
     "확인결과(채택/미채택)": "", "근거조항": "", "확인자": "", "확인일": "", "비고": ""},
]

# --- B. 행 단위 판별불가 / 정책 결정 ----------------------------------------
# 피부 정본 NC 중 EPA IV 유래가 아닌 행 — '확인 불가'로 남긴 항목
skin_nc = (CANON["skin"] == "NC")
skin_nc_nonepa = skin_nc & ~EPA_SKIN4
B_ROWS = [
    {"항목ID": "B1", "대상": f"EPA 피부 IV 유래 피부 NC {int(EPA_SKIN4.sum())}행",
     "성격": "범주 판별 불가 (Cat 3 채택 관할)",
     "현재_처리": "UN_GHS 에서만 마스크(학습 제외). EU/K_REACH/US_OSHA 는 음성 확정",
     "왜_판단이_필요한가": "EPA IV = '72시간 경미한 자극'은 GHS Cat 3(평균 1.5–<2.3)과 "
                    "무범주(<1.5)에 걸쳐 있다. 원 시험보고서의 평균 점수가 있으면 "
                    "확정 가능하다.",
     "필요한_자료": "NTP/EPA 원 시험보고서의 Draize 평균 점수 (홍반+부종 72h 평균)",
     "결정선택지": "① 현재대로 UN 에서 마스크 유지  ② 원 점수 수집해 확정  "
              "③ UN 에서도 음성으로 확정(권장하지 않음 — 미시험을 음성으로 쓰는 것과 동일)",
     "판단결과": "", "판단근거": "", "확인자": "", "확인일": ""},
    {"항목ID": "B2", "대상": f"EPA IV 유래가 아닌 피부 NC {int(skin_nc_nonepa.sum())}행",
     "성격": "확인 불가 (공급자 SDS 의 '분류되지 않음' 침묵)",
     "현재_처리": "전 관할에서 음성",
     "왜_판단이_필요한가": "공급자의 소재 관할이 Cat 3 미채택 지역(EU 등)이면 그 SDS 의 "
                    "침묵은 Cat 3 를 배제하지 않는다. UN 기준에서는 원칙상 판별 "
                    "불가일 수 있다.",
     "필요한_자료": "각 SDS 의 발행 관할(공급자 소재지 / 준거 규정 표기)",
     "결정선택지": "① 현재대로 음성 유지  ② SDS 발행 관할을 확인해 EU계 SDS 는 "
              "UN 투영에서 마스크  ③ 전부 마스크(데이터 손실 큼)",
     "판단결과": "", "판단근거": "", "확인자": "", "확인일": ""},
    {"항목ID": "B3", "대상": "눈 negrec 109행",
     "성격": "라벨 신뢰도 미달 (M3 위치지수 0.60, 양성예측률 2.0배)",
     "현재_처리": "**학습 마스크 적용됨** (본 실행에서 반영). 라벨값은 보존",
     "왜_판단이_필요한가": "마스크는 우회 조치다. 원문을 확인하면 음성이 맞는지 "
                    "양성으로 정정할지가 확정된다.",
     "필요한_자료": "해당 제형의 SDS Section 11 원문 (아래 C 시트에 URL 정리)",
     "결정선택지": "① 마스크 유지하며 원문 재수집 진행  ② 마스크 해제(권장하지 않음)  "
              "③ 원문 확인 후 양성으로 정정",
     "판단결과": "", "판단근거": "", "확인자": "", "확인일": ""},
    {"항목ID": "B4", "대상": "피부 negrec 128행 / 감작 negrec 211행",
     "성격": "판정 유보 (피부 위치지수 0.36–0.38, 감작 0.41↔0.16 불안정)",
     "현재_처리": "**마스크하지 않음.** 플래그만 부여(`y_{ep}__negrec`)",
     "왜_판단이_필요한가": "눈만큼 계통 오류 증거가 강하지 않다. 마스크하면 피부 "
                    "128행·감작 211행을 잃는데 근거가 부족하다.",
     "필요한_자료": "원문 SDS Section 11 (표본 확인만으로도 판단 가능)",
     "결정선택지": "① 현재대로 유지  ② 표본 20~30건 원문 확인 후 결정  ③ 눈과 같이 마스크",
     "판단결과": "", "판단근거": "", "확인자": "", "확인일": ""},
]

# --- C. negrec 원문 재수집 목록 (눈 우선) -----------------------------------
try:
    MF = pd.read_csv(AUDIT)
    HAVE = set(MF["url"].dropna().astype(str))
except Exception as e:                                  # noqa: BLE001
    log(f"감사 manifest 읽기 실패 — 로컬보유 판정 생략: {e}")
    HAVE = set()

URLC = [c for c in ("tox_source_url", "supplemental_source_url", "tox2_src_url",
                    "p1_source_url") if c in FORM.columns]
FU = FORM.set_index("Formulation_ID")


def url_of(fid):
    for c in URLC:
        v = FU[c].get(fid)
        if isinstance(v, str) and v.strip().startswith("http"):
            return v.strip(), c
    return "", ""


C_ROWS = []
for ep in EPS:
    for i in np.where(IS_NEGREC[ep])[0]:
        fid = FID[i]
        u, ucol = url_of(fid)
        C_ROWS.append({
            "우선순위": 1 if ep == "eye" else 2,
            "endpoint": ep, "Formulation_ID": fid,
            "product_name": Y["product_name"].iloc[i],
            "현재라벨": RAW[ep][i] or "결측", "현재이진": 0,
            "마스크적용": "예" if ep == "eye" else "아니오",
            "원문URL": u, "URL출처컬럼": ucol,
            "로컬보유": "예" if (u and u in HAVE) else "아니오",
            "doc_source": FU["doc_source"].get(fid, ""),
            "doc_rel": FU["doc_rel"].get(fid, ""),
            "판정결과(양성/음성/판별불가)": "", "H코드": "", "원문근거문장": "",
            "확인자": "", "확인일": ""})
CD = pd.DataFrame(C_ROWS).sort_values(
    ["우선순위", "로컬보유", "endpoint", "Formulation_ID"],
    ascending=[True, False, True, True])
log(f"C 목록 {len(CD)}행 (눈 {int((CD.endpoint=='eye').sum())}) — "
    f"URL 보유 {int((CD.원문URL!='').sum())} / 로컬문서 보유 "
    f"{int((CD.로컬보유=='예').sum())}")

# --- D. 기타 미완 항목 ------------------------------------------------------
D_ROWS = [
    {"항목ID": "D1", "구분": "파이프라인 미구현",
     "항목": "`ct_not_applicable` 게이트 구현 (극단 pH 비가산 예외)",
     "현재상태": "컬럼은 정의되어 있으나 소비하는 코드가 없다 "
             "(build_input_v5.py:1168 정의, :1175 로그 — 그 둘뿐)",
     "왜_중요한가": "pH ≤2 / ≥11.5 는 GHS 비가산 예외이자 Skin Corr. 1 판정 근거인데 "
              "해당 12행이 NC 로 라벨돼 있다. 라벨은 피부 Cat1 인데 CT 가산이 못 "
              "미치는 행도 3건 있다.",
     "누가": "모델 담당(코드)", "결정필요": "게이트 구현 우선순위 승인",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D2", "구분": "파이프라인 미구현",
     "항목": "`parse_tox11()` 에 `ghs_hazard_statements` 4번째 근거 추가",
     "현재상태": "회수 A 로 복구한 H코드 19건(H317×6, H319×5, H315×4, H318×3, H314×1)이 "
             "반영 경로가 없어 사장돼 있다",
     "왜_중요한가": "복구된 라벨 주장 19건을 쓸 수 있게 된다",
     "누가": "모델 담당(코드)", "결정필요": "구현 승인",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D3", "구분": "원문 확인",
     "항목": "`EyeIrritation6pack_PID81` 출처간 진짜 모순 1건",
     "현재상태": "SDS 전사는 EPA IV(24h 내 회복 → NC), NTP 실측은 양성. 두 관할 "
             "투영에서 모두 충돌하므로 관할 선택으로 설명되지 않는다",
     "왜_중요한가": "회수 C 56셀 중 유일한 진짜 모순. 원문 없이는 어느 쪽도 못 쓴다",
     "누가": "데이터 담당", "결정필요": "원문 확보 후 판정",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D4", "구분": "데이터 미도착",
     "항목": "김보경 SC·EC 코드 pH 274건",
     "현재상태": "0건 도착. 다만 D1 게이트가 없어 도착했어도 반영될 곳이 없었다",
     "왜_중요한가": "한계로 명시해야 하는 항목. D1 선행이 없으면 재수집 가치도 낮다",
     "누가": "총책임자", "결정필요": "재지시 여부 (D1 구현 후로 미루는 것을 권한다)",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D5", "구분": "데이터 품질",
     "항목": "`composition_mismatch` 를 라벨 폐기 조건으로 승격 / 중복 제품 병합 / "
           "`doc_rel` 결측 1134행 감사",
     "현재상태": "미착수. 중복 제품 미병합은 폴드 누설 위험",
     "왜_중요한가": "폴드 누설은 성능을 낙관적으로 부풀린다",
     "누가": "모델 담당(코드)", "결정필요": "착수 승인",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D6", "구분": "원문 확인",
     "항목": "감작 정정후보 21건 (ECHA Annex VI 대조 필요)",
     "현재상태": "원장만 있고 미적용",
     "왜_중요한가": "Annex VI 조화분류는 신뢰도가 가장 높은 근거다",
     "누가": "데이터 담당", "결정필요": "ECHA 원문 대조 착수",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D7", "구분": "방법론 결정",
     "항목": "결측 처리 규약 확정 (nan→0 유지 vs sklearn 네이티브 전환)",
     "현재상태": "nan→0 이 정규 베이스라인. native 를 병기 중. "
             "S1_chemistry 결측률 19.6%, CT 26열 29.8%",
     "왜_중요한가": "nan→0 은 '미지'와 '무위험(0)'을 같은 값으로 만든다 — 피처 수준에서 "
              "미시험을 음성으로 쓰는 것과 같은 결함이다. 다만 전환하면 과거 "
              "보고 수치 전부와 비교가 끊긴다",
     "누가": "총책임자", "결정필요": "전환 여부 및 전환 시 과거 수치 재산출 범위",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D8", "구분": "방법론 결정",
     "항목": "판정 임계값 (현재 0.5 고정)",
     "현재상태": "AUC 0.73–0.78 인데 BA 는 0.60–0.68 — 0.5 가 최적이 아니다",
     "왜_중요한가": "규제 목적에서 과소분류(위험 누락)와 과대분류의 비용이 다르다. "
              "임계값은 그 비용비로 정해야 하고, 성능이 좋아 보이게 후향적으로 "
              "맞추는 것은 금지",
     "누가": "총책임자", "결정필요": "과소분류:과대분류 비용비 제시",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D9", "구분": "코드 결함",
     "항목": "`pc2_*` 컬럼 순서 비결정성 (`lib_parse.py:296-298` 이 Python set 사용)",
     "현재상태": "실행마다 열 순서가 바뀐다. 재현성 결함",
     "왜_중요한가": "재현성이 깨지면 어떤 성능 비교도 신뢰할 수 없다",
     "누가": "모델 담당(코드)", "결정필요": "수정 승인 (build_input_* 수정 금지 규약과 충돌 — "
                              "총책임자 예외 승인 필요)",
     "판단결과": "", "확인자": "", "확인일": ""},
    {"항목ID": "D10", "구분": "원문 확인",
     "항목": "A시트 6건 수정행 — 중복 레코드에서 확정라벨을 끌어온 건",
     "현재상태": "중복 동일성 미검증이라 적용 보류",
     "왜_중요한가": "중복이 실제로 같은 제품이 아니면 잘못된 라벨을 심는다",
     "누가": "데이터 담당", "결정필요": "중복 동일성 확인 후 적용 여부",
     "판단결과": "", "확인자": "", "확인일": ""},
]

# --- E. L1 정본 정정 82셀 확인 ----------------------------------------------
E = FIXLOG.copy()
E["확인결과(승인/반대)"] = ""
E["확인자"] = ""
E["확인일"] = ""

# --- F. 부분확인 레지스트리 원본 (행 단위, 추적용) --------------------------
F = REGD[REGD["확인상태"] != "확인"].copy()

# ==================================================================== 엑셀
GUIDE = [
    ["수동검토 · 최종판단 작업파일", ""],
    ["", ""],
    ["이 파일은 모델 산출물과 분리된 '사람이 판단해야 하는 항목'만 모은 것이다.", ""],
    ["모델 코드는 이 파일을 읽지 않는다. 여기에 채운 결과를 내가 반영한다.", ""],
    ["", ""],
    ["시트", "무엇을 하면 되는가"],
    ["A_관할규정_확정", "K_REACH / US_OSHA 의 선택범주 채택 여부를 고시·HCS 원문과 "
                  "대조해 '확인결과' 칸에 채택/미채택을 적는다. 6건. "
                  "이 중 A1·A2·A4·A5 는 라벨이 실제로 뒤집히는 항목이다."],
    ["B_행단위_판별불가", "행 단위로 '음성인지 판별불가인지'가 걸린 정책 결정 4건. "
                   "'결정선택지' 중 하나를 골라 '판단결과' 에 적는다."],
    ["C_negrec_원문재수집", "SDS Section 11 원문을 열어 실제 판정을 적는다. "
                     "우선순위 1(눈 109행)이 최우선. 로컬보유='예' 인 행은 "
                     "이미 문서가 있으니 먼저 처리할 수 있다."],
    ["D_기타_미완항목", "코드 구현·재지시·방법론 결정이 걸린 항목 10건. "
                  "'누가' 열이 총책임자인 항목(D4·D7·D8·D9)은 결정만 주면 된다."],
    ["E_L1정정_82셀", "피부 2A/2B → 2 로 정정한 82셀. GHS 범주 유효성 정정이므로 "
                 "이진 라벨은 바뀌지 않았다. 승인/반대만 확인하면 된다."],
    ["F_부분확인_레지스트리", "A 시트의 근거가 되는 원본 매핑 행(추적용). "
                     "직접 채울 것은 없다."],
    ["", ""],
    ["입력 규칙", ""],
    ["1", "회색 헤더 아래 빈 칸만 채운다. 기존 값이 있는 칸은 고치지 않는다."],
    ["2", "확실하지 않으면 비워 두거나 '확인 불가' 라고 적는다. 추측으로 채우지 않는다."],
    ["3", "판정에는 반드시 근거(조항 번호 또는 원문 문장)를 같이 적는다."],
    ["4", "이 파일은 라벨을 직접 바꾸지 않는다. 원본 라벨(L0)은 어떤 경우에도 보존된다."],
]

HDR = Font(bold=True)
FILLH = PatternFill("solid", fgColor="D9D9D9")
FILLIN = PatternFill("solid", fgColor="FFF2CC")     # 입력 칸
INPUT_KW = ("확인결과", "판정결과", "판단결과", "근거조항", "확인자", "확인일",
            "비고", "판단근거", "H코드", "원문근거문장")

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "0_작업안내"
for r in GUIDE:
    ws.append(r)
for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
    for c in row:
        c.alignment = Alignment(vertical="top", wrap_text=True)
ws["A1"].font = Font(bold=True, size=14)
ws.column_dimensions["A"].width = 24
ws.column_dimensions["B"].width = 110

for nm, df in [("A_관할규정_확정", pd.DataFrame(A_ROWS)),
               ("B_행단위_판별불가", pd.DataFrame(B_ROWS)),
               ("C_negrec_원문재수집", CD),
               ("D_기타_미완항목", pd.DataFrame(D_ROWS)),
               ("E_L1정정_82셀", E),
               ("F_부분확인_레지스트리", F)]:
    w = wb.create_sheet(nm)
    w.append(list(df.columns))
    for _, r in df.iterrows():
        w.append(["" if (v is None or (isinstance(v, float) and math.isnan(v)))
                  else str(v) for v in r.tolist()])
    for c in w[1]:
        c.font = HDR
        c.fill = FILLH
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for j, col in enumerate(df.columns, start=1):
        wide = any(k in str(col) for k in ("근거", "영향", "왜_", "확인방법", "결정",
                                           "현재상태", "성격", "항목", "원문", "URL"))
        w.column_dimensions[w.cell(1, j).column_letter].width = 46 if wide else 18
        if any(k in str(col) for k in INPUT_KW):
            for i in range(2, len(df) + 2):
                w.cell(i, j).fill = FILLIN
    for row in w.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    w.freeze_panes = "A2"
    if len(df):
        w.auto_filter.ref = w.dimensions

# ┌─ 경고 (2026-09-04) ────────────────────────────────────────────────────┐
# │ 이 워크북은 이후 `rebuild_review_workbook_by_role.py` 가 **담당별 시트**  │
# │ 로 재구성해 같은 경로에 덮어쓴다. 이 스크립트를 다시 돌리면 담당 배정이   │
# │ 사라진 이전 판으로 되돌아가고, 팀원이 채운 판정이 전부 지워진다.          │
# │ 또한 위 `로컬보유` 판정(:679)은 결함이다 — `doc_rel` 로컬 PDF 430건을     │
# │ 보지 않아 5건만 '예'로 표시했다. 재구성판은 파일 실존 검사로 판정한다.    │
# │ → 이 스크립트를 재실행하려면 먼저 작업파일을 백업하고, 실행 후 반드시     │
# │   `rebuild_review_workbook_by_role.py` 를 이어서 돌릴 것.                │
# └────────────────────────────────────────────────────────────────────────┘
_WBP = REV_OUT / "수동검토_최종판단_작업파일.xlsx"
if _WBP.exists():
    import shutil
    shutil.copy2(_WBP, _WBP.with_name(_WBP.stem + "_직전판_자동백업.xlsx"))
    log(f"기존 작업파일을 {_WBP.stem}_직전판_자동백업.xlsx 로 보존")
wb.save(_WBP)
for nm, df in [("A_관할규정_확정", pd.DataFrame(A_ROWS)),
               ("B_행단위_판별불가", pd.DataFrame(B_ROWS)),
               ("C_negrec_원문재수집", CD),
               ("D_기타_미완항목", pd.DataFrame(D_ROWS))]:
    df.to_csv(REV_OUT / f"{nm}.csv", index=False, encoding="utf-8-sig")
log(f"→ {REV_OUT}/수동검토_최종판단_작업파일.xlsx (+ CSV 4종)")

with open(REV_OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "지시": "눈 negrec 109셀 학습 마스크 + 부분확인/수동판단 항목 작업파일 분리",
        "마스크": {
            "적용": "eye 109행 (y_eye_usable__{관할} = 0). 라벨값·정본·관할범주는 보존",
            "미적용": "skin 128행 / sens 211행 — 플래그만 부여. 근거 부족으로 판정 유보",
            "근거": "M3 위치지수 eye 0.596(nan0)/0.602(native), 양성예측률 40.7% vs "
                  "타출처 음성 20.2%(2.0배). 두 규약에서 값이 같아 규약 인공물 아님",
            "행수변화": {jn: {"before": int(LAYER[(jn, "eye")][2].sum()),
                          "after": int(MASKED[(jn, "eye")].sum())} for jn in JUR},
            "유병률변화": {jn: {
                "before": round(float(LAYER[(jn, "eye")][1][LAYER[(jn, "eye")][2]].mean()), 4),
                "after": round(float(LAYER[(jn, "eye")][1][MASKED[(jn, "eye")]].mean()), 4)}
                for jn in JUR},
            "되돌리기": "`y_eye_usable_premask__{관할}` 컬럼에 마스크 전 값이 보존돼 있다",
        },
        "작업파일": {
            "위치": str(REV_OUT),
            "A_관할규정_확정": len(A_ROWS),
            "B_행단위_판별불가": len(B_ROWS),
            "C_negrec_원문재수집": int(len(CD)),
            "C_로컬문서보유": int((CD["로컬보유"] == "예").sum()),
            "C_URL있음": int((CD["원문URL"] != "").sum()),
            "D_기타_미완항목": len(D_ROWS),
            "E_L1정정": int(len(E)),
            "F_부분확인_레지스트리행": int(len(F)),
        },
        "측정": {
            "M4_동일평가행": "평가는 신뢰 1044행 고정, 학습에만 negrec 포함/제외. "
                        "폴드 고정, 그룹 누설 차단. Δ>0 이면 마스크가 이득",
            "M5_마스크후": "마스크된 레이어의 실제 성능. n 1153→1044, 유병률 상승 — "
                      "**1153행 수치와 직접 비교 불가**",
        },
        "한계": [
            "마스크는 원문 대조의 대체물이 아니다. M3 위치지수는 negrec 행이 화학적으로 "
            "특이하기만 해도 올라갈 수 있다.",
            "마스크로 눈 학습행이 1153→1044(9.5% 감소)한다. 표본 감소 자체가 성능을 "
            "낮출 수 있으므로 M4(동일평가행)가 순효과 판정의 근거다.",
            "K_REACH/US_OSHA 는 여전히 '부분확인'이다. 작업파일 A 시트가 채워질 때까지 "
            "이 두 관할 수치는 가정에 기반한 값이다.",
        ],
    }, f, ensure_ascii=False, indent=2, default=str)
log("완료")
