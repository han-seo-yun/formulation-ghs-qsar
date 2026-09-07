#!/usr/bin/env python3
"""v5 후속 측정 2건 — 기존 산출물 무수정, 새 파일만 기록.

과제 1. skin 2A/2B 82행 매핑 정정
  원인: EPA→GHS 매핑표가 눈/피부에 동일 적용됨(EPA II→2A, III→2B).
        2A/2B 는 눈 전용 범주이므로 피부 82행(2A 11 + 2B 71)은 GHS 위반.
  정정: EPA I→1, EPA II→2, EPA III→2, EPA IV→NC  (UN GHS / EU CLP)
  변형: EU CLP 가 Cat 3 를 채택하지 않는 점을 반영해 EPA III→NC 인 대안도 측정.

과제 2. 정채윤 성분 GHS 271 CAS 를 CT 가산 입력에 증강
  기존 ing_ghs_* 는 라벨 출처 phase1_sds 와 같은 p1_*.json 에서 파생(순환 의혹).
  정채윤 값은 PubChem/ECHA 유래로 SDS 와 독립. 기존값과 충돌 0건(전부 신규).

폴드는 baseline y 에서 1회 산출해 전 arm 에 고정한다. y 가 바뀌는 과제 1 에서
폴드를 재계산하면 성능차의 원인 귀속이 불가해진다(과거 group_key 31행 전례).
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    balanced_accuracy_score,
    f1_score,
    matthews_corrcoef,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold

warnings.filterwarnings("ignore")

ROOT = Path("/Users/hanseoyun/Desktop/260830")
SRC = ROOT / "04_모델산출물" / "v4_fixed"          # 읽기 전용
V5 = ROOT / "04_모델산출물" / "input_dataset_v5.xlsx"  # 읽기 전용
CHAEYUN = ROOT / "정채윤_GHS 조사.xlsx"                # 읽기 전용
OUT = ROOT / "04_모델산출물" / "v6_skinmap_ct"
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
        n_jobs=-1, random_state=seed,
    )


# ================================================================ 로드
log("데이터 로드")
X = pd.read_parquet(SRC / "X_formulation.parquet")
Y = pd.read_parquet(SRC / "y_formulation.parquet")
MAN = pd.read_csv(SRC / "feature_role_manifest.csv")
assert (X["Formulation_ID"].values == Y["Formulation_ID"].values).all(), "행 정렬 불일치"

FID = X["Formulation_ID"].astype(str).to_numpy()
GK = Y["group_key"].astype(str).to_numpy()
log(f"X={X.shape}  Y={Y.shape}  group_key nunique={len(set(GK))}")

MANF = MAN[MAN["sheet"] == "formulation"]
CHEM = sorted(c for c in MANF[(MANF["feature_role"] == "chemistry")
                              & (~MANF["exclude_by_default"].astype(bool))]["column"]
              if c != "Formulation_ID")
CT_COLS = sorted(c for c in CHEM if c.startswith(("f_ct_", "ct_")))
assert len(CT_COLS) == 26, f"CT 열 수 {len(CT_COLS)} != 26"
log(f"S1_chemistry={len(CHEM)}열, CT={len(CT_COLS)}열")

xl = pd.ExcelFile(V5)
FORM = xl.parse("formulation")
ING = xl.parse("ingredient")
log(f"formulation={FORM.shape}  ingredient={ING.shape}")

# ================================================================ 과제 1 준비
# NTP EPA→GHS 매핑 실측 재확인 (주장 근거를 코드로 고정)
_m = FORM[FORM["ntp_epa_skin_cat"].notna() & FORM["ntp_skin_ghs_cat"].notna()]
obs_map = (_m.groupby(_m["ntp_epa_skin_cat"].astype(float))["ntp_skin_ghs_cat"]
             .agg(lambda s: sorted(set(s.astype(str)))).to_dict())
log(f"실측 EPA피부→GHS 매핑: {obs_map}")
assert obs_map.get(2.0) == ["2A"] and obs_map.get(3.0) == ["2B"], \
    f"전제 불성립 — 매핑이 예상과 다름: {obs_map}"

SKIN_BAD = FORM["ntp_skin_ghs_cat"].astype(str).isin(["2A", "2B"])
log(f"skin GHS 위반 행: {int(SKIN_BAD.sum())} "
    f"(2A={int((FORM['ntp_skin_ghs_cat'].astype(str)=='2A').sum())}, "
    f"2B={int((FORM['ntp_skin_ghs_cat'].astype(str)=='2B').sum())})")

# 라벨은 v4_fixed 의 y_formulation.parquet 을 쓴다(X 와 행 정렬이 보장된 유일한 소스).
# v5 xlsx 의 formulation 시트는 EPA 매핑 감사용으로만 읽는다.
Y_SKIN_RAW = Y["y_skin"].fillna("").astype(str).to_numpy()
SRC_SKIN = Y["y_skin_src_detail"].fillna("").astype(str).to_numpy()
log("y_skin 분포: " + str(pd.Series(Y_SKIN_RAW).value_counts().to_dict()))

POS_SKIN = {"1", "1A", "1B", "1C", "2", "3", "2A", "2B"}


def skin_target(variant: str):
    """(mask, y) — 라벨은 메모리에서만 만든다. 디스크 y 는 손대지 않는다."""
    raw = Y_SKIN_RAW.copy()
    keep = np.array([v not in ("nan", "None", "") for v in raw])
    if variant == "V0_current":
        pass
    elif variant == "V1_correct_map":          # 2A→2, 2B→2  (UN GHS/EU CLP 정답 매핑)
        raw = np.where(raw == "2A", "2", raw)
        raw = np.where(raw == "2B", "2", raw)
    elif variant == "V2_eu_epa3_neg":          # EPA III(=2B) 를 EU 역치 미달로 음성 처리
        raw = np.where(raw == "2A", "2", raw)
        raw = np.where(raw == "2B", "NC", raw)
    elif variant == "V3_drop82":               # 82행 제외
        keep = keep & ~np.isin(raw, ["2A", "2B"])
    else:
        raise ValueError(variant)
    y = np.isin(raw, list(POS_SKIN)).astype(int)
    return keep, y


# ================================================================ 과제 2 준비
# 정채윤 값 파싱 — H코드로 판정(문자열 서술에 의존하지 않는다)
CY = pd.ExcelFile(CHAEYUN).parse("성분")
CY = CY[CY["GHS조사_눈"].notna()][
    ["cas", "GHS조사_눈", "GHS조사_피부", "GHS조사_감작"]].drop_duplicates("cas")
log(f"정채윤 조사 CAS={len(CY)}")

H2CAT = {"eye": {"H318": "1", "H319": "2"},
         "skin": {"H314": "1", "H315": "2"},
         "sens": {"H317": "1"}}          # H334 = 호흡기 감작 → 피부 감작 아님, 제외
_RX_H = re.compile(r"\bH(3\d\d)\b")
_RX_PCT = re.compile(r"H\d{3}\s*\((\d+(?:\.\d+)?)%\)")


def parse_cy(val: str, ep: str):
    """(cat, tier, pct) 반환. cat: '1'/'2'/'NC'/None(=정보없음)."""
    s = str(val)
    if s.startswith("정보없음"):
        return None, None, None
    tier = "annex_vi" if "Annex VI" in s and "ECHA C&L 통보" not in s else (
        "echa_cl" if "ECHA C&L 통보" in s else "annex_vi")
    if s.startswith("분류대상아님"):
        return "NC", tier, None
    hits = [h for h in ("H" + m for m in _RX_H.findall(s)) if h in H2CAT[ep]]
    if not hits:
        return None, tier, None          # 파싱 불가 → 미지로 둔다(추측 금지)
    cat = min((H2CAT[ep][h] for h in hits), key=lambda c: {"1": 0, "2": 1}[c])
    pm = _RX_PCT.search(s)
    return cat, tier, (float(pm.group(1)) if pm else None)


CY_MAP: dict[str, dict[str, tuple]] = {}
CY_SHUF: dict[str, dict[str, tuple]] = {}   # 반증 대조군: 값만 CAS 간에 순열
unparsed = []
for _, r in CY.iterrows():
    cas = str(r["cas"]).strip()
    d = {}
    for ep, col in (("eye", "GHS조사_눈"), ("skin", "GHS조사_피부"), ("sens", "GHS조사_감작")):
        cat, tier, pct = parse_cy(r[col], ep)
        if cat is None and not str(r[col]).startswith("정보없음"):
            unparsed.append((cas, ep, str(r[col])[:70]))
        d[ep] = (cat, tier, pct)
    CY_MAP[cas] = d
log(f"파싱 실패(정보없음 제외)={len(unparsed)}건" + (f" 예: {unparsed[:3]}" if unparsed else ""))
assert len(unparsed) == 0, f"파싱 실패 존재 — 규칙 보강 필요: {unparsed[:5]}"

_cnt = {ep: pd.Series([v[ep][0] for v in CY_MAP.values()]).value_counts(dropna=False).to_dict()
        for ep in ("eye", "skin", "sens")}
log(f"정채윤 파싱 결과 분포: {_cnt}")

# 반증 대조군 A4 — CAS 키는 그대로 두고 값 벡터만 순열한다.
# 커버리지(어느 CAS 가 채워지는지)는 A1 과 완전히 동일하고 화학 정보만 파괴된다.
# A4 에서도 이득이 남으면 그 이득은 화학이 아니라 '결측이 메워졌다'는 사실 자체의
# 인공물이라는 뜻이므로 A1 의 이득 주장을 폐기해야 한다.
_keys = list(CY_MAP.keys())
_rng = np.random.default_rng(12345)
_perm = _rng.permutation(len(_keys))
for i, k_ in enumerate(_keys):
    CY_SHUF[k_] = CY_MAP[_keys[_perm[i]]]
log(f"A4 순열 대조군 구성: {len(CY_SHUF)} CAS, 고정 시드 12345")

# ---- CT 재계산 ------------------------------------------------------------
CAT1 = {"1", "1A", "1B", "1C"}
CAT2 = {"2", "2A", "2B"}
SEV_CT = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
          "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1, "2A": 2, "2B": 1, "NC": 0},
          "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}


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


def build_ct(arm: str) -> pd.DataFrame:
    """arm 별 f_ct_* / ct_*_ord 재산출. 기존 ing_ghs_* 값은 절대 덮지 않는다."""
    rows = []
    for fid, g in ING_G.groupby("Formulation_ID", sort=False):
        n = len(g)
        row = {"Formulation_ID": fid}
        for ep in ("eye", "skin", "sens"):
            col = f"ing_ghs_{ep}"
            cats, pcts = [], []
            for _, r in g.iterrows():
                base = r[col]
                base = None if (base is None or (isinstance(base, float) and math.isnan(base))
                                or str(base).lower() in ("nan", "none", "")) else str(base)
                cat = base
                if arm != "A0_base" and cat is None:      # 결측만 채운다
                    src = CY_SHUF if arm == "A4_shuffled_control" else CY_MAP
                    cy = src.get(r["cas"])
                    if cy is not None:
                        c, tier, pct = cy[ep]
                        if c is not None:
                            if arm == "A2_aug_nc_unknown" and c == "NC":
                                c = None                  # NC 를 '미지'로 둔다
                            if arm == "A3_aug_annexvi" and tier != "annex_vi":
                                c = None                  # 조화분류만 채택
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
    """재산출 CT 를 X 의 26열 스키마(원-핫 포함)에 주입. 원-핫 카테고리 집합 고정."""
    Xn = base_X.copy()
    for c in CT_COLS:
        if c == "ct_not_applicable":
            continue                                   # CT 입력과 무관 — 원본 유지
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


# 원-핫 레벨 밖의 범주가 생기면 조용히 소실되므로 명시적으로 검사한다
ONEHOT_LEV = {}
for c in CT_COLS:
    m = re.match(r"f_ct_(eye|skin|sens)_cat_(.+)$", c)
    if m:
        ONEHOT_LEV.setdefault(m.group(1), set()).add(m.group(2))
log(f"원-핫 레벨: {ONEHOT_LEV}")


# ================================================================ CV
def run_cv(Xi: np.ndarray, y: np.ndarray, g: np.ndarray, folds: list) -> dict:
    """folds = [(tr, te), ...] × seed. 폴드를 외부에서 주입해 arm 간 고정한다."""
    per_seed = []
    for si, sd in enumerate(SEEDS):
        proba = np.full(len(y), np.nan)
        for tr, te in folds[si]:
            m = rf(sd).fit(Xi[tr], y[tr])
            proba[te] = m.predict_proba(Xi[te])[:, 1]
        assert not np.isnan(proba).any(), "OOF 미충족"
        pred = (proba >= 0.5).astype(int)
        p = y.mean()
        per_seed.append({
            "roc_auc": roc_auc_score(y, proba),
            "mcc": matthews_corrcoef(y, pred),
            "f1": f1_score(y, pred, zero_division=0),
            "ba": balanced_accuracy_score(y, pred),
            "dummy_f1_allpos": 2 * p / (1 + p),
        })
    out = {"_per_seed": per_seed}          # arm 간 paired 통계용 원자료 보존
    for k in per_seed[0]:
        v = [d[k] for d in per_seed]
        out[k] = float(np.mean(v))
        out[k + "_sd"] = float(np.std(v, ddof=1))
    out["n"] = int(len(y))
    out["n_pos"] = int(y.sum())
    out["prevalence"] = float(y.mean())
    out["n_groups"] = int(len(set(g)))
    return out


def make_folds(y: np.ndarray, g: np.ndarray) -> list:
    return [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True,
                                      random_state=sd).split(np.zeros((len(y), 1)), y, g))
            for sd in SEEDS]


RESULTS = []

# ================================================================ 과제 1 실행
log("=" * 60)
log("과제 1 — skin 2A/2B 매핑 정정")
k0, y0 = skin_target("V0_current")
# 무결성: V0 재구성이 디스크의 y_skin_bin 과 완전히 일치해야 한다
_disk = Y.loc[k0, "y_skin_bin"].astype(int).to_numpy()
assert (y0[k0] == _disk).all(), "V0 재구성이 y_skin_bin 과 불일치 — POS_SKIN 정의 재검토"
log("무결성: V0_current == 디스크 y_skin_bin (일치율 1.0000)")
FOLDS_SKIN = make_folds(y0[k0], GK[k0])       # baseline 에서 1회 산출 → 전 arm 고정
log(f"고정 폴드 산출: n={int(k0.sum())}, 양성={int(y0[k0].sum())}")

XCHEM = X[CHEM]
for variant in ["V0_current", "V1_correct_map", "V2_eu_epa3_neg", "V3_drop82"]:
    k, y = skin_target(variant)
    if variant == "V1_correct_map":
        assert (y[k0] == y0[k0]).all(), "V1 이 y_bin 을 바꿨다 — 전제 재검토 필요"
        log("V1: y_bin 이 V0 과 원소 단위 동일함을 확인(정답 매핑은 이진 라벨 불변)")
    if variant == "V3_drop82":
        # 행이 줄어 고정 폴드를 재사용할 수 없다 → 별도 폴드, 비교는 참고용
        folds = make_folds(y[k], GK[k])
        note = "폴드 재산출(행 감소) — V0 과 직접 비교 불가"
    else:
        idx = np.where(k0)[0]
        assert (np.where(k)[0] == idx).all(), "행 집합이 V0 과 달라 폴드 고정 불가"
        folds = FOLDS_SKIN
        note = "V0 폴드 고정"
    Xi = np.nan_to_num(XCHEM[k].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    r = run_cv(Xi, y[k], GK[k], folds)
    r.update({"task": "skin_mapping", "arm": variant, "ep": "skin",
              "features": "S1_chemistry", "note": note})
    RESULTS.append(r)
    log(f"  {variant:18} n={r['n']:4} p={r['prevalence']:.3f} "
        f"AUC={r['roc_auc']:.4f}±{r['roc_auc_sd']:.4f} MCC={r['mcc']:.4f} "
        f"F1={r['f1']:.4f}(dummy {r['dummy_f1_allpos']:.4f}) BA={r['ba']:.4f}  [{note}]")

# ================================================================ 과제 2 실행
log("=" * 60)
log("과제 2 — 정채윤 GHS 를 CT 가산 입력에 증강")

CT_ARMS = ["A0_base", "A1_aug_all", "A2_aug_nc_unknown", "A3_aug_annexvi",
           "A4_shuffled_control"]
CT_TAB = {}
for arm in CT_ARMS:
    ct = build_ct(arm)
    CT_TAB[arm] = ct
    cov = {ep: float(ct[f"f_ct_{ep}_coverage"].mean()) for ep in ("eye", "skin", "sens")}
    nk = {ep: int(ct[f"f_ct_{ep}_n_known"].sum()) for ep in ("eye", "skin", "sens")}
    newlev = {ep: sorted(set(ct[f"f_ct_{ep}_cat"].dropna().astype(str)) - ONEHOT_LEV[ep])
              for ep in ("eye", "skin", "sens")}
    log(f"  {arm:20} coverage={ {k: round(v,3) for k,v in cov.items()} } "
        f"n_known합={nk} 원-핫밖범주={newlev}")
    for ep, lv in newlev.items():
        assert not lv, f"{arm}/{ep}: 원-핫 레벨 밖 범주 {lv} — 소실 위험"

# base 재현 검증
base = CT_TAB["A0_base"]
Xb = ct_to_X(XCHEM, base)
diff = {}
for c in CT_COLS:
    if c == "ct_not_applicable":
        continue
    a = pd.to_numeric(XCHEM[c], errors="coerce").to_numpy(dtype=float)
    b = pd.to_numeric(Xb[c], errors="coerce").to_numpy(dtype=float)
    d = int((~(np.isclose(a, b, equal_nan=True))).sum())
    if d:
        diff[c] = d
log(f"A0_base 재현 불일치 열: {diff if diff else '없음(전 열 일치)'}")
REPRO_OK = not diff

# y 는 현행 그대로(inclusive/all). 폴드는 ep 별 1회 산출 후 전 arm 고정.
FOLDS_EP = {}
YB = {}
for ep in ("eye", "skin", "sens"):
    yb = Y[f"y_{ep}_bin"]
    k = yb.notna().to_numpy()
    y = yb[k].astype(int).to_numpy()
    YB[ep] = (k, y)
    FOLDS_EP[ep] = make_folds(y, GK[k])
    log(f"  {ep}: n={int(k.sum())} 양성={int(y.sum())} p={y.mean():.3f}")

for arm in CT_ARMS:
    Xa = ct_to_X(XCHEM, CT_TAB[arm])
    for ep in ("eye", "skin", "sens"):
        k, y = YB[ep]
        Xi = np.nan_to_num(Xa[k].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        r = run_cv(Xi, y, GK[k], FOLDS_EP[ep])
        r.update({"task": "ct_augment", "arm": arm, "ep": ep,
                  "features": "S1_chemistry", "note": "y 불변·폴드 고정"})
        r["ct_coverage"] = float(CT_TAB[arm][f"f_ct_{ep}_coverage"].mean())
        RESULTS.append(r)
        log(f"  {arm:20} {ep:5} AUC={r['roc_auc']:.4f}±{r['roc_auc_sd']:.4f} "
            f"MCC={r['mcc']:.4f} F1={r['f1']:.4f} BA={r['ba']:.4f} "
            f"cov={r['ct_coverage']:.3f}")

# ================================================================ 산출
R = pd.DataFrame(RESULTS)
PS = R.pop("_per_seed")                     # 시드별 원자료는 CSV 에 넣지 않는다
R.to_csv(OUT / "skinmap_ct_results.csv", index=False, encoding="utf-8-sig")

# 델타표 (과제 2: A0 대비)
# 폴드가 arm 간 고정이므로 시드별 차이는 paired 관측이다. pooled sd 는 상관을 무시해
# 과소검정이 되므로, 정식 통계는 '시드별 델타의 sd' 로 낸다. 시드 5개 → t(df=4).
deltas = []
for ep in ("eye", "skin", "sens"):
    ia = R.index[(R.task == "ct_augment") & (R.arm == "A0_base") & (R.ep == ep)][0]
    a, aps = R.loc[ia], PS.loc[ia]
    for arm in CT_ARMS[1:]:
        ib = R.index[(R.task == "ct_augment") & (R.arm == arm) & (R.ep == ep)][0]
        b, bps = R.loc[ib], PS.loc[ib]
        dseed = [bps[i]["roc_auc"] - aps[i]["roc_auc"] for i in range(len(SEEDS))]
        sd_p = float(np.std(dseed, ddof=1))
        se_p = sd_p / math.sqrt(len(dseed))
        deltas.append({
            "ep": ep, "arm": arm, "auc_base": a.roc_auc, "auc_arm": b.roc_auc,
            "d_roc_auc": b.roc_auc - a.roc_auc,
            "d_per_seed": [round(v, 5) for v in dseed],
            "sd_paired": sd_p,
            "t_paired_df4": (float(np.mean(dseed)) / se_p) if se_p > 0 else None,
            "n_seeds_positive": int(sum(v > 0 for v in dseed)),
            "sd_pooled_참고": float(np.hypot(a.roc_auc_sd, b.roc_auc_sd)),
            "d_mcc": b.mcc - a.mcc,
            "cov_base": a.ct_coverage, "cov_arm": b.ct_coverage})
D = pd.DataFrame(deltas)
D.to_csv(OUT / "ct_augment_deltas.csv", index=False, encoding="utf-8-sig")

# 과제 1 델타 — V0 폴드 고정 arm(V1/V2)만 paired 통계가 성립한다.
sk_deltas = []
i0 = R.index[(R.task == "skin_mapping") & (R.arm == "V0_current")][0]
for arm in ("V1_correct_map", "V2_eu_epa3_neg"):
    ib = R.index[(R.task == "skin_mapping") & (R.arm == arm)][0]
    dseed = [PS.loc[ib][i]["roc_auc"] - PS.loc[i0][i]["roc_auc"] for i in range(len(SEEDS))]
    sd_p = float(np.std(dseed, ddof=1))
    se_p = sd_p / math.sqrt(len(dseed))
    sk_deltas.append({
        "arm": arm, "d_roc_auc": float(np.mean(dseed)),
        "d_per_seed": [round(v, 5) for v in dseed], "sd_paired": sd_p,
        "t_paired_df4": (float(np.mean(dseed)) / se_p) if se_p > 0 else None,
        "d_mcc": R.loc[ib].mcc - R.loc[i0].mcc,
        "p_base": R.loc[i0].prevalence, "p_arm": R.loc[ib].prevalence})
SK = pd.DataFrame(sk_deltas)
SK.to_csv(OUT / "skin_mapping_deltas.csv", index=False, encoding="utf-8-sig")

SUMMARY = {
    "생성시각_주": "Date.now 미사용 — run.log 의 경과초만 기록",
    "baseline": "RandomForestClassifier(n_estimators=300, class_weight='balanced', "
                "min_samples_leaf=2)",
    "cv": f"StratifiedGroupKFold(n_splits={N_SPLITS}, shuffle=True, random_state=seed) "
          "on group_key, 폴드는 baseline y 에서 1회 산출 후 arm 간 고정",
    "seeds": SEEDS,
    "group_key_nunique": len(set(GK)),
    "과제1_skin_매핑": {
        "실측_EPA피부_GHS_매핑": {str(k): v for k, v in obs_map.items()},
        "GHS위반_행수": int(SKIN_BAD.sum()),
        "정답매핑_y_bin_불변": True,
        "결과": R[R.task == "skin_mapping"][
            ["arm", "n", "prevalence", "roc_auc", "roc_auc_sd", "mcc", "f1",
             "dummy_f1_allpos", "ba", "note"]].to_dict("records"),
    },
    "과제2_CT증강": {
        "정채윤_파싱분포": _cnt,
        "A0_base_재현_일치": REPRO_OK,
        "A0_base_불일치열": diff,
        "결과": R[R.task == "ct_augment"][
            ["arm", "ep", "n", "prevalence", "roc_auc", "roc_auc_sd", "mcc", "f1",
             "dummy_f1_allpos", "ba", "ct_coverage"]].to_dict("records"),
        "델타": D.to_dict("records"),
        "A4_반증대조군_해석규칙": "A4 의 d_roc_auc 가 A1 과 통계적으로 구별되지 않으면 "
                            "A1 의 이득은 화학정보가 아니라 결측 메움 인공물이므로 폐기한다.",
    },
    "과제1_델타": SK.to_dict("records"),
    "한계": [
        "정채윤 눈 값에 2A/2B 세분이 없어 '2' 로만 들어온다(해상도 손실).",
        "분류대상아님 중 91 CAS 는 Annex VI 항목 부재 상태의 침묵 NC — "
        "A2_aug_nc_unknown 이 그 영향을 분리하는 변형이다.",
        "ECHA C&L 저통보율 값을 A1 은 구분 없이 채택한다. A3 가 조화분류만 쓰는 대조군.",
        "V3_drop82 는 행이 줄어 폴드가 달라지므로 V0 과 직접 비교 불가.",
        "f_ct_*_coverage / n_known 은 chemistry 피처집합에 없어 X 에 주입되지 않는다.",
    ],
}
with open(OUT / "skinmap_ct_summary.json", "w", encoding="utf-8") as f:
    json.dump(SUMMARY, f, ensure_ascii=False, indent=2, default=str)

log("=" * 60)
log(f"산출: {OUT}/skinmap_ct_results.csv, ct_augment_deltas.csv, skinmap_ct_summary.json")
log("완료")
_LOGF.close()
