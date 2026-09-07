#!/usr/bin/env python3
"""관할별 라벨 레이어 구축 + 관할별 성능 측정 + negrec 448셀 교차전이 검정.

설계 (총책임자 지시: "기본 원본 라벨은 유지하고, 세부 라벨은 각 국가 규제 기준에
맞게 별도로 매핑")
--------------------------------------------------------------------------
L0  원본(raw)   : y_{ep}. **절대 불변.** 디스크와 바이트 동일성을 assert 로 고정.
L1  정본(canon) : L0 에 **GHS 범주 유효성 정정만** 적용. 관할 선택과 무관한
                  규범 오류만 고친다. 현재 해당은 피부 '2A'/'2B' → '2' 뿐이다
                  (2A/2B 는 눈 전용 범주 — 어느 관할에도 피부 2A/2B 는 없다).
L2  관할투영    : L1 → 관할별 (범주, 이진, 사용가능여부). 관할마다 독립 컬럼으로
                  병렬 생성한다. 어느 것도 다른 것을 덮어쓰지 않는다.

관할이 갈리는 축은 정확히 두 개다.
  (a) Eye Irrit. 2B (H320)  — UN GHS/미국/한국 채택, EU CLP Annex I 미채택
  (b) Skin Irrit. 3 (H316)  — UN GHS/미국 채택, EU CLP·K-REACH 미채택
따라서 관할 프로파일은 3종으로 분기한다(EU / K-REACH·US / UN).

'미채택'의 처리
--------------
미채택 범주는 **음성**으로 투영한다 — 그 관할에서 해당 물질은 실제로 분류되지
않으므로 규제 예측 대상으로서 음성이 맞다. 단 하나의 예외를 둔다: 판정 자체가
불가능한 경우는 음성이 아니라 **마스크**다. EPA 피부 IV("72시간 경미한 자극")는
GHS Cat 3(평균 1.5–<2.3)과 무범주(<1.5)에 걸쳐 있어 UN 기준으로 범주를 확정할
수 없다. Cat 3 를 채택하는 관할(UN)에서만 이 444행이 판별 불가가 되고, Cat 3 를
미채택하는 관할(EU/K-REACH)에서는 둘 다 '분류되지 않음'으로 수렴하므로 음성으로
확정된다. 미시험/판별불가를 음성으로 쓰지 않는다는 원칙을 그대로 적용한 결과다.

측정
----
M1 관할 비교  : 모든 관할에서 판정 가능한 **공통행**만 사용. 폴드를 1회 산출해
                관할 간 고정 → 차이를 라벨 투영에만 귀속시킨다. CT 는 A0(기본).
M2 관할별 실전: 관할별 자기 행집합·자기 폴드. **관할 간 직접 비교 불가**로 명시.
                CT 는 앞선 권고 조합(eye/skin A2, sens A3).
M3 교차전이   : negrec 448셀(전부 NC)의 신뢰도. negrec 를 뺀 학습(그룹 누설 차단)
                후 negrec 예측 양성률을 다른 출처의 음성행 양성률과 비교한다.
                원문 대조가 불가한 상황에서 계통 오류를 계량하는 우회로다.

무수정 원칙: 모든 입력 읽기 전용. 산출은 04_모델산출물/v6_jurisdiction/ 에만.
GHS CT 가산 공식 변경 없음. 라벨 값은 메모리에서만 파생한다.
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (balanced_accuracy_score, matthews_corrcoef,
                             roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path("/Users/hanseoyun/Desktop/260830")
SRC = ROOT / "04_모델산출물" / "v4_fixed"               # 읽기 전용
V6 = v6.V6                                              # 통합 단일 입력(읽기 전용)
OUT = ROOT / "04_모델산출물" / "v6_jurisdiction"
OUT.mkdir(parents=True, exist_ok=True)

SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
_t0 = time.time()
_LOGF = open(OUT / "run.log", "w", encoding="utf-8")


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
assert (X["Formulation_ID"].values == Y["Formulation_ID"].values).all(), "행 정렬 불일치"

FID = X["Formulation_ID"].astype(str).to_numpy()
GK = Y["group_key"].astype(str).to_numpy()
N = len(FID)
MANF = MAN[MAN["sheet"] == "formulation"]
CHEM = sorted(c for c in MANF[(MANF["feature_role"] == "chemistry")
                              & (~MANF["exclude_by_default"].astype(bool))]["column"]
              if c != "Formulation_ID")
CT_COLS = sorted(c for c in CHEM if c.startswith(("f_ct_", "ct_")))
assert len(CT_COLS) == 26, f"CT 열 수 {len(CT_COLS)} != 26"
log(f"X={X.shape}  Y={Y.shape}  S1_chemistry={len(CHEM)}열  CT={len(CT_COLS)}열")

xl = pd.ExcelFile(V6)
FORM = xl.parse("formulation")
ING = xl.parse("ingredient")
FORM_I = FORM.set_index("Formulation_ID")

# ==================================================================== L0 원본
EPS = ("eye", "skin", "sens")
RAW = {ep: Y[f"y_{ep}"].fillna("").astype(str).to_numpy() for ep in EPS}
SRCD = {ep: Y[f"y_{ep}_src_detail"].fillna("").astype(str).to_numpy() for ep in EPS}
RAW_SNAPSHOT = {ep: RAW[ep].copy() for ep in EPS}
for ep in EPS:
    log(f"L0 y_{ep} 원본 분포: {pd.Series(RAW[ep]).replace('', '결측').value_counts().to_dict()}")

# ==================================================================== L1 정본
# GHS 범주 유효성만 정정한다. 관할 선택과 무관한 규범 오류만 대상.
VALID = {"eye": {"1", "2", "2A", "2B", "NC"},
         "skin": {"1", "1A", "1B", "1C", "2", "3", "NC"},
         "sens": {"1", "1A", "1B", "NC"}}
# (endpoint, 원본범주) -> (정본범주, 정정근거)
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
        if v == "":
            continue
        if (ep, v) in CANON_FIX:
            new, why = CANON_FIX[(ep, v)]
            out[i] = new
            fixlog.append({"Formulation_ID": FID[i], "endpoint": ep, "원본": v,
                           "정본": new, "정정근거": why, "출처": SRCD[ep][i]})
    CANON[ep] = out
    bad = sorted({v for v in out if v not in VALID[ep] and v != ""})
    assert not bad, f"{ep} 정본에 GHS 무효 범주 잔존: {bad}"
    log(f"L1 정본 y_{ep}: {pd.Series(out).replace('','결측').value_counts().to_dict()}")
FIXLOG = pd.DataFrame(fixlog)
log(f"L1 정정 셀: {len(FIXLOG)}건 " +
    (FIXLOG.groupby(['endpoint','원본']).size().to_dict().__str__() if len(FIXLOG) else ""))

# L0 불변 검증 — 정본을 만드는 과정에서 원본 배열이 오염되지 않았는가
for ep in EPS:
    assert (RAW[ep] == RAW_SNAPSHOT[ep]).all(), f"L0 y_{ep} 오염"
    assert (Y[f"y_{ep}"].fillna("").astype(str).to_numpy() == RAW_SNAPSHOT[ep]).all()
log("L0 원본 불변 검증 통과 (디스크 == 메모리, 3 엔드포인트)")

# ==================================================================== L2 관할 투영
# 값: 1=양성, 0=음성, None=판정불가(마스크)
# 근거: 1=채택 범주, 0=미채택(그 관할에서 분류되지 않음), None=범주 확정 불가
_UN_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 1, "NC": 0}
_EU_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 0, "NC": 0}     # 2B(H320) 미채택
_UN_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 1, "NC": 0}
_EU_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 0, "NC": 0}  # Cat3 미채택
_SENS = {"1": 1, "1A": 1, "1B": 1, "NC": 0}               # 관할 간 차이 없음

JUR = {
    "UN_GHS":  {"eye": _UN_EYE, "skin": _UN_SKIN, "sens": _SENS,
                "확인상태": "확인", "근거": "UN GHS Rev.10 3.3(눈 2A/2B 선택범주 포함), "
                                    "3.2(피부 Cat 3 선택범주 포함)"},
    "EU_CLP":  {"eye": _EU_EYE, "skin": _EU_SKIN, "sens": _SENS,
                "확인상태": "확인", "근거": "규칙 (EC) 1272/2008 부속서 I — Eye Irrit. 2 "
                                    "단일범주(2B/H320 없음), Skin Irrit. 2 단일범주"
                                    "(Cat 3/H316 없음)"},
    "K_REACH": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS,
                "확인상태": "부분확인", "근거": "고용노동부 고시 화학물질 분류·표시 — 눈 "
                                      "자극성 2A/2B 세분 채택, 피부 자극성 구분 2 "
                                      "단일(구분 3 미채택). **고시 원문 대조 미완**"},
    "US_OSHA": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS,
                "확인상태": "부분확인", "근거": "OSHA HCS 2012 부속서 A.3(눈 2A/2B), "
                                      "A.2(피부 Cat 2, Cat 3 미채택). **원문 대조 미완**"},
}
# 행 단위 오버레이 — 범주 단위 규칙으로 표현할 수 없는 판별 불가 조건
EPA_SKIN4 = (FORM_I["ntp_epa_skin_cat"].reindex(FID).astype(float) == 4).to_numpy()
log(f"오버레이 대상 EPA 피부 IV 행: {int(EPA_SKIN4.sum())}")


def project(jname: str, ep: str):
    """(관할범주 배열, 이진 배열, 사용가능 마스크)."""
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
    # 오버레이: Cat 3 을 채택하는 관할에서만 EPA 피부 IV 가 판별 불가
    if ep == "skin" and tab.get("3") == 1:
        drop = EPA_SKIN4 & keep
        keep = keep & ~EPA_SKIN4
        cat[EPA_SKIN4] = "판별불가(EPA IV ↔ Cat3/무범주 경계)"
        log(f"  {jname}/skin 오버레이 마스크: {int(drop.sum())}행 "
            f"(EPA IV — Cat 3 채택 관할에서 범주 확정 불가)")
    return cat, bin_, keep


LAYER = {}
for jn in JUR:
    for ep in EPS:
        LAYER[(jn, ep)] = project(jn, ep)
        c, b, k = LAYER[(jn, ep)]
        log(f"L2 {jn:8}/{ep:4} n={int(k.sum()):5} 양성={int(b[k].sum()):4} "
            f"유병률={b[k].mean():.3f}")

# 관할 간 라벨이 실제로 갈리는 행 계량
DIVERG = []
for ep in EPS:
    base = LAYER[("UN_GHS", ep)][1]
    for jn in ("EU_CLP", "K_REACH", "US_OSHA"):
        b = LAYER[(jn, ep)][1]
        both = (base >= 0) & (b >= 0)
        DIVERG.append({"endpoint": ep, "관할": jn, "UN과_공통판정행": int(both.sum()),
                       "이진불일치": int((base[both] != b[both]).sum())})
log("관할 간 이진 불일치: " + json.dumps(DIVERG, ensure_ascii=False))

# ---- 라벨 레이어 산출 (원본·정본·관할별 병렬 컬럼) -------------------------
LAB = pd.DataFrame({"Formulation_ID": FID, "group_key": GK})
for ep in EPS:
    LAB[f"y_{ep}__L0_raw"] = np.where(RAW[ep] == "", None, RAW[ep])
    LAB[f"y_{ep}__L1_canon"] = np.where(CANON[ep] == "", None, CANON[ep])
    LAB[f"y_{ep}__L1_fixed"] = (RAW[ep] != CANON[ep]).astype(int)
    for jn in JUR:
        c, b, k = LAYER[(jn, ep)]
        LAB[f"y_{ep}_cat__{jn}"] = np.where(c == "", None, c)
        LAB[f"y_{ep}_bin__{jn}"] = np.where(k, b, None)
        LAB[f"y_{ep}_usable__{jn}"] = k.astype(int)
LAB.to_csv(OUT / "라벨레이어_관할별.csv", index=False, encoding="utf-8-sig")

REG = []
for jn, d in JUR.items():
    for ep in EPS:
        for cat, b in d[ep].items():
            REG.append({"관할": jn, "endpoint": ep, "정본범주": cat,
                        "이진": b, "처리": "양성" if b else "음성(해당 관할 미분류)",
                        "확인상태": d["확인상태"], "근거": d["근거"]})
# EPA IV 오버레이는 4 관할 전부에 대해 기록한다. Cat 3 채택 여부로 처리가 갈리므로
# 채택 관할만 적으면 레지스트리가 불완전해진다(초기 판에서 2 관할만 적혀 있었다).
for jn in JUR:
    adopts3 = JUR[jn]["skin"].get("3") == 1
    REG.append({
        "관할": jn, "endpoint": "skin", "정본범주": "NC(EPA IV 유래)",
        "이진": None if adopts3 else 0,
        "처리": "마스크(판별불가)" if adopts3 else "음성",
        "확인상태": JUR[jn]["확인상태"],
        "근거": ("EPA 피부 IV(72h 경미)는 GHS Cat 3(1.5–<2.3)과 무범주(<1.5)에 걸쳐 "
               "있어 Cat 3 채택 관할에서 범주 확정 불가. 40 CFR 156.62 표1"
               if adopts3 else
               "Cat 3 미채택 관할이므로 Cat 3 와 무범주가 모두 '분류되지 않음'으로 "
               "수렴 → 음성 확정. 40 CFR 156.62 표1")})
pd.DataFrame(REG).to_csv(OUT / "관할매핑_레지스트리.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(REG if len(FIXLOG) == 0 else REG).head(0)
FIXLOG.to_csv(OUT / "L1_정본정정_원장.csv", index=False, encoding="utf-8-sig")

# ==================================================================== CT 재산출
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
        return {"cat": None, "s1": None, "s2": None, "s3": None, "add": None, "n_known": 0}
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
    """엔드포인트별로 다른 arm 을 적용해 CT 재산출. 기존 ing_ghs_* 는 덮지 않는다."""
    rows = []
    for fid, g in ING_G.groupby("Formulation_ID", sort=False):
        n = len(g)
        row = {"Formulation_ID": fid}
        for ep in EPS:
            arm = arm_by_ep[ep]
            cats, pcts = [], []
            for _, r in g.iterrows():
                base = r[f"ing_ghs_{ep}"]
                base = None if (base is None or (isinstance(base, float) and math.isnan(base))
                                or str(base).lower() in ("nan", "none", "")) else str(base)
                cat = base
                if arm != "A0_base" and cat is None:          # 결측만 채운다
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


def ct_to_X(base_X: pd.DataFrame, ct: pd.DataFrame) -> pd.DataFrame:
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


log("CT A0(기본) 재산출 — 디스크 일치 검증")
CT0 = build_ct({ep: "A0_base" for ep in EPS})
X0 = X[CHEM].copy()
X_A0 = ct_to_X(X0, CT0)
_mism = [c for c in CT_COLS if not np.allclose(
    pd.to_numeric(X_A0[c], errors="coerce").fillna(-999),
    pd.to_numeric(X0[c], errors="coerce").fillna(-999))]
log(f"A0 재현 불일치 열: {len(_mism)} {_mism[:5]}")
assert not _mism, "A0 재현 실패 — 증강 arm 의 어떤 수치도 신뢰할 수 없다"

log("CT 권고조합 재산출 (eye/skin=A2, sens=A3)")
CT_REC = build_ct({"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown",
                   "sens": "A3_aug_annexvi"})
X_REC = ct_to_X(X0, CT_REC)
# 검증된 v6_skinmap_ct 실측 커버리지와 대조 — 전처리 드리프트를 다시 놓치지 않는다
_COV_EXPECT = {("A0", "eye"): 0.240, ("A0", "skin"): 0.241, ("A0", "sens"): 0.241,
               ("REC", "eye"): 0.330, ("REC", "skin"): 0.296, ("REC", "sens"): 0.496}
for tag, ct in (("A0", CT0), ("REC", CT_REC)):
    for ep in EPS:
        got = float(ct[f"f_ct_{ep}_coverage"].mean())
        exp = _COV_EXPECT[(tag, ep)]
        assert abs(got - exp) < 0.002, f"{tag}/{ep} coverage {got:.3f} != {exp} (검증값 이탈)"
log("CT 커버리지 = v6_skinmap_ct 검증값 일치 (A0 / 권고 각 3 엔드포인트)")

# ---- 결측 처리 규약 ---------------------------------------------------------
# 이 프로젝트의 정규 베이스라인은 전부 nan→0 이다
# (measure_v5_performance.py:138, smoke_baseline_v4_multitask.py:35,
#  measure_target_definition_grid.py:108). 따라서 기존 보고 수치와 비교 가능한
# 것은 nan0 쪽뿐이다. 그러나 nan→0 은 '미지'와 '무위험(0)'을 같은 값으로 만들어
# 피처 수준에서 미시험을 음성으로 쓰는 결함이다. sklearn 1.8 의 트리는 결측을
# 네이티브로 분기 처리하므로 native 가 방법론적으로 우월하다. 둘 다 낸다.
CHEM_NA_RATE = float(X0.isna().to_numpy().mean())
log(f"S1_chemistry 결측률 {CHEM_NA_RATE:.4f} "
    f"(CT 26열 {float(X0[CT_COLS].isna().to_numpy().mean()):.4f}) — 규약 차이가 유의할 수준")


def as_mat(df, impute):
    M = df[CHEM].to_numpy(dtype=np.float64)
    return np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0) if impute == "nan0" else M


XM = {(ct, im): as_mat(df, im)
      for ct, df in (("CT_A0", X_A0), ("CT_권고", X_REC))
      for im in ("nan0", "native")}
IMPUTES = ["nan0", "native"]        # nan0 = 정규 베이스라인 규약(주), native = 병기


# ==================================================================== CV
def make_folds(y, g):
    return [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True,
                                      random_state=sd).split(np.zeros((len(y), 1)), y, g))
            for sd in SEEDS]


def run_cv(Xi, y, g, folds):
    per_seed = []
    for si, sd in enumerate(SEEDS):
        proba = np.full(len(y), np.nan)
        for tr, te in folds[si]:
            m = rf(sd).fit(Xi[tr], y[tr])
            proba[te] = m.predict_proba(Xi[te])[:, 1]
        assert not np.isnan(proba).any(), "OOF 미충족"
        pred = (proba >= 0.5).astype(int)
        per_seed.append({"roc_auc": roc_auc_score(y, proba),
                         "mcc": matthews_corrcoef(y, pred),
                         "ba": balanced_accuracy_score(y, pred)})
    out = {"_per_seed": per_seed}
    for k in per_seed[0]:
        v = [d[k] for d in per_seed]
        out[k] = float(np.mean(v))
        out[k + "_sd"] = float(np.std(v, ddof=1))
    out.update(n=int(len(y)), n_pos=int(y.sum()), prevalence=float(y.mean()),
               n_groups=int(len(set(g))))
    return out


def paired(a, b, key="roc_auc"):
    """폴드 고정 하에서는 시드별 차이가 대응표본이다. pooled sd 는 과소검정."""
    d = np.array([x[key] - y[key] for x, y in zip(a["_per_seed"], b["_per_seed"])])
    sd = float(np.std(d, ddof=1))
    return {"delta": float(d.mean()), "sd_paired": sd,
            "t_df4": float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else None,
            "n_seeds_positive": int((d > 0).sum())}


RES = []

# ---------------- M1 관할 비교 (공통행, 폴드 고정, CT A0) --------------------
log("=" * 66)
log("M1 관할 비교 — 공통행 + 폴드 고정 + CT A0")
M1 = {}
for ep in EPS:
    common = np.ones(N, dtype=bool)
    for jn in JUR:
        common &= LAYER[(jn, ep)][2]
    yb = LAYER[("UN_GHS", ep)][1]
    folds = make_folds(yb[common], GK[common])       # 1회 산출 → 관할 간 고정
    log(f"{ep}: 공통행 {int(common.sum())} / 관할별 전체행 "
        f"{ {jn: int(LAYER[(jn,ep)][2].sum()) for jn in JUR} }")
    for im in IMPUTES:
        base = None
        for jn in JUR:
            y = LAYER[(jn, ep)][1][common]
            r = run_cv(XM[("CT_A0", im)][common], y, GK[common], folds)
            M1[(ep, jn, im)] = r
            d = paired(r, base) if base is not None else {}
            if jn == "UN_GHS":
                base = r
            RES.append({"측정": "M1_공통행_CT_A0", "결측처리": im, "endpoint": ep,
                        "관할": jn, "n": r["n"], "유병률": round(r["prevalence"], 4),
                        "ROC_AUC": round(r["roc_auc"], 4),
                        "AUC_sd": round(r["roc_auc_sd"], 4),
                        "MCC": round(r["mcc"], 4), "BA": round(r["ba"], 4),
                        "ΔAUC_vs_UN": round(d["delta"], 4) if d else None,
                        "sd_paired": round(d["sd_paired"], 4) if d else None,
                        "t_df4": round(d["t_df4"], 2) if d and d["t_df4"] else None,
                        "시드양수": d.get("n_seeds_positive")})
            log(f"  [{im:6}] {jn:8} n={r['n']:5} p={r['prevalence']:.3f} "
                f"AUC={r['roc_auc']:.4f}±{r['roc_auc_sd']:.4f} MCC={r['mcc']:.3f}"
                + (f"  ΔvsUN={d['delta']:+.4f} t={d['t_df4']:.2f}"
                   if d and d["t_df4"] else ""))

# ---------------- M2 관할별 실전 (자기 행집합, CT 권고) ----------------------
log("=" * 66)
log("M2 관할별 실전 — 자기 행집합·자기 폴드, CT 권고조합 (관할 간 직접 비교 불가)")
for ep in EPS:
    for jn in JUR:
        c, b, k = LAYER[(jn, ep)]
        y = b[k]
        fo = make_folds(y, GK[k])              # 라벨 동일 → CT arm·규약 간 폴드 고정
        for im in IMPUTES:
            b0 = None
            for xn in ("CT_A0", "CT_권고"):
                r = run_cv(XM[(xn, im)][k], y, GK[k], fo)
                d = paired(r, b0) if b0 is not None else {}
                if b0 is None:
                    b0 = r
                RES.append({"측정": f"M2_관할행_{xn}", "결측처리": im, "endpoint": ep,
                            "관할": jn, "n": r["n"],
                            "유병률": round(r["prevalence"], 4),
                            "ROC_AUC": round(r["roc_auc"], 4),
                            "AUC_sd": round(r["roc_auc_sd"], 4),
                            "MCC": round(r["mcc"], 4), "BA": round(r["ba"], 4),
                            "ΔAUC_CT권고_vs_A0": round(d["delta"], 4) if d else None,
                            "t_df4": round(d["t_df4"], 2) if d and d["t_df4"] else None})
                if d:
                    log(f"  {ep:4} {jn:8} [{im:6}] n={r['n']:5} p={r['prevalence']:.3f} "
                        f"A0={b0['roc_auc']:.4f} 권고={r['roc_auc']:.4f} "
                        f"Δ={d['delta']:+.4f}")

# ---------------- M3 negrec 448셀 교차전이 검정 ------------------------------
log("=" * 66)
log("M3 negrec 교차전이 — 448셀(전부 NC)이 계통적으로 틀렸는지 계량")
NEG3 = []
for ep, im in [(e, i) for e in EPS for i in IMPUTES]:
    k = LAYER[("EU_CLP", ep)][2]          # 전 행 판정 가능한 관할을 기준으로
    b = LAYER[("EU_CLP", ep)][1]
    isneg = (SRCD[ep] == "sds_v1_negative_recovered") & k
    assert (b[isneg] == 0).all(), "negrec 에 양성 라벨 혼입 — 설계 전제 불성립"
    other = k & ~isneg
    # 그룹 누설 차단: negrec 를 포함한 group_key 는 학습에서 완전히 뺀다
    gbad = set(GK[isneg])
    trmask = other & ~np.isin(GK, list(gbad))
    ytr = b[trmask]
    log(f"{ep}: negrec={int(isneg.sum())} 타출처={int(other.sum())} "
        f"학습가용(그룹누설제외)={int(trmask.sum())} 제외그룹={len(gbad)}")
    if ytr.sum() < 10 or (1 - ytr).sum() < 10:
        log(f"  {ep} 학습 표본 부족 — 측정 불가")
        continue
    Xi = XM[("CT_권고", im)]
    p_neg, p_on, p_op = [], [], []
    for sd in SEEDS:
        m = rf(sd).fit(Xi[trmask], ytr)
        p_neg.append(m.predict_proba(Xi[isneg])[:, 1])
        # 대조: 같은 모델을 자기 학습행에 쓰면 낙관적이므로 OOF 로 뽑는다
        fo = make_folds(ytr, GK[trmask])[SEEDS.index(sd)]
        oof = np.full(len(ytr), np.nan)
        for tr, te in fo:
            oof[te] = rf(sd).fit(Xi[trmask][tr], ytr[tr]).predict_proba(Xi[trmask][te])[:, 1]
        p_on.append(oof[ytr == 0])
        p_op.append(oof[ytr == 1])
    mn = float(np.mean([x.mean() for x in p_neg]))
    mo = float(np.mean([x.mean() for x in p_on]))
    mp = float(np.mean([x.mean() for x in p_op]))
    rn = float(np.mean([(x >= .5).mean() for x in p_neg]))
    ro = float(np.mean([(x >= .5).mean() for x in p_on]))
    rp = float(np.mean([(x >= .5).mean() for x in p_op]))
    # 역방향: negrec 만으로 학습 불가(단일 클래스) → 대신 negrec 를 음성으로 섞은
    # 학습이 타출처 성능을 떨어뜨리는지 본다.
    yfull, kf = b[k], k
    a_wo, a_w = [], []
    tem = other & np.isin(GK, list(GK[other]))
    for sd in SEEDS:
        f_o = make_folds(b[other], GK[other])[SEEDS.index(sd)]
        oo = np.full(int(other.sum()), np.nan)
        Xo, yo = Xi[other], b[other]
        for tr, te in f_o:
            oo[te] = rf(sd).fit(Xo[tr], yo[tr]).predict_proba(Xo[te])[:, 1]
        a_wo.append(roc_auc_score(yo, oo))
        # negrec 를 학습에만 추가하고 평가는 동일한 타출처 폴드에서
        oi = np.full(int(other.sum()), np.nan)
        idx_neg = np.where(isneg)[0]
        for tr, te in f_o:
            tr_g = set(GK[other][tr])
            add = idx_neg[np.isin(GK[isneg], list(tr_g))] if len(idx_neg) else idx_neg
            Xtr = np.vstack([Xo[tr], Xi[add]]) if len(add) else Xo[tr]
            ytr2 = np.concatenate([yo[tr], b[add]]) if len(add) else yo[tr]
            oi[te] = rf(sd).fit(Xtr, ytr2).predict_proba(Xo[te])[:, 1]
        a_w.append(roc_auc_score(yo, oi))
    d = np.array(a_w) - np.array(a_wo)
    sdp = float(np.std(d, ddof=1))
    NEG3.append({"endpoint": ep, "결측처리": im, "negrec_n": int(isneg.sum()),
                 "타출처_n": int(other.sum()), "학습가용_n": int(trmask.sum()),
                 "negrec_평균예측p": round(mn, 4), "타출처음성_OOF_평균p": round(mo, 4),
                 "타출처양성_OOF_평균p": round(mp, 4),
                 "negrec_양성예측률": round(rn, 4), "타출처음성_양성예측률": round(ro, 4),
                 "타출처양성_양성예측률": round(rp, 4),
                 "위치지수": round((mn - mo) / (mp - mo), 4) if mp > mo else None,
                 "타출처AUC_negrec제외": round(float(np.mean(a_wo)), 4),
                 "타출처AUC_negrec학습추가": round(float(np.mean(a_w)), 4),
                 "ΔAUC_paired": round(float(d.mean()), 4),
                 "t_df4": round(float(d.mean() / (sdp / np.sqrt(len(d)))), 2) if sdp > 0 else None})
    log(f"  {ep} [{im}]: negrec 평균p={mn:.4f} vs 타출처음성 {mo:.4f} (양성 {mp:.4f}) "
        f"위치지수={NEG3[-1]['위치지수']} | negrec 학습추가 ΔAUC={d.mean():+.4f}")

pd.DataFrame(RES).to_csv(OUT / "관할별_성능.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(NEG3).to_csv(OUT / "M3_negrec_교차전이.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(DIVERG).to_csv(OUT / "관할간_불일치.csv", index=False, encoding="utf-8-sig")

# ---- 엑셀 (openpyxl 직접 — pd.ExcelWriter 는 이 환경에서 실패) --------------
wb = openpyxl.Workbook()
for i, (nm, df) in enumerate([("관할매핑_레지스트리", pd.DataFrame(REG)),
                              ("관할별_성능", pd.DataFrame(RES)),
                              ("M3_negrec_교차전이", pd.DataFrame(NEG3)),
                              ("관할간_불일치", pd.DataFrame(DIVERG)),
                              ("L1_정본정정_원장", FIXLOG)]):
    ws = wb.active if i == 0 else wb.create_sheet()
    ws.title = nm
    ws.append(list(df.columns))
    for _, r in df.iterrows():
        ws.append(["" if v is None else str(v) for v in r.tolist()])
    ws.freeze_panes = "A2"
    if len(df):
        ws.auto_filter.ref = ws.dimensions
wb.save(OUT / "관할별_라벨레이어.xlsx")

with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "설계": "L0 원본 불변 / L1 GHS 유효성 정정만 / L2 관할별 병렬 투영",
        "L0_불변검증": "통과 — 디스크 y_{ep} 와 메모리 배열 바이트 동일 (3 엔드포인트)",
        "L1_정정셀수": int(len(FIXLOG)),
        # json 은 tuple 키를 못 쓴다 — groupby 결과를 문자열 키로 평탄화
        "L1_정정내역": ({f"{a}/{b}→{c}": int(v) for (a, b, c), v in
                     FIXLOG.groupby(["endpoint", "원본", "정본"]).size().items()}
                    if len(FIXLOG) else {}),
        "관할_확인상태": {jn: JUR[jn]["확인상태"] for jn in JUR},
        "관할간_불일치": DIVERG,
        "관할별_행수": {f"{ep}/{jn}": int(LAYER[(jn, ep)][2].sum())
                   for ep in EPS for jn in JUR},
        "M3_negrec": NEG3,
        "한계": [
            "K_REACH / US_OSHA 투영은 '부분확인' — 고시·HCS 부속서 원문 대조 미완. "
            "레지스트리 CSV 를 규제 담당이 직접 정정할 수 있게 값·근거·확인상태를 "
            "행 단위로 분리해 뒀다.",
            "피부 정본 'NC' 중 EPA 유래가 아닌 244행(공급자 SDS 의 '분류되지 않음')은 "
            "UN 기준에서도 음성으로 뒀다. 공급자 관할이 Cat 3 미채택 지역이면 그 침묵은 "
            "Cat 3 를 배제하지 않으므로 원칙상 판별 불가일 수 있다 — 확인 불가로 남긴다.",
            "감작은 세 관할의 투영표가 동일하다(1/1A/1B 모두 채택). 따라서 감작 성능의 "
            "관할 간 차이는 0 이어야 하며, 이는 구현 검증에도 쓰인다.",
            "M3 는 원문 대조의 대체물이 아니라 계통 오류의 간접 지표다. negrec 라벨이 "
            "옳더라도 그 행들이 화학적으로 특이하면 지수가 올라갈 수 있다.",
        ],
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT}")
