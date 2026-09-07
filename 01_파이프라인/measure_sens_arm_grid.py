#!/usr/bin/env python3
"""감작 CT arm 격자 — A0 / A2 / A3 / A3s 를 동일 폴드에서 나란히 잰다.

왜 필요한가 (critic Gap)
-----------------------
현재 권고안은 눈·피부 `A2`, 감작 `A3` 다. 그런데 **감작을 A2 로 하면 어떻게
되는지는 측정된 적이 없다**. A3 를 고른 근거는 커버리지(49.6%)였는데, §10-3 에서
확인했듯 그 49.6% 는 A3 가 NC 를 '아는 값'으로 세기 때문에 나온 수치이고
A2 와 직접 비교할 수 없다. 즉 arm 선택 근거 자체가 규칙 혼용 위에 있었다.

이 스크립트는 감작 엔드포인트만, 동일 폴드·동일 규약에서 네 arm 을 잰다.
눈·피부 CT 컬럼은 A2 로 고정한다(감작 arm 만 바꾼 순효과를 보기 위해).
`f_ct_sens_*` 는 세 엔드포인트 모델 모두의 피처지만 여기서는 감작 라벨만
평가하므로 교차 영향은 없다.

임계값 조정 없음(0.5 고정), 가산 공식 변경 없음, 기존 산출 무수정.
읽기 전용. 산출은 v6_jurisdiction/ 에 추가.
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

import v6_integrated as v6

ROOT = Path("/Users/hanseoyun/Desktop/260830")
SRC = ROOT / "04_모델산출물" / "v4_fixed"
OUT = ROOT / "04_모델산출물" / "v6_jurisdiction"
SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
EPS = ("eye", "skin", "sens")
ARMS = ["A0_base", "A2_aug_nc_unknown", "A3_aug_annexvi", "A3s_aug_annexvi_strict"]
_t0 = time.time()
_LOGF = open(OUT / "run_sens_arm_grid.log", "w", encoding="utf-8")


def log(m):
    line = f"[{time.time()-_t0:7.1f}s] {m}"
    print(line, flush=True)
    _LOGF.write(line + "\n")
    _LOGF.flush()


def rf(seed):
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  min_samples_leaf=2, n_jobs=-1, random_state=seed)


X = pd.read_parquet(SRC / "X_formulation.parquet")
Y = pd.read_parquet(SRC / "y_formulation.parquet")
MAN = pd.read_csv(SRC / "feature_role_manifest.csv")
FID = X["Formulation_ID"].astype(str).to_numpy()
GK = Y["group_key"].astype(str).to_numpy()
MANF = MAN[MAN["sheet"] == "formulation"]
CHEM = sorted(c for c in MANF[(MANF["feature_role"] == "chemistry")
                              & (~MANF["exclude_by_default"].astype(bool))]["column"]
              if c != "Formulation_ID")
CT_COLS = sorted(c for c in CHEM if c.startswith(("f_ct_", "ct_")))
assert len(CT_COLS) == 26

SH = v6.load_sheets(("ingredient",))
ING = SH["ingredient"]
CY = v6.cy_map(ING)                      # 결함 있는 _tier (A3 재현용)
CY_S = v6.cy_map(ING, strict_tier=True)  # 교정판 _tier_src (A3s)
log(f"통합본 로드 — 조사 CAS {len(CY)}")

# 감작 라벨: 전 관할 동일(투영표가 같다). L1 정정은 피부 전용이므로 감작엔 없다.
_raw = Y["y_sens"].fillna("").astype(str).to_numpy()
_TAB = {"1": 1, "1A": 1, "1B": 1, "NC": 0}
b = np.array([_TAB[v] if v != "" else -1 for v in _raw])
keep = b >= 0
y = b[keep]
assert len(y) == 716, f"감작 행수 {len(y)} != 716"
log(f"감작 n={len(y)} 유병률={y.mean():.4f}")

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
             "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens"]].copy()
ING_G["cas"] = ING_G["cas"].astype(str).str.strip()
_GROUPED = list(ING_G.groupby("Formulation_ID", sort=False))


def build_ct(arm_by_ep):
    rows = []
    for fid, g in _GROUPED:
        nrow = len(g)
        row = {"Formulation_ID": fid}
        for ep in EPS:
            arm = arm_by_ep[ep]
            src = CY_S if arm == "A3s_aug_annexvi_strict" else CY
            cats, pcts = [], []
            for _, r in g.iterrows():
                cat = _clean(r[f"ing_ghs_{ep}"])
                if arm != "A0_base" and cat is None:
                    hit = src.get(r["cas"])
                    if hit is not None:
                        c, tier = hit[ep]
                        if c is not None:
                            if arm == "A2_aug_nc_unknown" and c == "NC":
                                c = None
                            elif arm in ("A3_aug_annexvi", "A3s_aug_annexvi_strict") \
                                    and tier != "annex_vi":
                                c = None
                            cat = c
                cats.append(cat)
                pcts.append(r["ing_pct_best"])
            ct = ct_predict(list(zip(cats, pcts)), ep)
            row[f"f_ct_{ep}_cat"] = ct["cat"]
            for k in ("s1", "s2", "add", "n_known"):
                row[f"f_ct_{ep}_{k}"] = ct[k]
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
# A0 는 디스크와 일치해야 한다(전처리 드리프트 검출)
_CT0 = build_ct({ep: "A0_base" for ep in EPS})
_mism = [c for c in CT_COLS if not np.allclose(
    pd.to_numeric(ct_to_X(X0, _CT0)[c], errors="coerce").fillna(-999),
    pd.to_numeric(X0[c], errors="coerce").fillna(-999))]
assert not _mism, f"A0 재현 실패: {_mism[:5]}"
log("A0 == 디스크 — 전처리 드리프트 없음")

# 눈·피부는 A2 고정, 감작만 arm 을 바꾼다
CTS, COV = {}, {}
for a in ARMS:
    conf = {"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown", "sens": a}
    if a == "A0_base":
        conf = {ep: "A0_base" for ep in EPS}   # A0 는 전 엔드포인트 기본선
    CTS[a] = build_ct(conf)
    COV[a] = round(float(CTS[a]["f_ct_sens_coverage"].mean()), 4)
    log(f"  {a}: 감작 커버리지 {COV[a]}")

folds = [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=sd)
              .split(np.zeros((len(y), 1)), y, GK[keep])) for sd in SEEDS]

RES, PER = [], {}
for im in ("nan0", "native"):
    for a in ARMS:
        M = ct_to_X(X0, CTS[a])[CHEM].to_numpy(dtype=np.float64)
        if im == "nan0":
            M = np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0)
        Xi = M[keep]
        aucs, mccs = [], []
        for si, sd in enumerate(SEEDS):
            p = np.full(len(y), np.nan)
            for tr, te in folds[si]:
                p[te] = rf(sd).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
            aucs.append(roc_auc_score(y, p))
            mccs.append(matthews_corrcoef(y, (p >= 0.5).astype(int)))
        PER[(im, a)] = np.array(aucs)
        RES.append({"결측처리": im, "arm": a, "n": int(len(y)),
                    "유병률": round(float(y.mean()), 4), "감작커버리지": COV[a],
                    "ROC_AUC": round(float(np.mean(aucs)), 4),
                    "ROC_AUC_sd": round(float(np.std(aucs, ddof=1)), 4),
                    "MCC": round(float(np.mean(mccs)), 4)})
        log(f"  {im:6} {a:24} AUC={RES[-1]['ROC_AUC']:.4f} MCC={RES[-1]['MCC']:.3f}")

# arm 간 paired 대조 (동일 폴드·동일 시드)
CMP = []
for im in ("nan0", "native"):
    for x, z in (("A0_base", "A2_aug_nc_unknown"), ("A0_base", "A3_aug_annexvi"),
                 ("A2_aug_nc_unknown", "A3_aug_annexvi"),
                 ("A2_aug_nc_unknown", "A3s_aug_annexvi_strict"),
                 ("A3_aug_annexvi", "A3s_aug_annexvi_strict")):
        d = PER[(im, z)] - PER[(im, x)]
        t, pv = stats.ttest_rel(PER[(im, z)], PER[(im, x)])
        CMP.append({"결측처리": im, "기준": x, "대조": z,
                    "Δ": round(float(d.mean()), 4), "t_df4": round(float(t), 2),
                    "p": round(float(pv), 4), "시드양수": int((d > 0).sum())})
        log(f"  {im:6} {z} − {x}: Δ={CMP[-1]['Δ']:+.4f} t={CMP[-1]['t_df4']:+.2f} "
            f"({CMP[-1]['시드양수']}/5)")

pd.DataFrame(RES).to_csv(OUT / "감작_CTarm_격자.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(CMP).to_csv(OUT / "감작_CTarm_대조.csv", index=False, encoding="utf-8-sig")
with open(OUT / "감작_CTarm_요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "질문": "감작 권고안을 A3 로 둔 근거가 커버리지였는데 그 커버리지는 A2 와 "
              "비교 불가한 수치였다. 그러면 감작을 A2 로 하면 어떻게 되는가?",
        "설계": "감작 라벨 716행, 동일 폴드(5폴드×5시드), 임계값 0.5 고정. "
              "눈·피부 CT 컬럼은 A2 로 고정하고 감작 arm 만 교체(A0 행만 전 엔드포인트 기본).",
        "성능": RES, "arm간_대조": CMP,
        "주의": "커버리지는 arm 간 직접 비교 불가(A2 는 NC 를 미지로 버리고 A3 계열은 "
              "아는 값으로 센다). 성능은 동일 라벨·동일 폴드이므로 비교 가능하다.",
    }, f, ensure_ascii=False, indent=2)
log(f"완료 → {OUT}/감작_CTarm_격자.csv")
