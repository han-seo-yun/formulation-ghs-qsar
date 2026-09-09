#!/usr/bin/env python3
"""모순 규명 — CT 증강 이득이 라벨 정의에 의존하는가.

배경(정직한 기록)
----------------
v6_skinmap_ct 보고에서 CT 증강 A2 의 이득을 skin +0.0159, eye +0.0256 으로 보고했다.
그런데 v6_jurisdiction M2 에서 같은 A2 계열(권고조합)의 이득이 skin −0.0002,
eye +0.0028 로 사실상 0 이 나왔다. 두 측정은 내가 낸 것이고 서로 배치된다.
어느 쪽이 옳은지 또는 무엇이 조건을 갈랐는지 귀속시켜야 한다.

1차 실행에서 라벨 변형은 원인이 아님이 드러났다(disk_bin 에서도 Δ가 +0.0019 뿐).
진짜 원인은 **결측 처리 규약**이었다. v6_skinmap_ct 는 이 프로젝트 정규 베이스라인과
같이 `np.nan_to_num(nan=0.0)` 을 썼고, 새 스크립트는 sklearn 1.8 트리의 네이티브
결측 분기를 썼다. S1_chemistry 결측률 19.6%(CT 26열 29.8%)에서 이 차이는 크다.
nan→0 은 '미지'와 '무위험(0)'을 같은 값으로 만들므로, 결측을 실제 값으로 채우는
CT 증강이 큰 이득처럼 보인다 — 즉 그 이득의 상당 부분이 대체(imputation) 인공물이다.

따라서 원인 세 축을 직교로 분리한다.
  (1) 결측 처리   : nan0(정규 베이스라인 규약) / native(트리 네이티브)
  (2) 라벨 정의   : disk_bin(기존 이진) / L1_UN(정본·UN 투영) / L1_EU(정본·EU 투영)
  (3) CT arm 구성 : A0(기본) / A2_all(세 엔드포인트 모두 A2) / 권고(eye·skin A2, sens A3)
2×3×3×2 격자. 폴드는 **라벨 변형별로 1회 산출해 arm·규약 간 고정**한다(라벨이 바뀌면
층화축이 바뀌어 폴드가 달라지므로 라벨 간 비교는 폴드 고정이 불가 — 그 점을 명시한다).

읽기 전용. 산출은 v6_jurisdiction/ 에만 추가한다.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

import v6_integrated as v6
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import matthews_corrcoef, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04_모델산출물" / "v4_fixed"
V6 = v6.V6                                    # 통합 단일 입력(읽기 전용)
OUT = ROOT / "04_모델산출물" / "v6_jurisdiction"
SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
_t0 = time.time()


def log(m):
    print(f"[{time.time()-_t0:7.1f}s] {m}", flush=True)


def rf(s):
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  min_samples_leaf=2, n_jobs=-1, random_state=s)


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
xl = pd.ExcelFile(V6)
FORM_I = xl.parse("formulation").set_index("Formulation_ID")
ING = xl.parse("ingredient")
EPS = ("eye", "skin", "sens")

# ------------------------------------------------------------------ 라벨 변형
RAW = {ep: Y[f"y_{ep}"].fillna("").astype(str).to_numpy() for ep in EPS}
CANON = {ep: RAW[ep].copy() for ep in EPS}
CANON["skin"] = np.where(np.isin(CANON["skin"], ["2A", "2B"]), "2", CANON["skin"])
EPA_SKIN4 = (FORM_I["ntp_epa_skin_cat"].reindex(FID).astype(float) == 4).to_numpy()
_UN = {"eye": {"1": 1, "2": 1, "2A": 1, "2B": 1, "NC": 0},
       "skin": {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 1, "NC": 0}}
_EU = {"eye": {"1": 1, "2": 1, "2A": 1, "2B": 0, "NC": 0},
       "skin": {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 0, "NC": 0}}


def label(ep, variant):
    """(keep, y). UN 은 피부 EPA IV 를 판별불가로 마스크한다."""
    if variant == "disk_bin":
        yb = Y[f"y_{ep}_bin"]
        keep = yb.notna().to_numpy()
        return keep, yb.fillna(0).astype(int).to_numpy()[keep]
    tab = (_UN if variant == "L1_UN" else _EU)[ep]
    c = CANON[ep]
    keep = c != ""
    y = np.array([tab.get(v, 0) for v in c])
    if ep == "skin" and variant == "L1_UN":
        keep = keep & ~EPA_SKIN4
    return keep, y[keep]


# ------------------------------------------------------------------ CT
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
ING_G = ING[["Formulation_ID", "cas", "ing_pct_best",
             "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens"]].copy()
ING_G["cas"] = ING_G["cas"].astype(str).str.strip()


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


def build_ct(arm_by_ep):
    rows = []
    for fid, g in ING_G.groupby("Formulation_ID", sort=False):
        n = len(g)
        row = {"Formulation_ID": fid}
        for ep in EPS:
            arm = arm_by_ep[ep]
            cats, pcts = [], []
            for _, r in g.iterrows():
                b = r[f"ing_ghs_{ep}"]
                b = None if (b is None or (isinstance(b, float) and math.isnan(b))
                             or str(b).lower() in ("nan", "none", "")) else str(b)
                cat = b
                if arm != "A0_base" and cat is None:
                    cy = CY_MAP.get(r["cas"])
                    if cy is not None:
                        c, tier = cy[ep]
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
            for k in ("s1", "s2", "add", "n_known"):
                row[f"f_ct_{ep}_{k}"] = ct[k]
            row[f"f_ct_{ep}_coverage"] = (ct["n_known"] / n) if n else None
            o = SEV_CT[ep].get(ct["cat"]) if ct["cat"] is not None else None
            row[f"f_ct_{ep}_ord"] = o
            row[f"ct_{ep}_ord"] = o
        rows.append(row)
    return pd.DataFrame(rows).set_index("Formulation_ID").reindex(FID)


def ct_to_X(base, ct):
    Xn = base.copy()
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


def ct_matches_disk(Xn: pd.DataFrame) -> list:
    """NaN 이 있으므로 allclose 를 그대로 쓸 수 없다 — 열별 센티넬 치환 후 비교."""
    return [c for c in CT_COLS if not np.allclose(
        pd.to_numeric(Xn[c], errors="coerce").fillna(-999),
        pd.to_numeric(X0[c], errors="coerce").fillna(-999))]


ARMS = {"A0_base": {ep: "A0_base" for ep in EPS},
        "A2_all": {ep: "A2_aug_nc_unknown" for ep in EPS},
        "권고_eyeskinA2_sensA3": {"eye": "A2_aug_nc_unknown",
                                "skin": "A2_aug_nc_unknown", "sens": "A3_aug_annexvi"}}
XM = {}
for an, cfg in ARMS.items():
    log(f"CT 재산출 {an}")
    Xn = ct_to_X(X0, build_ct(cfg))
    if an == "A0_base":
        mism = ct_matches_disk(Xn)
        assert not mism, f"A0 재현 실패 — 불일치 열 {mism[:6]}"
        log("A0 == 디스크 검증 통과 (26 CT 열 전부 일치)")
    M = Xn[CHEM].to_numpy(dtype=np.float64)
    XM[(an, "nan0")] = np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0)
    XM[(an, "native")] = M
IMPUTES = ["nan0", "native"]
log(f"S1_chemistry 결측률 {float(X0.isna().to_numpy().mean()):.4f} / "
    f"CT 26열 {float(X0[CT_COLS].isna().to_numpy().mean()):.4f}")


def run(Xi, y, g, folds):
    ps = []
    for si, sd in enumerate(SEEDS):
        pr = np.full(len(y), np.nan)
        for tr, te in folds[si]:
            pr[te] = rf(sd).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
        ps.append({"roc_auc": roc_auc_score(y, pr),
                   "mcc": matthews_corrcoef(y, (pr >= .5).astype(int))})
    return ps


def folds_of(y, g):
    return [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=sd)
                 .split(np.zeros((len(y), 1)), y, g)) for sd in SEEDS]


R = []
for ep in ("eye", "skin"):
    for lv in ("disk_bin", "L1_UN", "L1_EU"):
        keep, y = label(ep, lv)
        fo = folds_of(y, GK[keep])            # 라벨 변형별 1회 → arm·규약 간 고정
        for im in IMPUTES:
            base = None
            for an in ARMS:
                ps = run(XM[(an, im)][keep], y, GK[keep], fo)
                a = float(np.mean([d["roc_auc"] for d in ps]))
                sd_ = float(np.std([d["roc_auc"] for d in ps], ddof=1))
                mc = float(np.mean([d["mcc"] for d in ps]))
                row = {"endpoint": ep, "결측처리": im, "라벨변형": lv, "CT_arm": an,
                       "n": int(len(y)), "유병률": round(float(y.mean()), 4),
                       "ROC_AUC": round(a, 4), "AUC_sd": round(sd_, 4),
                       "MCC": round(mc, 4)}
                if base is None:
                    base = ps
                else:
                    d = np.array([x["roc_auc"] - b["roc_auc"] for x, b in zip(ps, base)])
                    s = float(np.std(d, ddof=1))
                    row.update({"ΔAUC_vs_A0": round(float(d.mean()), 4),
                                "sd_paired": round(s, 4),
                                "t_df4": round(float(d.mean() / (s / np.sqrt(len(d)))), 2)
                                if s > 0 else None,
                                "시드양수": int((d > 0).sum())})
                R.append(row)
                log(f"  {ep:4} {lv:9} [{im:6}] {an:22} n={row['n']:5} "
                    f"p={row['유병률']:.3f} AUC={a:.4f} MCC={mc:.3f} "
                    f"Δ={row.get('ΔAUC_vs_A0')}")

RD = pd.DataFrame(R)
RD.to_csv(OUT / "CT이득_라벨의존성_귀속.csv", index=False, encoding="utf-8-sig")
with open(OUT / "CT이득_귀속_요약.json", "w", encoding="utf-8") as f:
    json.dump({"목적": "v6_skinmap_ct 의 CT 이득(+0.016/+0.026)과 v6_jurisdiction M2 의 "
                     "이득(≈0) 의 배치를 결측처리 × 라벨정의 × CT arm 직교 격자로 귀속",
               "설계": "폴드는 라벨 변형별 1회 산출 후 arm·규약 간 고정. 라벨 간 비교는 "
                     "폴드가 달라지므로 직접 비교 불가 — arm 내 Δ만 해석한다.",
               "규약_출처": "nan0 = 이 프로젝트 정규 베이스라인 규약"
                        "(measure_v5_performance.py:138, "
                        "smoke_baseline_v4_multitask.py:35, "
                        "measure_target_definition_grid.py:108). "
                        "native = sklearn 1.8 트리 네이티브 결측 분기.",
               "격자": R}, f, ensure_ascii=False, indent=2, default=str)
log(f"→ {OUT}/CT이득_라벨의존성_귀속.csv")
