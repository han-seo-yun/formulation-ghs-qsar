#!/usr/bin/env python3
"""모델 공용 모듈 — 성분(물질) 단위 / 제형(혼합물) 단위 2층 분리의 공통 기반.

왜 분리하는가
------------
v6 까지는 `measure_full_metrics_v6.py` 하나가 **제형 단위 단일 모델**만 학습했다.
성분 정보는 (a) 디스크립터 6모멘트 집계, (b) GHS CT 가산 arm 두 경로로만 들어갔고,
`X_ingredient.parquet` 는 빌드만 되고 어떤 학습 스크립트도 읽지 않았다.
이 모듈은 그 두 층을 각각 독립 모델로 세운다.

  * **성분 단위** = 물질(substance) → GHS 구분. 순수 QSAR. 1행 = 1물질.
    입력은 구조에서 나온 것만(RDKit 디스크립터 · 구조경보 · 지문). 농도·제형맥락은
    성분의 고유 유해성이 아니므로 피처에서 배제한다.
  * **제형 단위** = 혼합물 → GHS 구분. 성분 집계 + 제형 고유 물성 + CT 가산.
    v6 과 동일한 설계이며, 수치가 재현되는지 lock 으로 확인한다.

엔드포인트 배치 (2026-09-09 총책임자 지시)
  eye · skin → `v7_성분모델/` · `v7_제형모델/` 에서 학습·측정한다.
  sens       → **학습 대상에서 제외**. 삭제하지 않고 v6 까지의 측정 기록을
               `v7_감작/` 로 동결 보존한다(`archive_sens.py`). v7 에서 감작을
               새로 학습하는 코드는 만들지 않는다.

  단, sens 의 **CT 열**(`f_ct_sens_*`)은 eye/skin 모델의 피처에 그대로 남는다.
  이것은 라벨이 아니라 혼합물 가산식으로 계산한 디스크립터이고, v6 이 학습에 쓴
  피처 구성과 동일해야 재현 lock 이 성립한다. 라벨 `y_sens` 는 쓰지 않는다.

불변 규칙 (README 데이터 규칙 준수)
  * 값을 만들지 않는다. 결측은 결측으로 남긴다.
  * 임계값은 0.5 고정. 사후 최적값(0.298~0.400)은 참고열로만 병기한다.
  * 라벨 유도원(`ing_ghs_*`, `y_*`, S11 판독 등)은 피처로 절대 쓰지 않는다.
  * `measure_full_metrics_v6.py` 는 수정하지 않는다 — v6 측정치의 재현 근거다.
"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                             confusion_matrix, f1_score, matthews_corrcoef,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04_모델산출물" / "v4_fixed"
V6 = ROOT / "04_모델산출물" / "input_dataset_v6.xlsx"
V6_METRICS = ROOT / "04_모델산출물" / "v6_jurisdiction" / "전체지표_ROC_F1.csv"
SEEDS = [0, 1, 2, 3, 4]
N_SPLITS = 5
NEGREC = "sds_v1_negative_recovered"
THRESHOLD = 0.5          # 고정. 조정 금지 — 근거는 각 산출물 요약 json 참조.
EPS_ALL = ("eye", "skin", "sens")

# ------------------------------------------------------------------ 결측 규약
#   nan0        결측 셀을 0 으로 채운다. v6 까지의 기본값이며 재현 대조용으로만 남긴다.
#   native      결측을 결측으로 둔다. sklearn ≥1.4 의 트리 네이티브 NaN 분기를 쓴다.
#   native_복원  native + 빌드 단계에서 결측이 0 으로 굳어버린 열을 결측으로 되돌린다.
# 대표값은 native_복원 이다 — "값을 만들지 않는다" 규칙에 부합하는 유일한 규약이다.
IMPUTES = ("nan0", "native", "native_복원")
CANON_IMPUTE = "native_복원"

# build_input_v5.py 가 `a = row["f_pct_surf_anionic"] or 0.0` 관용구로 계산해서
# **조성 미상(f_pct_sum_known 결측) 행까지 0 이 들어간** 열. 구성 성분 4열은 모두
# 결측(281행)인데 합계만 0 이라 결측 플래그(`*_isna`)조차 만들어지지 않았다.
# 감사 근거: audit_zero_vs_missing.py — 이 두 열 외에 제형·성분 행렬에 임퓨트된
# 0 은 없다(나머지 0 은 전부 '해당 역할 없음'·'성분 1종이라 분산 0' 같은 실측값).
ZERO_IS_MISSING = ("f_pct_surf_total", "f_surf_anionic_nonionic")

_T0 = time.time()


def make_logger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w", encoding="utf-8")

    def log(m):
        line = f"[{time.time()-_T0:7.1f}s] {m}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()
    return log


def rf(seed):
    """v6 과 동일한 추정기. 바꾸면 재현 lock 이 깨진다."""
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  min_samples_leaf=2, n_jobs=-1, random_state=seed)


# ============================================================ 관할별 투영표 (L2)
_UN_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 1, "NC": 0}
_EU_EYE = {"1": 1, "2": 1, "2A": 1, "2B": 0, "NC": 0}
_UN_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 1, "NC": 0}
_EU_SKIN = {"1": 1, "1A": 1, "1B": 1, "1C": 1, "2": 1, "3": 0, "NC": 0}
_SENS = {"1": 1, "1A": 1, "1B": 1, "NC": 0}
JUR = {"UN_GHS": {"eye": _UN_EYE, "skin": _UN_SKIN, "sens": _SENS},
       "EU_CLP": {"eye": _EU_EYE, "skin": _EU_SKIN, "sens": _SENS},
       "K_REACH": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS},
       "US_OSHA": {"eye": _UN_EYE, "skin": _EU_SKIN, "sens": _SENS}}

# ------------------------------------------------------------ 대표(전관할) 기준
# 관할 4개의 투영표 차이는 실제로 스위치 2개뿐이다.
#   눈  구분 2B 를 '분류'로 볼 것인가   → UN·K-REACH·US-OSHA = 예 / EU-CLP = 아니오
#   피부 구분 3 을 '분류'로 볼 것인가   → UN-GHS = 예 / EU-CLP·K-REACH·US-OSHA = 아니오
# 두 스위치를 동시에 만족하는 관할이 K_REACH 와 US_OSHA 이고, 둘의 투영표는
# 완전히 동일하다. 즉 이 하나를 대표로 고정하면 국내(K-REACH)·미국(HazCom) 두
# 관할에 투영 손실이 0 이고, 눈은 UN GHS 원문과도 같다. 다른 관할로 갈아타려면
# 이 상수만 바꾸면 되고, 나머지 관할도 계속 부가 산출로 함께 측정한다.
CANON_JUR = "K_REACH"

JUR_DOC = {
    "UN_GHS": "UN GHS 원문 (Purple Book). 눈 2B·피부 구분3 모두 '분류'로 본다",
    "EU_CLP": "EU 규정 (EC) No 1272/2008 (CLP) + Annex VI 조화분류. "
              "눈 2B 없음(2A 만 분류), 피부 구분3 채택 안 함",
    "K_REACH": "한국 — 화학물질의 분류·표시 및 물질안전보건자료에 관한 기준 "
               "(고용노동부고시) / 화학물질등록평가법. 눈 2B 채택, 피부 구분3 채택 안 함",
    "US_OSHA": "미국 — 29 CFR 1910.1200 Hazard Communication (HazCom 2012, "
               "GHS rev.3 기반). 투영표가 K_REACH 와 동일하다",
}

CAT1 = {"1", "1A", "1B", "1C"}
CAT2 = {"2", "2A", "2B"}
SEV_CT = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
          "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1,
                   "2A": 2, "2B": 1, "NC": 0},
          "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}
# 라벨 충돌 해소용 심각도 순위(성분 모델). 같은 물질에 상이 구분이 붙은 경우
# 규제 보수성 원칙에 따라 더 심한 쪽을 채택한다 — 값을 새로 만드는 것이 아니라
# 이미 수집된 두 값 중 하나를 고르는 것이다. 충돌 목록은 CSV 로 남긴다.
SEV_PICK = {"eye": {"NC": 0, "2B": 1, "2": 2, "2A": 2, "1": 3},
            "skin": {"NC": 0, "3": 1, "2B": 1, "2": 2, "2A": 2,
                     "1C": 3, "1B": 3, "1A": 4, "1": 4},
            "sens": {"NC": 0, "1B": 1, "1": 2, "1A": 3}}


# ================================================================ 지표 · 폴드
def all_metrics(y, p):
    """임계값 0.5 고정. ROC-AUC / PR-AUC 만 임계값 불변이다."""
    pred = (p >= THRESHOLD).astype(int)
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
    """참고용 상한 — OOF 후향 선택이므로 성능 주장에 쓸 수 없다."""
    ths = np.unique(np.round(p, 3))
    return max(((f1_score(y, (p >= t).astype(int), zero_division=0), t) for t in ths),
               key=lambda x: x[0])


def make_folds(y, g):
    return [list(StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True,
                                      random_state=sd).split(np.zeros((len(y), 1)), y, g))
            for sd in SEEDS]


def cv_eval(Xi, y, folds, meta):
    """5시드 × 5폴드 OOF. meta 를 그대로 앞쪽 열로 붙인 1행 dict 를 돌려준다."""
    per, oof_last = [], None
    for si, sd in enumerate(SEEDS):
        p = np.full(len(y), np.nan)
        for tr, te in folds[si]:
            p[te] = rf(sd).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
        assert not np.isnan(p).any(), "OOF 미채움 — 폴드 구성 결함"
        per.append(all_metrics(y, p))
        oof_last = p
    fb, tb = f1_best(y, oof_last)
    row = dict(meta)
    row.update({"n": int(len(y)), "양성": int(y.sum()),
                "유병률": round(float(y.mean()), 4)})
    for kk in per[0]:
        v = [d[kk] for d in per]
        if kk in ("tp", "fp", "fn", "tn"):
            row[kk] = round(float(np.mean(v)), 1)
        else:
            name = kk.upper() if kk in ("mcc", "ba") else kk
            row[name] = round(float(np.mean(v)), 4)
            row[name + "_sd"] = round(float(np.std(v, ddof=1)), 4)
    # 이 두 열만 5시드 평균이 아니라 마지막 시드(seed=4) 단독이다 — 열 이름에 박아
    # 다른 열과 짝지어 비교하는 것을 막는다.
    row["F1_최적임계값_참고_seed4"] = round(float(fb), 4)
    row["최적임계값_참고_seed4"] = round(float(tb), 3)
    return row


def is_canon(jns):
    """이 관할 묶음이 대표(전관할) 기준인가. jns 는 리스트 또는 '+' 결합 문자열."""
    return CANON_JUR in (jns.split("+") if isinstance(jns, str) else list(jns))


def mark_canon(R, canon_impute=CANON_IMPUTE, canon_ct=None, canon_feat=None):
    """대표 4모델(성분·제형 × 눈·피부) 행에 `대표` = 1 을 세운다.

    대표 = 대표관할(K_REACH 계열) × 대표결측규약 × (지정 시) 대표 CT arm·피처.
    나머지 행은 부가 산출로 같은 파일에 남긴다 — 기준을 갈아탈 때 재실행이 필요
    없도록 하기 위한 것이고, 대표 외 행을 성능 주장에 쓰지 않는다.
    """
    m = R["관할"].map(is_canon) & (R["결측처리"] == canon_impute)
    if canon_ct is not None and "CT" in R.columns:
        m &= R["CT"] == canon_ct
    if canon_feat is not None and "피처" in R.columns:
        m &= R["피처"] == canon_feat
    R = R.copy()
    R["대표"] = m.astype(int)
    assert int(R["대표"].sum()) == R["endpoint"].nunique(), \
        f"대표 행 {int(R['대표'].sum())}개 — 엔드포인트당 1개여야 한다"
    return R


OUT_COLS = ["대표", "단위", "endpoint", "관할", "피처", "결측처리", "CT", "n", "양성", "유병률",
            "roc_auc", "roc_auc_sd", "pr_auc", "pr_auc_sd",
            "f1_pos", "f1_pos_sd", "f1_neg", "f1_macro",
            "precision", "recall", "specificity", "MCC", "BA", "accuracy",
            "tp", "fp", "fn", "tn", "F1_최적임계값_참고_seed4", "최적임계값_참고_seed4"]


def order_cols(R):
    return R[[c for c in OUT_COLS if c in R.columns]
             + [c for c in R.columns if c not in OUT_COLS]]


# ==================================================================== 제형 단위
class FormulationData:
    """제형 단위 입력. v6 과 동일한 전처리를 그대로 수행한다(수치 재현 대상)."""

    def __init__(self, log):
        X = pd.read_parquet(SRC / "X_formulation.parquet")
        Y = pd.read_parquet(SRC / "y_formulation.parquet")
        MAN = pd.read_csv(SRC / "feature_role_manifest.csv")
        assert (X["Formulation_ID"].values == Y["Formulation_ID"].values).all()
        self.FID = X["Formulation_ID"].astype(str).to_numpy()
        self.GK = Y["group_key"].astype(str).to_numpy()
        self.N = len(self.FID)
        MANF = MAN[MAN["sheet"] == "formulation"]
        self.CHEM = sorted(
            c for c in MANF[(MANF["feature_role"] == "chemistry")
                            & (~MANF["exclude_by_default"].astype(bool))]["column"]
            if c != "Formulation_ID")
        self.CT_COLS = sorted(c for c in self.CHEM if c.startswith(("f_ct_", "ct_")))
        assert len(self.CT_COLS) == 26, f"CT 열 {len(self.CT_COLS)} != 26"

        xl = pd.ExcelFile(V6)
        self.FORM_I = xl.parse("formulation").set_index("Formulation_ID")
        self.ING = xl.parse("ingredient")
        assert len(self.FORM_I) == 1675 and len(self.ING) == 5287, "v6 행수 이탈"
        for _c in (f"ing_ghs_indep_{e}_{k}" for e in EPS_ALL for k in ("cat", "tier")):
            assert _c in self.ING.columns, f"v6 통합열 {_c} 없음 — build_input_v6 먼저"

        RAW = {ep: Y[f"y_{ep}"].fillna("").astype(str).to_numpy() for ep in EPS_ALL}
        self.SRCD = {ep: Y[f"y_{ep}_src_detail"].fillna("").astype(str).to_numpy()
                     for ep in EPS_ALL}
        # L1 정본: skin 2A/2B 는 GHS 에 없는 구분이므로 2 로 정정한다(82행).
        self.CANON = {ep: RAW[ep].copy() for ep in EPS_ALL}
        _fix = sum(1 for i, v in enumerate(self.CANON["skin"]) if v in ("2A", "2B"))
        self.CANON["skin"] = np.array(
            ["2" if v in ("2A", "2B") else v for v in self.CANON["skin"]], dtype=object)
        assert _fix == 82, f"L1 정정 {_fix} != 82"
        self.EPA_SKIN4 = (self.FORM_I["ntp_epa_skin_cat"].reindex(self.FID)
                          .astype(float) == 4).to_numpy()
        assert int(self.EPA_SKIN4.sum()) == 444
        self.IS_NEGREC = {ep: (self.SRCD[ep] == NEGREC) for ep in EPS_ALL}
        assert {ep: int(self.IS_NEGREC[ep].sum()) for ep in EPS_ALL} == \
            {"eye": 109, "skin": 128, "sens": 211}
        self.X0 = X[self.CHEM].copy()
        # 조성 미상 행 — 성분 농도가 하나도 파싱되지 않아 역할별 농도합이 정의되지
        # 않는다. ZERO_IS_MISSING 열을 되돌릴 때 이 마스크만 건드린다.
        self.NOPCT = pd.to_numeric(X["f_pct_sum_known"], errors="coerce").isna().to_numpy()
        assert int(self.NOPCT.sum()) == 281, f"조성미상 {int(self.NOPCT.sum())} != 281"
        self.log = log

    # ---------------------------------------------------------------- L2 투영
    def project(self, jn, ep):
        tab = JUR[jn][ep]
        b = np.full(self.N, -1, dtype=int)
        for i, v in enumerate(self.CANON[ep]):
            if v == "":
                continue
            assert tab.get(v) is not None, f"{jn}/{ep} 투영표에 '{v}' 없음"
            b[i] = tab[v]
        keep = b >= 0
        if ep == "skin" and tab.get("3") == 1:
            keep = keep & ~self.EPA_SKIN4
        if ep == "eye":
            keep = keep & ~self.IS_NEGREC["eye"]     # 현행: negrec 마스크 적용
        return b, keep

    def layers(self, eps):
        L = {(jn, ep): self.project(jn, ep) for jn in JUR for ep in eps}
        EXP = {("eye", "UN_GHS"): 1044, ("eye", "EU_CLP"): 1044,
               ("skin", "UN_GHS"): 615, ("skin", "EU_CLP"): 1059,
               ("sens", "UN_GHS"): 716}
        for (ep, jn), n in EXP.items():
            if ep in eps:
                got = int(L[(jn, ep)][1].sum())
                assert got == n, f"{jn}/{ep} 행수 {got} != {n}"
        return L

    # -------------------------------------------------------------------- CT
    def _cy_of(self, row, ep):
        out = []
        for k in ("cat", "tier"):
            v = row[f"ing_ghs_indep_{ep}_{k}"]
            out.append(None if (v is None or (isinstance(v, float) and math.isnan(v))
                                or str(v) in ("nan", "None", "")) else str(v))
        return out[0], out[1]

    def build_ct(self, arm_by_ep, eps):
        cols = ["Formulation_ID", "cas", "ing_pct_best"] \
            + [f"ing_ghs_{ep}" for ep in eps] \
            + [f"ing_ghs_indep_{ep}_{k}" for ep in eps for k in ("cat", "tier")]
        G = self.ING[cols].copy()
        G["cas"] = G["cas"].astype(str).str.strip()
        rows = []
        for fid, g in G.groupby("Formulation_ID", sort=False):
            n = len(g)
            row = {"Formulation_ID": fid}
            for ep in eps:
                arm = arm_by_ep[ep]
                cats, pcts = [], []
                for _, r in g.iterrows():
                    base = r[f"ing_ghs_{ep}"]
                    base = None if (base is None
                                    or (isinstance(base, float) and math.isnan(base))
                                    or str(base).lower() in ("nan", "none", "")) \
                        else str(base)
                    cat = base
                    if arm != "A0_base" and cat is None:
                        c, tier = self._cy_of(r, ep)
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
        return pd.DataFrame(rows).set_index("Formulation_ID").reindex(self.FID)

    def ct_to_X(self, ct, eps):
        Xn = self.X0.copy()
        for c in self.CT_COLS:
            if c == "ct_not_applicable":
                continue
            m = re.match(r"f_ct_(eye|skin|sens)_cat_(.+)$", c)
            if m:
                ep, lev = m.group(1), m.group(2)
                if ep not in eps:
                    continue
                cur = ct[f"f_ct_{ep}_cat"]
                want = None if lev == "__NA__" else lev
                Xn[c] = (cur.isna() if want is None
                         else (cur.astype(str) == want)).astype(int).to_numpy()
            elif c in ct.columns:
                Xn[c] = ct[c].to_numpy(dtype=float)
        return Xn

    def matrices(self):
        """(CT arm × 결측규약) 행렬 4종. v6 과 동일한 검증 assert 를 유지한다.

        CT 는 **항상 3 엔드포인트 전부** 계산한다. sens 를 학습에서 뺐어도
        `f_ct_sens_*` 는 라벨이 아니라 혼합물 가산 디스크립터이고, v6 이 쓴 피처
        구성과 같아야 재현 lock 이 성립하기 때문이다. 라벨 `y_sens` 는 쓰지 않는다.
        """
        eps = EPS_ALL
        CT0 = self.build_ct({ep: "A0_base" for ep in eps}, eps)
        X_A0 = self.ct_to_X(CT0, eps)
        mism = [c for c in self.CT_COLS if not np.allclose(
            pd.to_numeric(X_A0[c], errors="coerce").fillna(-999),
            pd.to_numeric(self.X0[c], errors="coerce").fillna(-999))]
        assert not mism, f"A0 재현 실패: {mism[:5]}"
        rec_arm = {"eye": "A2_aug_nc_unknown", "skin": "A2_aug_nc_unknown",
                   "sens": "A3_aug_annexvi"}
        CT_R = self.build_ct({ep: rec_arm[ep] for ep in eps}, eps)
        X_R = self.ct_to_X(CT_R, eps)
        COV = {("A0", "eye"): 0.240, ("A0", "skin"): 0.241, ("A0", "sens"): 0.241,
               ("REC", "eye"): 0.330, ("REC", "skin"): 0.296, ("REC", "sens"): 0.496}
        for tag, ct in (("A0", CT0), ("REC", CT_R)):
            for ep in eps:
                got = float(ct[f"f_ct_{ep}_coverage"].mean())
                assert abs(got - COV[(tag, ep)]) < 0.002, \
                    f"{tag}/{ep} coverage {got:.3f} 이탈"
        self.log("CT A0 == 디스크 / 커버리지 == 검증값 — 전처리 드리프트 없음")

        # 빌드 단계에서 0 으로 굳은 결측을 되돌린다. nan0/native 행렬은 손대지 않아야
        # v6 재현 lock 이 성립하므로, 복원은 native_복원 규약에서만 적용한다.
        zi = [self.CHEM.index(c) for c in ZERO_IS_MISSING if c in self.CHEM]
        n_rest = len(zi) * int(self.NOPCT.sum())
        self.log(f"결측 복원 대상: {[c for c in ZERO_IS_MISSING if c in self.CHEM]} "
                 f"× 조성미상 {int(self.NOPCT.sum())}행 = {n_rest}셀 (native_복원 규약에만 적용)")

        def as_mat(df, im):
            M = df[self.CHEM].to_numpy(dtype=np.float64)
            if im == "nan0":
                return np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0)
            if im == "native_복원":
                M = M.copy()
                for j in zi:
                    assert not np.isnan(M[self.NOPCT, j]).any(), \
                        f"{self.CHEM[j]}: 조성미상 행에 이미 결측이 있다 — 감사 전제 이탈"
                    M[self.NOPCT, j] = np.nan
            return M
        return {(ct, im): as_mat(df, im)
                for ct, df in (("CT_A0", X_A0), ("CT_권고", X_R))
                for im in IMPUTES}


def ct_predict(pairs, endpoint):
    """GHS 혼합물 가산식(CT). 성분별 구분 × 성분별 농도 → 혼합물 구분."""
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


def jur_groups(data, eps, layers):
    """라벨 벡터가 동일한 관할끼리 묶어 중복 계산을 막는다."""
    out = []
    for ep in eps:
        seen = {}
        for jn in JUR:
            b, k = layers[(jn, ep)]
            seen.setdefault((b[k].tobytes(), k.tobytes()), []).append(jn)
        for jns in seen.values():
            out.append((ep, jns))
    return out


# ================================================================== 성분 단위
# 성분 모델의 피처는 **구조에서 나온 것만** 쓴다. 아래는 명시 배제 목록이다.
#   라벨 유도원 : has_ghs_label · has_y · has_toxicity · has_ntp_label · trainable*
#   제형 맥락   : formulation_type_* (물질 고유 유해성이 아니다)
#   농도·정합   : pct_* · ing2_pct_* · ing_pct_* · pct_repaired · align_ok
#   수집 이력   : june_audit_recovered · ing_name_corrected · ing_cas_corrected
ING_DROP_EXACT = {
    "Formulation_ID", "ing_idx", "pct_value", "align_ok", "has_toxicity",
    "has_ghs_label", "has_ntp_label", "has_y", "trainable", "trainable_ntp",
    "trainable_ghs", "ing2_pct_value", "ing2_pct_lo", "ing2_pct_hi", "ing2_parse_ok",
    "ing_pct_best", "june_audit_recovered", "ing_name_corrected", "ing_cas_corrected",
    "ing_pct_raw", "pct_repaired", "ing_pct_rank", "ing_is_max_pct",
    "pct_value_isna", "ing2_pct_value_isna", "ing2_pct_lo_isna", "ing2_pct_hi_isna",
    "ing_pct_best_isna", "ing_pct_raw_isna", "ing_pct_rank_isna",
}
ING_DROP_PREFIX = ("formulation_type_",)


class SubstanceData:
    """성분(물질) 단위 입력. 1행 = 1물질.

    물질 동일성 키
      RDKit InChIKey 앞 14자(골격 블록). `canonicalize_ingredient_smiles.py` 가
      이미 확립한 규칙을 그대로 쓴다 — 입체표기만 다른 SMILES 를 한 물질로 묶고,
      골격이 다르면 다른 물질로 둔다.

    라벨
      두 독립 출처의 합집합. 행 수준에서 교집합은 0 이다(측정으로 확인).
        ing_ghs_{ep}            : Phase 1 수집 (PubChem LCSS / ECHA C&L)
        ing_ghs_indep_{ep}_cat  : 정채윤 GHS 조사 (ACTIVE 승격분)
      같은 물질에 상이 구분이 붙으면 SEV_PICK 로 더 심한 쪽을 채택하고 목록을
      CSV 로 남긴다. 없는 값을 만들지는 않는다.

    누출 차단
      * 제형이 아니라 물질이 CV 단위다. 같은 물질이 여러 제형에 나오던 중복을
        먼저 접기 때문에, 제형 단위 모델의 group_key 누출 문제가 원리적으로 없다.
      * 그 위에 Murcko 골격을 group 으로 준 StratifiedGroupKFold 를 쓴다.
        유사 골격 동족체가 train/test 로 쪼개져 성능이 부풀는 것을 막는다.
    """

    def __init__(self, log, eps, out_dir):
        from rdkit import Chem, RDLogger
        from rdkit.Chem.Scaffolds import MurckoScaffold
        RDLogger.DisableLog("rdApp.*")
        self.log = log
        self.eps = tuple(eps)
        ING = pd.ExcelFile(V6).parse("ingredient")
        assert len(ING) == 5287, "ingredient 행수 이탈"
        XI = pd.read_parquet(SRC / "X_ingredient.parquet")
        assert len(XI) == 5287 and (XI["Formulation_ID"].astype(str).to_numpy()
                                   == ING["Formulation_ID"].astype(str).to_numpy()).all(), \
            "X_ingredient 와 ingredient 시트 행 정렬 불일치"

        # ---- 물질 키 -------------------------------------------------------
        sm = ING["smiles"].astype(str).str.strip()
        has_sm = ING["smiles"].notna() & (sm != "") & (sm.str.lower() != "nan")
        keymap, scafmap, n_mol_fail, inchi_fail = {}, {}, 0, []
        for s in sorted(set(sm[has_sm])):
            m = Chem.MolFromSmiles(s)
            if m is None:
                n_mol_fail += 1
                continue
            k = Chem.MolToInchiKey(m)
            if not k or len(k) < 14:
                # InChI 생성 실패(주로 유기금속). 빈 문자열을 키로 쓰면 서로 다른
                # 물질이 한 행으로 합쳐진다 — canonical SMILES 로 대체해 분리를 보장한다.
                keymap[s] = "SMI:" + Chem.MolToSmiles(m)
                inchi_fail.append(s)
            else:
                keymap[s] = k[:14]
            try:
                scafmap[s] = MurckoScaffold.MurckoScaffoldSmiles(mol=m) or "__ACYCLIC__"
            except Exception:
                scafmap[s] = "__ACYCLIC__"
        ING = ING.assign(_skel=sm.where(has_sm).map(keymap),
                         _scaf=sm.where(has_sm).map(scafmap))
        log(f"물질 키: SMILES 보유 {int(has_sm.sum())}행 · SMILES 파싱실패 "
            f"{n_mol_fail}종 · InChIKey 생성실패 {len(inchi_fail)}종(canonical SMILES 로 "
            f"대체) · 고유 물질 {ING['_skel'].nunique()}종")
        assert ING["_skel"].dropna().map(lambda v: v != "").all(), "빈 물질키 발생"

        # ---- 라벨 합집합 ---------------------------------------------------
        lab, src = {}, {}
        for ep in self.eps:
            a = ING[f"ing_ghs_{ep}"].where(ING[f"ing_ghs_{ep}"].notna())
            b = ING[f"ing_ghs_indep_{ep}_cat"]
            assert int((a.notna() & b.notna()).sum()) == 0, \
                f"{ep}: 두 출처가 행 수준에서 겹친다 — 충돌 해소 규칙 재설계 필요"
            lab[ep] = a.astype("string").fillna(b.astype("string"))
            src[ep] = np.where(a.notna(), "phase1",
                               np.where(b.notna(), "정채윤", ""))

        # ---- 피처 열 선정 ---------------------------------------------------
        feats = [c for c in XI.columns
                 if c not in ING_DROP_EXACT and not c.startswith(ING_DROP_PREFIX)]
        self.log(f"성분 피처 후보 {len(feats)}열 (X_ingredient {XI.shape[1]}열에서 "
                 f"라벨유도원·제형맥락·농도열 {XI.shape[1]-len(feats)}열 배제)")

        # ---- 물질 단위로 접기 -----------------------------------------------
        base = XI[feats].copy()
        base["_skel"] = ING["_skel"].to_numpy()
        base["_scaf"] = ING["_scaf"].to_numpy()
        num = [c for c in feats if pd.api.types.is_numeric_dtype(base[c])
               or pd.api.types.is_bool_dtype(base[c])]
        # 구조가 같으면 디스크립터도 같아야 한다 — first 로 접고 실제로 같은지 검사.
        S = base[base["_skel"].notna()].groupby("_skel", sort=True)
        agg = S[num].first()
        nun = S[["MolWt", "TPSA", "MolLogP"]].nunique()
        bad = nun[(nun > 1).any(axis=1)]
        # 동일 골격 + 상이 디스크립터 = 입체이성질체 표기차. 대표값 1개로 접는다.
        if len(bad):
            self.log(f"  ※ 동일 골격·상이 디스크립터 {len(bad)}물질 — 입체표기 차이. "
                     f"첫 값을 대표로 접음(canonicalize 규칙과 동일)")
        agg["_scaf"] = S["_scaf"].first()
        self.n_sub = len(agg)

        # ---- 라벨을 물질 단위로 접기 ----------------------------------------
        conflicts = []
        Yl, Ys = {}, {}
        for ep in self.eps:
            d = pd.DataFrame({"_skel": ING["_skel"], "lab": lab[ep], "src": src[ep]})
            d = d[d["_skel"].notna() & d["lab"].notna()]
            picked, psrc = {}, {}
            for sk, g in d.groupby("_skel", sort=False):
                vals = list(dict.fromkeys(g["lab"].tolist()))
                if len(vals) > 1:
                    conflicts.append({"endpoint": ep, "물질키": sk,
                                      "값들": " | ".join(vals),
                                      "출처들": " | ".join(sorted(set(g["src"]))),
                                      "채택": max(vals, key=lambda v: SEV_PICK[ep].get(v, -1)),
                                      "성분행수": len(g)})
                v = max(vals, key=lambda v: SEV_PICK[ep].get(v, -1))
                picked[sk] = v
                psrc[sk] = "+".join(sorted(set(g.loc[g["lab"] == v, "src"])))
            Yl[ep] = pd.Series(picked).reindex(agg.index)
            Ys[ep] = pd.Series(psrc).reindex(agg.index)
            self.log(f"  {ep}: 라벨보유 물질 {int(Yl[ep].notna().sum())}종 "
                     f"(성분행 {len(d)}행에서 접음)")
        if conflicts:
            C = pd.DataFrame(conflicts)
            out_dir.mkdir(parents=True, exist_ok=True)
            C.to_csv(out_dir / "라벨충돌_물질.csv", index=False, encoding="utf-8-sig")
            self.log(f"  라벨충돌 {len(C)}건 → 라벨충돌_물질.csv (SEV_PICK 로 심각한 쪽 채택)")

        self.NUM = num
        self.AGG = agg
        self.LAB = Yl
        self.LABSRC = Ys
        self.SCAF = agg["_scaf"].fillna("__NA__").astype(str).to_numpy()
        self.ING = ING
        self.XI = XI

        # ---- 지문 arm -------------------------------------------------------
        self.FP = self._load_fp(ING)

    def _load_fp(self, ING):
        """MACCS 167 + Morgan(빈도 하위 비트 제거). 물질 단위로 접는다."""
        out = {}
        skel = ING["_skel"].to_numpy()
        for nm, fn, minc in (("maccs", "fp_ing_maccs.npz", 1),
                             ("morgan", "fp_ing_morgan.npz", 5)):
            z = np.load(SRC / fn, allow_pickle=True)
            assert (z["fid"].astype(str) == ING["Formulation_ID"].astype(str).to_numpy()).all()
            fp = z["fp"]
            D = pd.DataFrame(fp)
            D["_skel"] = skel
            D = D[D["_skel"].notna()].groupby("_skel", sort=True).max()
            D = D.reindex(self.AGG.index).fillna(0)
            cnt = D.to_numpy().sum(0)
            keep = np.where(cnt >= minc)[0]
            out[nm] = (D.to_numpy(dtype=np.float64)[:, keep], keep)
            self.log(f"  지문 {nm}: {fp.shape[1]}비트 → 물질 {D.shape[0]}종 × "
                     f"보유비트 {len(keep)}개(최소빈도 {minc})")
        return out

    # ---------------------------------------------------------------- L2 투영
    def layer(self, jn, ep):
        """관할 투영 + 마스크. 반환 (y, keep, 제외이유dict).

        정채윤 조사의 눈 '2' 는 2A/2B 세분이 없다. EU_CLP 는 2A=분류/2B=비분류로
        갈리므로 이 값은 EU 관할에서 판정 불가다 — 임의로 한쪽에 넣지 않고
        해당 물질을 그 레이어에서 제외한다(값을 만들지 않는다는 규칙).
        """
        tab = JUR[jn][ep]
        v = self.LAB[ep]
        b = np.full(self.n_sub, -1, dtype=int)
        for i, val in enumerate(v.to_numpy()):
            if val is None or (isinstance(val, float) and math.isnan(val)) \
                    or str(val) in ("nan", "None", "", "<NA>"):
                continue
            s = str(val)
            assert tab.get(s) is not None, f"{jn}/{ep} 투영표에 '{s}' 없음"
            b[i] = tab[s]
        keep = b >= 0
        excl = {}
        if ep == "eye" and tab.get("2B") == 0:      # EU_CLP 계열
            # BooleanDtype → object 로 새는 것을 막는다(keep 이 object 가 되면 인덱싱 실패).
            amb = (v.astype("string").eq("2").fillna(False).to_numpy(dtype=bool)
                   & self.LABSRC[ep].astype("string").eq("정채윤")
                     .fillna(False).to_numpy(dtype=bool))
            excl["EU눈_2A2B미세분_제외"] = int((amb & keep).sum())
            keep = keep & ~amb
        return b, keep, excl

    # ---------------------------------------------------------------- 피처행렬
    def matrices(self):
        Xs = self.AGG[self.NUM].apply(pd.to_numeric, errors="coerce") \
            .to_numpy(dtype=np.float64)
        keepc = np.array([np.nanstd(Xs[:, j]) > 0 and np.isfinite(Xs[:, j]).any()
                          for j in range(Xs.shape[1])])
        Xs = Xs[:, keepc]
        self.log(f"성분 피처 확정: 구조 {Xs.shape[1]}열 "
                 f"(상수·전결측 {int((~keepc).sum())}열 제거)")
        Xfp = np.hstack([Xs, self.FP["maccs"][0], self.FP["morgan"][0]])
        self.log(f"  지문 arm: {Xfp.shape[1]}열")
        out = {}
        for nm, M in (("구조", Xs), ("구조+지문", Xfp)):
            for im in ("nan0", "native"):
                out[(nm, im)] = (np.nan_to_num(M, nan=0.0, posinf=0.0, neginf=0.0)
                                 if im == "nan0" else M)
        return out


# ============================================================== v6 재현 lock
def v6_lock(R, log, unit="제형"):
    """제형 단위 산출은 v6 과 동일해야 한다. 다르면 리팩터에 결함이 있다는 뜻."""
    if not V6_METRICS.exists():
        log("v6 재현 lock: 기준 산출 없음 — 대조 생략")
        return
    O = pd.read_csv(V6_METRICS)
    key = ["endpoint", "관할", "결측처리", "CT"]
    cur = R[R["단위"] == unit] if "단위" in R.columns else R
    m = O.merge(cur, on=key, suffixes=("_old", "_new"), how="inner")
    assert len(m) > 0, "v6 과 대조할 셀이 없다 — 관할·arm 이름이 바뀌었다"
    num = [c for c in O.columns if c not in key
           and pd.api.types.is_numeric_dtype(O[c])]
    bad = []
    for c in num:
        if f"{c}_old" in m and f"{c}_new" in m:
            d = (m[f"{c}_old"] - m[f"{c}_new"]).abs().max()
            if pd.notna(d) and d > 1e-9:
                bad.append(f"{c}(최대차 {d:.3e})")
    assert not bad, "v7 분리가 제형 수치를 바꿨다 — 결함: " + ", ".join(bad)
    log(f"v6 재현 lock 통과: {len(m)}셀 × 지표 전부 동일 (허용오차 1e-9)")


def write_jur_doc(out_dir: Path, unit: str, R, extra=()):
    """규제 처리 명시 문서. 어떤 규제의 어느 조항으로 라벨을 이진화했는지,
    그리고 그 과정에서 어떤 행을 왜 뺐는지를 산출물 옆에 항상 남긴다."""
    canon = R[R["대표"] == 1] if "대표" in R.columns else R
    lines = [f"# 규제 처리 명시 — {unit} 단위 모델", "",
             "GHS 구분(범주형)을 이진 라벨로 바꾸는 규칙은 규제 관할마다 다르다. "
             "이 문서는 그 변환을 관할별로 전부 적어 둔 것이다. 모델이 예측하는 것은 "
             "'독성의 세기'가 아니라 **해당 관할에서 분류 대상인지 여부**다.", "",
             "## 관할별 근거", "", "| 관할 | 근거 법령·문서 |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in JUR_DOC.items()]
    lines += ["", f"## 대표(전관할) 기준 — `{CANON_JUR}`", "",
              "관할 4개의 차이는 스위치 2개뿐이다.", "",
              "| 스위치 | 분류로 보는 관할 | 분류로 보지 않는 관할 |", "|---|---|---|",
              "| 눈 구분 2B | UN_GHS · K_REACH · US_OSHA | EU_CLP |",
              "| 피부 구분 3 | UN_GHS | EU_CLP · K_REACH · US_OSHA |", "",
              f"두 스위치를 동시에 만족하는 관할이 `K_REACH` 와 `US_OSHA` 이고 두 투영표는 "
              f"완전히 동일하다. 그래서 대표 기준을 `{CANON_JUR}` 로 고정했다 — 국내·미국 "
              "두 관할에 투영 손실이 0 이고, 눈은 UN GHS 원문과도 일치한다. "
              "EU_CLP 기준과 UN_GHS 기준 결과도 같은 파일에 부가 행으로 남긴다.", "",
              "## 이진화 투영표 (구분 → 라벨)", "",
              "| 엔드포인트 | 관할 | 1(분류) | 0(비분류) |", "|---|---|---|---|"]
    for ep in ("eye", "skin"):
        for jn, tabs in JUR.items():
            tab = tabs[ep]
            pos = " · ".join(k for k, v in tab.items() if v == 1)
            neg = " · ".join(k for k, v in tab.items() if v == 0)
            lines.append(f"| {ep} | `{jn}` | {pos} | {neg} |")
    lines += ["", "## 제외 규칙 (값을 만들지 않기 위해 행을 뺀 곳)", ""]
    lines += [f"- {t}" for t in extra] or ["- (없음)"]
    lines += ["", "## 대표 4모델 중 이 단위의 셀", "",
              "| endpoint | 관할 | 피처 | 결측처리 | CT | n | 유병률 | ROC-AUC | MCC |",
              "|---|---|---|---|---|---|---|---|---|"]
    for _, r in canon.iterrows():
        lines.append(f"| {r['endpoint']} | {r['관할']} | {r['피처']} | {r['결측처리']} | "
                     f"{r['CT']} | {r['n']} | {r['유병률']} | {r['roc_auc']} | {r['MCC']} |")
    lines += ["", "임계값은 0.5 고정이다(`THRESHOLD_NOTE` 참조). ROC-AUC 와 PR-AUC 만 "
              "임계값 불변이며, 유병률이 다른 관할끼리는 F1 로 비교하지 않는다.", ""]
    (out_dir / "규제처리_명시.md").write_text("\n".join(lines), encoding="utf-8")


THRESHOLD_NOTE = [
    "임계값 0.5 는 sklearn predict() 기본값이며 이 데이터의 최적값이 아니다. "
    "BA 가 ROC-AUC 보다 뚜렷이 낮고 OOF 후향 최적값은 0.5 미만이다.",
    "그럼에도 0.5 를 유지하는 이유: 임계값은 과소분류(미분류 독성물질 출하) 대 "
    "과대분류의 비용비를 총책임자가 정한 뒤 **학습 폴드 내부에서만** 결정해야 한다. "
    "평가에 쓴 OOF 에서 고른 임계값을 성능으로 보고하면 낙관 편향이 들어간다.",
    "따라서 F1·정밀도·재현율·특이도·MCC·정확도·혼동행렬은 모두 '임계값 0.5 조건부' "
    "값이다. 임계값 불변 지표는 ROC-AUC 와 PR-AUC 뿐이다.",
    "F1 은 진음성을 무시하므로 유병률이 다른 관할 간 비교에 쓸 수 없다. "
    "관할·엔드포인트 비교에는 ROC-AUC 와 MCC 를 쓴다.",
]
