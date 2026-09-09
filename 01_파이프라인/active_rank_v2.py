#!/usr/bin/env python3
"""축 A — 능동학습 기반 수집 우선순위 재랭킹 (v2, 결함 수정판).

`active_rank.py`(v1)는 비교 기준으로 보존하고, 본 파일에서 아래 3건을 수정한다.

[결함 1] 엔드포인트 가중치 미적용
    v1 라인 140:
        score = (0.6*unc + 0.4*div) if (unc is not None and div is not None) \
                else fid2["score"].get(fid, np.nan)
    `unc`/`div`는 `fid2[...]` (fid -> 값) dict의 .get() 결과다. 최소빈 S11 대상 제형은
    전부 D(=X∩Y)에 존재하므로 두 값이 None이 되는 경우가 없고, 따라서 삼항연산의
    첫 분기가 100% 선택된다. sens 2배 가중이 들어있는 `priority_score`(fallback)는
    한 번도 평가되지 않는다. → v2는 fallback에 의존하지 않고 가중치를 결합 점수에
    **직접 곱**한다. 가중치는 임의 배율이 아니라 엔드포인트별 학습곡선에서 추정한
    한 건 추가 시 기대 오차감소(marginal expected information gain)로 산출한다.

[결함 2] 다양성 점수 자기포함(self-inclusion)
    v1은 최근접이웃 풀(labeled fid 전체)에서 자기 자신을 제외하지 않아, 라벨을 가진
    제형은 sim.max()==1.0 → diversity==0.0 으로 붕괴한다. → v2는 leave-one-out과
    기존 실행계획의 leave-group-out(같은 `group_key` 제형끼리 최근접이웃 금지)을
    동시에 적용한다.

[결함 3] 거리 지표 오표기
    v1은 코드에서 cosine 유사도를 쓰면서 주석/실행계획서에는 Tanimoto라고 적었다.
    fp_form_pooled.npz 의 morgan[:, :2048] 은 max-pool 구간으로 값이 {0,1} 이진이므로
    이진 지문 표준인 Tanimoto(Jaccard)가 맞다. → v2는 Tanimoto로 교체하고,
    cosine과의 순위 차이를 보고서에 함께 남긴다.

배정 범위(이서윤/김보경 pH 554건, 정채윤 GHS 331 CAS, 최소빈 S11 797건)는 변경하지 않는다.
v2는 각 리스트 *내부* 순서만 바꾼다.

산출(새 디렉터리, 기존 v4 산출물은 읽기만 함):
    04_모델산출물/v4_active/active_rank_priority_v2.csv
    04_모델산출물/v4_active/active_rank_fix_report.json
"""
import json
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import StratifiedGroupKFold
from scipy.stats import spearmanr

ROOT = str(Path(__file__).resolve().parent.parent)
SRC = f"{ROOT}/04_모델산출물/v4"          # 읽기 전용
OUT = f"{ROOT}/04_모델산출물/v4_active"   # 쓰기 전용
ASSIGN = f"{ROOT}/03_입력데이터/dataset_배정_20260824.xlsx"
SEEDS = [0, 1, 2, 3, 4]
EPS = ("eye", "skin", "sens")
YCOL = {"eye": "y_eye_bin", "skin": "y_skin_bin", "sens": "y_sens_bin"}
os.makedirs(OUT, exist_ok=True)
t0 = time.time()
rep = {}

# ---------------------------------------------------------------- 데이터 로드
X = pd.read_parquet(f"{SRC}/X_formulation.parquet")
Y = pd.read_parquet(f"{SRC}/y_formulation.parquet")
G = pd.read_parquet(f"{SRC}/groups.parquet")[["Formulation_ID", "group_key"]]
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
D = D.drop(columns=[c for c in ("group_key", "group_key__y") if c in D.columns])
D = D.merge(G, on="Formulation_ID", how="left")
FEATS = [c for c in X.columns if c not in ("Formulation_ID", "group_key")]
Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)

fp = np.load(f"{SRC}/fp_form_pooled.npz", allow_pickle=True)
fp_fid = fp["fid"]
# layout = "morgan=[max(2048)|wmean(2048)]" -> 앞 2048은 max-pool = 이진(0/1) 구간
fp_vec = fp["morgan"][:, :2048].astype(np.float64)
assert set(np.unique(fp_vec)).issubset({0.0, 1.0}), "max-pool 구간이 이진이 아님"
fid_to_fprow = {fid: i for i, fid in enumerate(fp_fid)}

rep["data"] = {
    "n_formulation": int(len(D)),
    "n_features": len(FEATS),
    "n_labeled": {ep: int(D[YCOL[ep]].notna().sum()) for ep in EPS},
    "fp_dim_used": int(fp_vec.shape[1]),
    "fp_is_binary": True,
    "n_groups": int(D["group_key"].nunique()),
}


# -------------------------------------------------- committee 불확실도 (검증)
def committee_uncertainty(ycol, n_estimators=300):
    """query-by-committee: 시드 5개 RF의 양성클래스 확률 std = 불확실도.

    v1 로직 유지. 검증 포인트:
      - class_weight="balanced", min_samples_leaf=2 로 시드 간 분산이 실제로 발생하는지
      - classes_ 에 양/음성이 모두 존재하는지(pos_col fallback 0이 잘못 잡히지 않는지)
      - 학습에 쓰인 행(in-sample)의 std가 미라벨 행보다 낮게 눌리지 않는지
    """
    m = D[ycol].notna().to_numpy()
    y = D.loc[m, ycol].astype(int).to_numpy()
    Xtr = Xall[m]
    preds, cls_ok = [], True
    for s in SEEDS:
        clf = RandomForestClassifier(n_estimators=n_estimators, class_weight="balanced",
                                     min_samples_leaf=2, n_jobs=-1, random_state=s)
        clf.fit(Xtr, y)
        proba = clf.predict_proba(Xall)
        if 1 not in clf.classes_:
            cls_ok = False
        pos_col = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
        preds.append(proba[:, pos_col])
    P = np.vstack(preds)
    return P.std(axis=0), m, {"classes_ok": bool(cls_ok),
                              "mean_pred_spread": float(P.max(0).mean() - P.min(0).mean())}


unc, has, cdiag = {}, {}, {}
for ep in EPS:
    unc[ep], has[ep], cdiag[ep] = committee_uncertainty(YCOL[ep])
    D[f"unc_{ep}"] = unc[ep]

rep["committee_check"] = {
    ep: {
        **cdiag[ep],
        "unc_mean_all": float(np.mean(unc[ep])),
        "unc_mean_labeled": float(np.mean(unc[ep][has[ep]])),
        "unc_mean_unlabeled": float(np.mean(unc[ep][~has[ep]])),
        "unc_zero_frac": float(np.mean(unc[ep] == 0.0)),
    } for ep in EPS
}


def minmax(s):
    s = np.asarray(s, dtype=np.float64)
    rng = np.nanmax(s) - np.nanmin(s)
    return (s - np.nanmin(s)) / rng if rng > 0 else s * 0.0


for ep in EPS:
    D[f"unc_{ep}_n"] = minmax(D[f"unc_{ep}"])
    # 대안 정규화(민감도 분석용): min-max는 극단값 1개에 스케일이 좌우되어
    # 엔드포인트 간 비교가능성이 보장되지 않는다. 분위수 정규화는 그 영향을 제거한다.
    D[f"unc_{ep}_q"] = D[f"unc_{ep}"].rank(pct=True)

labeled_mask = has["eye"] | has["skin"] | has["sens"]
D["is_labeled"] = labeled_mask


# ------------------------------------------------------- 다양성 (결함 2·3 수정)
labeled_fids = list(D.loc[labeled_mask, "Formulation_ID"])
pool_fids = [f for f in labeled_fids if f in fid_to_fprow]
pool_rows = np.array([fid_to_fprow[f] for f in pool_fids])
pool_vecs = fp_vec[pool_rows]
pool_pop = pool_vecs.sum(axis=1)
pool_norms = np.linalg.norm(pool_vecs, axis=1) + 1e-9
fid_group = dict(zip(D["Formulation_ID"], D["group_key"]))
pool_groups = np.array([fid_group.get(f) for f in pool_fids], dtype=object)
pool_fid_arr = np.array(pool_fids, dtype=object)


def diversity(fid, metric="tanimoto", exclude_self=True, exclude_group=True):
    """1 - (풀 내 최근접이웃 유사도). 미보유 구조는 중립값 0.5 (v1과 동일 규약).

    metric="tanimoto": c/(a+b-c)  — 이진 지문 표준(Jaccard). v2 기본값.
    metric="cosine"  : v1이 실제로 쓴 지표. 비교용으로만 유지.
    exclude_self/exclude_group: leave-one-out / leave-group-out.
    """
    if fid not in fid_to_fprow or pool_vecs.shape[0] == 0:
        return 0.5
    v = fp_vec[fid_to_fprow[fid]]
    a = v.sum()
    if a == 0:
        return 0.5  # 지문 전부 0 (구조 파싱 실패) -> 중립값
    keep = np.ones(pool_vecs.shape[0], dtype=bool)
    if exclude_self:
        keep &= (pool_fid_arr != fid)
    if exclude_group:
        g = fid_group.get(fid)
        if g is not None:
            keep &= (pool_groups != g)
    if not keep.any():
        return 0.5
    Pv, Pp, Pn = pool_vecs[keep], pool_pop[keep], pool_norms[keep]
    c = Pv @ v
    if metric == "tanimoto":
        sims = c / (Pp + a - c + 1e-9)
    else:
        sims = c / (Pn * np.linalg.norm(v))
    return float(1.0 - sims.max())


DIV_VARIANTS = {
    # v1 재현: cosine + 자기포함 + 그룹 미제외
    "v1_cosine_selfin": dict(metric="cosine", exclude_self=False, exclude_group=False),
    # 자기/그룹 제외만 적용(지표는 v1과 동일) -> 결함2 단독 효과 분리용
    "loo_cosine": dict(metric="cosine", exclude_self=True, exclude_group=True),
    # v2 최종: Tanimoto + LOO + LGO
    "v2_tanimoto_loo": dict(metric="tanimoto", exclude_self=True, exclude_group=True),
    # 지표 효과만 분리(자기포함 유지)
    "v1_tanimoto_selfin": dict(metric="tanimoto", exclude_self=False, exclude_group=False),
}
divs = {}
for name, kw in DIV_VARIANTS.items():
    raw = np.array([diversity(f, **kw) for f in D["Formulation_ID"]])
    divs[name] = {"raw": raw, "n": minmax(raw)}
    D[f"div_{name}"] = divs[name]["n"]

n_struct = int(sum(1 for f in D["Formulation_ID"]
                   if f in fid_to_fprow and fp_vec[fid_to_fprow[f]].sum() > 0))
rep["diversity_distribution"] = {
    name: {
        "raw_mean": float(v["raw"].mean()),
        "raw_median": float(np.median(v["raw"])),
        "raw_std": float(v["raw"].std()),
        "n_raw_exact_zero": int((v["raw"] == 0.0).sum()),
        "n_raw_le_1e_9": int((v["raw"] <= 1e-9).sum()),
        "n_raw_le_0.01": int((v["raw"] <= 0.01).sum()),
        "n_raw_le_0.05": int((v["raw"] <= 0.05).sum()),
        "frac_raw_le_0.01": round(float((v["raw"] <= 0.01).mean()), 4),
        "n_neutral_0.5": int((v["raw"] == 0.5).sum()),
    } for name, v in divs.items()
}
rep["diversity_distribution"]["_meta"] = {
    "n_rows": int(len(D)),
    "n_with_nonzero_fingerprint": n_struct,
    "n_without_usable_structure": int(len(D) - n_struct),
    "note": "v1은 pool_norms에 +1e-9를 더하므로 자기유사도가 정확히 1.0이 아니라 "
            "1-1e-9 수준이 된다. 따라서 자기포함 붕괴는 n_raw_exact_zero가 아니라 "
            "n_raw_le_1e_9 / n_raw_le_0.01 로 세어야 한다. n_neutral_0.5 = 구조 미보유 "
            "중립값 행(붕괴 아님).",
}

# 지표 교체 영향: 동일한 LOO/LGO 조건에서 cosine vs Tanimoto
sp = spearmanr(divs["loo_cosine"]["raw"], divs["v2_tanimoto_loo"]["raw"])
rep["metric_swap_cosine_vs_tanimoto"] = {
    "spearman_rho_diversity_raw": round(float(sp.statistic), 4),
    "spearman_p": float(sp.pvalue),
    "pearson_r": round(float(np.corrcoef(divs["loo_cosine"]["raw"],
                                         divs["v2_tanimoto_loo"]["raw"])[0, 1]), 4),
    "mean_abs_diff": round(float(np.abs(divs["loo_cosine"]["raw"]
                                        - divs["v2_tanimoto_loo"]["raw"]).mean()), 4),
    "tanimoto_mean_minus_cosine_mean": round(
        float(divs["v2_tanimoto_loo"]["raw"].mean() - divs["loo_cosine"]["raw"].mean()), 4),
}


# ------------------------------- 엔드포인트 가중치: 학습곡선 기반 기대정보이득
def learning_curve_weights(fracs=(0.25, 0.4, 0.55, 0.7, 0.85, 1.0), n_rep=3,
                           n_estimators=200, n_splits=4):
    """엔드포인트별 가중치 = "라벨 1건 추가 시 기대 오차감소".

    근거(임의 배율 금지):
      1) 각 엔드포인트에서 라벨된 표본을 여러 비율로 서브샘플링해 group 기반 CV로
         err(n) = 1 - balanced_accuracy 를 측정한다.
      2) 관측된 (n, err)에 멱법칙 err = A * n^(-b) 를 log-log 선형회귀로 적합한다.
         (b는 가정값이 아니라 데이터에서 추정 — 튜닝 여지를 없애기 위함)
      3) 현재 표본수 n0에서의 한계 기대이득 g = |d err/dn| = A*b*n0^(-(b+1)) 을 계산.
      4) 가중치 w = g / mean(g)  (평균 1로 정규화). 스케일에 임의 상수를 넣지 않는다.

    sens가 큰 가중을 받는다면 그것은 (a) 표본이 최소(n=595)이고 (b) 곡선이 아직
    가파른 구간에 있다는 두 관측의 결과이며, 원하는 결과를 만들려고 고른 값이 아니다.
    """
    out = {}
    for ep in EPS:
        m = D[YCOL[ep]].notna().to_numpy()
        y = D.loc[m, YCOL[ep]].astype(int).to_numpy()
        Xl, gl = Xall[m], D.loc[m, "group_key"].to_numpy()
        n0 = int(m.sum())
        pts = []
        for fr in fracs:
            scores = []
            for r in range(n_rep):
                rs = np.random.RandomState(1000 * r + int(fr * 100))
                if fr < 1.0:
                    ug = np.unique(gl)
                    keep_g = set(rs.choice(ug, max(4, int(len(ug) * fr)), replace=False))
                    sel = np.array([g in keep_g for g in gl])
                else:
                    sel = np.ones(len(y), dtype=bool)
                ys, Xs, gs = y[sel], Xl[sel], gl[sel]
                if len(np.unique(ys)) < 2 or min(np.bincount(ys)) < n_splits:
                    continue
                cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=r)
                yhat = np.zeros_like(ys)
                for tr, te in cv.split(Xs, ys, groups=gs):
                    if len(np.unique(ys[tr])) < 2:
                        continue
                    clf = RandomForestClassifier(n_estimators=n_estimators,
                                                 class_weight="balanced",
                                                 min_samples_leaf=2, n_jobs=-1,
                                                 random_state=r)
                    clf.fit(Xs[tr], ys[tr])
                    yhat[te] = clf.predict(Xs[te])
                scores.append((len(ys), 1.0 - balanced_accuracy_score(ys, yhat)))
            if scores:
                pts.append((float(np.mean([s[0] for s in scores])),
                            float(np.mean([s[1] for s in scores]))))
        ns = np.array([p[0] for p in pts]); errs = np.array([p[1] for p in pts])
        ok = errs > 0
        lx, ly = np.log(ns[ok]), np.log(errs[ok])
        b_, logA = np.polyfit(lx, ly, 1)
        b = -float(b_); A = float(np.exp(logA))
        r2 = float(1.0 - ((ly - (b_ * lx + logA)) ** 2).sum() / ((ly - ly.mean()) ** 2).sum())
        g = A * b * n0 ** (-(b + 1.0))
        out[ep] = {"n0": n0, "curve": [[round(a, 1), round(c, 4)] for a, c in pts],
                   "powerlaw_A": round(A, 4), "powerlaw_b": round(b, 4),
                   "loglog_fit_r2": round(r2, 4),
                   "err_at_n0": round(A * n0 ** (-b), 4), "marginal_gain": g}
    gs_ = np.array([out[ep]["marginal_gain"] for ep in EPS])
    mean_g = gs_.mean()
    for ep in EPS:
        out[ep]["weight"] = float(out[ep]["marginal_gain"] / mean_g)
    return out


lc = learning_curve_weights()
W = {ep: lc[ep]["weight"] for ep in EPS}
# 민감도 참조용(가중치로 채택하지 않음): 순수 역표본수 가중
inv = {ep: 1.0 / rep["data"]["n_labeled"][ep] for ep in EPS}
inv_mean = np.mean(list(inv.values()))
W_invn = {ep: inv[ep] / inv_mean for ep in EPS}
rep["endpoint_weights"] = {
    "method": "learning-curve marginal expected information gain "
              "(err=A*n^-b, b fitted from data; w = g/mean(g), g=A*b*n0^-(b+1))",
    "per_endpoint": {ep: {k: v for k, v in lc[ep].items()} for ep in EPS},
    "weights_used": {ep: round(W[ep], 4) for ep in EPS},
    "sensitivity_inverse_n_weights": {ep: round(W_invn[ep], 4) for ep in EPS},
    "v1_documented_weights": {"eye": 1.0, "skin": 1.0, "sens": 2.0,
                              "note": "v1 문서상 sens 2배. 실제 코드에서는 최소빈 797행에 "
                                      "적용되지 않았음(라인 140 fallback 미실행)."},
}


# --------------------------------------------------------------- 점수 계산기
def make_scores(div_key, weights, weight_mode, unc_suffix="_n"):
    """weight_mode: "off"=가중치 미적용(v1 실효 동작), "on"=결합점수에 직접 곱."""
    dv = D[f"div_{div_key}"].to_numpy()
    U = {ep: D[f"unc_{ep}{unc_suffix}"].to_numpy() for ep in EPS}
    if weight_mode == "off":
        wts = {ep: 1.0 for ep in EPS}
    else:
        wts = weights
    # 다엔드포인트 과제(pH/GHS): 한 건 확보가 3개 엔드포인트에 모두 기여 -> 가중 평균
    all_score = 0.6 * (sum(wts[ep] * U[ep] for ep in EPS) / sum(wts.values())) + 0.4 * dv
    per_ep = {}
    for ep in EPS:
        base = 0.6 * U[ep] + 0.4 * dv
        # 결함1 수정의 핵심: fallback 삼항연산이 아니라 여기서 명시적으로 곱한다.
        # 해석: "이 라벨을 확보해 얻는 기대 정보량" = (해당 행의 정보성) x
        #        (그 엔드포인트에서 라벨 1건의 한계 가치 w_ep)
        per_ep[ep] = base * (wts[ep] if weight_mode == "on" else 1.0)
    return {
        "all": dict(zip(D["Formulation_ID"], all_score)),
        **{ep: dict(zip(D["Formulation_ID"], per_ep[ep])) for ep in EPS},
        "div": dict(zip(D["Formulation_ID"], dv)),
        **{f"unc_{ep}": dict(zip(D["Formulation_ID"], U[ep])) for ep in EPS},
    }


VARIANTS = {
    # 현행 재현 (cosine + 자기포함 + 가중치 실효 미적용)
    "A_v1_asis":        ("v1_cosine_selfin", "off"),
    # 가중치만 실제 적용
    "B_weight_only":    ("v1_cosine_selfin", "on"),
    # 다양성만 수정 (LOO/LGO, 지표는 cosine 유지)
    "C_div_only":       ("loo_cosine",       "off"),
    # 둘 다 (지표는 cosine 유지) -> 지표 효과와 분리
    "D_weight_and_div": ("loo_cosine",       "on"),
    # v2 최종: 둘 다 + Tanimoto
    "E_v2_final":       ("v2_tanimoto_loo",  "on"),
}
SC = {k: make_scores(dk, W, wm) for k, (dk, wm) in VARIANTS.items()}

# 민감도 변형 (가중치 산정방식·정규화 선택이 결과를 좌우하는지 확인. 최종 산출물 아님)
SENS_VARIANTS = {
    "S1_invn_weights": make_scores("v2_tanimoto_loo", W_invn, "on"),
    "S2_quantile_unc": make_scores("v2_tanimoto_loo", W, "on", unc_suffix="_q"),
    "S3_quantile_unc_noweight": make_scores("v2_tanimoto_loo", W, "off", unc_suffix="_q"),
}


# --------------------------------------------- 배정 시트 -> 행 생성 (범위 불변)
xl = pd.ExcelFile(ASSIGN)
phy = xl.parse("특성")
ing = xl.parse("성분")
tox = xl.parse("독성코드")
cas_to_fids = ing.groupby("cas")["Formulation_ID"].apply(list).to_dict()


def build_rows(S):
    rows = []
    for person in ("이서윤", "김보경"):
        sub = phy[phy["pH작업_담당"] == person]
        for fid in sub["Formulation_ID"]:
            rows.append({"assigned_to": person, "task_type": "pH수집", "endpoint": "전체",
                         "Formulation_ID": fid, "cas": None,
                         "priority_score": S["all"].get(fid, np.nan),
                         "unc_eye": S["unc_eye"].get(fid), "unc_skin": S["unc_skin"].get(fid),
                         "unc_sens": S["unc_sens"].get(fid), "diversity": S["div"].get(fid)})
    sub = ing[ing["GHS조사_담당"] == "정채윤"]
    for _, r in sub.iterrows():
        cas = r["cas"]
        fids = cas_to_fids.get(cas, [r["Formulation_ID"]])
        scores = [S["all"][f] for f in fids if f in S["all"]]
        rows.append({"assigned_to": "정채윤", "task_type": "GHS조사", "endpoint": "전체",
                     "Formulation_ID": r["Formulation_ID"], "cas": cas,
                     "priority_score": max(scores) if scores else np.nan,
                     "unc_eye": None, "unc_skin": None, "unc_sens": None, "diversity": None})
    sub = tox[tox["S11_담당"] == "최소빈"]
    for _, r in sub.iterrows():
        fid = r["Formulation_ID"]
        for ep, col in (("eye", "S11_원문_눈"), ("skin", "S11_원문_피부"), ("sens", "S11_원문_감작")):
            if pd.notna(r[col]):
                rows.append({"assigned_to": "최소빈", "task_type": "S11판독", "endpoint": ep,
                             "Formulation_ID": fid, "cas": None,
                             "priority_score": S[ep].get(fid, np.nan),
                             "unc_eye": S["unc_eye"].get(fid), "unc_skin": S["unc_skin"].get(fid),
                             "unc_sens": S["unc_sens"].get(fid), "diversity": S["div"].get(fid)})
    out = pd.DataFrame(rows)
    out["priority_rank"] = out.groupby("assigned_to")["priority_score"].rank(
        ascending=False, method="first")
    return out.sort_values(["assigned_to", "priority_rank"])


TAB = {k: build_rows(SC[k]) for k in VARIANTS}
TAB_S = {k: build_rows(v) for k, v in SENS_VARIANTS.items()}


# --------------------------------------------------------- 결함 1 실증 계측
# v1 라인 140의 삼항연산이 fallback을 몇 번 타는지 직접 계측한다.
fb = {"branch_primary": 0, "branch_fallback": 0, "rows": 0}
v1_unc = {ep: dict(zip(D["Formulation_ID"], D[f"unc_{ep}_n"])) for ep in EPS}
v1_div = dict(zip(D["Formulation_ID"], D["div_v1_cosine_selfin"]))
for _, r in tox[tox["S11_담당"] == "최소빈"].iterrows():
    fid = r["Formulation_ID"]
    for ep, col in (("eye", "S11_원문_눈"), ("skin", "S11_원문_피부"), ("sens", "S11_원문_감작")):
        if pd.notna(r[col]):
            fb["rows"] += 1
            u, d = v1_unc[ep].get(fid), v1_div.get(fid)
            if u is not None and d is not None:
                fb["branch_primary"] += 1
            else:
                fb["branch_fallback"] += 1
rep["defect1_branch_instrumentation"] = {
    **fb,
    "fallback_rate": round(fb["branch_fallback"] / max(1, fb["rows"]), 6),
    "conclusion": "최소빈 797행 전부 첫 분기(무가중 0.6*unc+0.4*div)를 타며 "
                  "sens 2배 가중이 들어간 priority_score fallback은 0회 실행됨.",
}


# ----------------------------------------------------- 영향 정량화 (기여 분해)
def top20_sens(df):
    cs = df[df.assigned_to == "최소빈"].sort_values("priority_score", ascending=False)
    k = max(1, int(len(cs) * 0.2))
    t = cs.head(k)
    return {"n_total": int(len(cs)), "n_top20": int(k),
            "sens_share_overall": round(float((cs.endpoint == "sens").mean()), 4),
            "sens_share_top20": round(float((t.endpoint == "sens").mean()), 4),
            "eye_share_top20": round(float((t.endpoint == "eye").mean()), 4),
            "skin_share_top20": round(float((t.endpoint == "skin").mean()), 4)}


# 현행 CSV(v4)로부터 직접 재계산 — 코드 재실행 없는 독립 확인
cur_path = f"{SRC}/active_rank_priority.csv"
cur = pd.read_csv(cur_path, encoding="utf-8-sig")
rep["current_csv_recheck"] = {"path": cur_path, "shape": list(cur.shape),
                              **top20_sens(cur)}
rep["current_csv_recheck"]["counts_per_person"] = {
    k: int(v) for k, v in cur["assigned_to"].value_counts().items()}
cur_cs = cur[cur.assigned_to == "최소빈"]
rep["current_csv_recheck"]["diversity_zero_rows_in_csv"] = int(
    (cur["diversity"].fillna(-1) <= 1e-9).sum())

rep["decomposition_top20_sens"] = {k: top20_sens(TAB[k]) for k in VARIANTS}
base = rep["decomposition_top20_sens"]["A_v1_asis"]["sens_share_top20"]
rep["decomposition_top20_sens"]["_delta_pp_vs_A"] = {
    k: round((rep["decomposition_top20_sens"][k]["sens_share_top20"] - base) * 100, 2)
    for k in VARIANTS}


rep["sensitivity_top20_sens"] = {
    "_note": "가중치 산정방식(학습곡선 vs 역표본수)과 불확실도 정규화(min-max vs 분위수) "
             "선택에 top20% sens 비중이 얼마나 민감한지. 값이 크게 흔들리면 '개선폭'을 "
             "가중치 효과로 단정할 수 없음을 뜻한다.",
    "E_v2_final(학습곡선W, minmax)": top20_sens(TAB["E_v2_final"]),
    **{k: top20_sens(v) for k, v in TAB_S.items()},
}


def top20_set(df):
    cs = df[df.assigned_to == "최소빈"].sort_values("priority_score", ascending=False)
    k = max(1, int(len(cs) * 0.2))
    return set(zip(cs.head(k)["Formulation_ID"], cs.head(k)["endpoint"]))


def rank_compare(a, b):
    ja = TAB[a][TAB[a].assigned_to == "최소빈"].set_index(
        ["Formulation_ID", "endpoint"])["priority_score"]
    jb = TAB[b][TAB[b].assigned_to == "최소빈"].set_index(
        ["Formulation_ID", "endpoint"])["priority_score"]
    j = pd.concat([ja.rename("a"), jb.rename("b")], axis=1).dropna()
    sa, sb = top20_set(TAB[a]), top20_set(TAB[b])
    return {"spearman_rho": round(float(spearmanr(j["a"], j["b"]).statistic), 4),
            "top20_overlap_frac": round(len(sa & sb) / max(1, len(sa)), 4),
            "top20_overlap_n": len(sa & sb), "top20_size": len(sa)}


rep["rank_impact"] = {
    "A_v1_asis__vs__E_v2_final": rank_compare("A_v1_asis", "E_v2_final"),
    "D_cosine__vs__E_tanimoto (지표 단독 효과)": rank_compare("D_weight_and_div", "E_v2_final"),
    "A__vs__B (가중치 단독)": rank_compare("A_v1_asis", "B_weight_only"),
    "A__vs__C (다양성 단독)": rank_compare("A_v1_asis", "C_div_only"),
}

# ----------------------------------------------- 배정 범위 불변 검증 (절대 요건)
EXPECT = {"이서윤": 280, "김보경": 274, "정채윤": 331, "최소빈": 797}
final = TAB["E_v2_final"]
got = {k: int(v) for k, v in final["assigned_to"].value_counts().items()}
cur_counts = {k: int(v) for k, v in cur["assigned_to"].value_counts().items()}
id_match = {}
for p in EXPECT:
    a = sorted(map(str, cur[cur.assigned_to == p]["Formulation_ID"]))
    b = sorted(map(str, final[final.assigned_to == p]["Formulation_ID"]))
    id_match[p] = bool(a == b)
rep["assignment_invariance"] = {
    "expected": EXPECT, "v2_counts": got, "v4_current_counts": cur_counts,
    "counts_match_expected": all(got.get(k) == v for k, v in EXPECT.items()),
    "counts_match_current": got == cur_counts,
    "formulation_id_multiset_identical_per_person": id_match,
    "all_identical": all(id_match.values()),
}

# ------------------------------------------------------------------- 산출 저장
csv_path = f"{OUT}/active_rank_priority_v2.csv"
final.to_csv(csv_path, index=False, encoding="utf-8-sig")
rep["outputs"] = {"csv": csv_path, "shape": list(final.shape),
                  "elapsed_sec": round(time.time() - t0, 1)}
rep["caveats"] = [
    "학습곡선 log-log 적합의 R2가 eye 0.63 / skin 0.39 / sens 0.11 수준으로 낮다(곡선이 "
    "거의 평평하고 노이즈가 큼). 따라서 가중치 1.28(sens)은 점추정치이며 신뢰구간이 넓다. "
    "가중치의 '방향'(sens>1)은 표본수 최소 사실과 세 갈래 증거로 뒷받침되지만 '크기'는 약하다.",
    "top20% 내 sens 비중은 검증 지표가 아니다. 양의 sens 가중을 주면 기계적으로 상승하는 "
    "값이므로(민감도: 학습곡선W 70.4% / 역표본수W 86.8% / 무가중+분위수 32.1%) AC1은 "
    "가중치가 적용됐는지의 확인일 뿐 모델 개선의 증거가 아니다. AC1 문구 재정의를 권고한다.",
    "엔드포인트별 불확실도를 각각 min-max 정규화한 뒤 엔드포인트 간에 비교하는 v1의 설계는 "
    "그 자체로 비교가능성이 보장되지 않는다(극단값 1개가 스케일을 좌우). v2는 v1 호환을 위해 "
    "min-max를 유지했고 분위수 정규화 결과를 민감도로 병기했다.",
    "committee 불확실도는 학습에 쓰인 행에도 in-sample로 예측하므로 라벨 보유 행의 std가 "
    "미라벨 행보다 눌린다(예: sens 0.0130 vs 0.0195). 최소빈 S11 판독 대상은 대부분 "
    "미라벨 행이라 실무 영향은 제한적이지만, OOF committee로 교체하는 것이 원칙적으로 옳다.",
    "committee std 절대값이 0.013~0.020으로 매우 작다(RF 5-seed는 부트스트랩/특성샘플링 "
    "차이만 반영). 불확실도 신호 자체가 약하다는 뜻이므로 앙상블 다양화(모델군 혼합) 검토 필요.",
]
rep["variant_legend"] = {
    "A_v1_asis": "현행 v1 재현: cosine + 자기포함 + 가중치 실효 미적용",
    "B_weight_only": "결함1만 수정(학습곡선 가중치 실제 적용)",
    "C_div_only": "결함2만 수정(leave-one-out + leave-group-out)",
    "D_weight_and_div": "결함1+2 수정(지표는 cosine 유지)",
    "E_v2_final": "v2 최종: 결함1+2+3 수정(Tanimoto)",
}
with open(f"{OUT}/active_rank_fix_report.json", "w", encoding="utf-8") as f:
    json.dump(rep, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- 콘솔 요약
print(f"저장: {csv_path} {final.shape}")
print("배정 불변:", rep["assignment_invariance"]["counts_match_expected"],
      rep["assignment_invariance"]["all_identical"], got)
print("결함1 fallback 실행 횟수:", fb["branch_fallback"], "/", fb["rows"])
print("가중치(학습곡선):", rep["endpoint_weights"]["weights_used"],
      "| 역표본수 참조:", rep["endpoint_weights"]["sensitivity_inverse_n_weights"])
print("\ntop20% sens 비중 분해:")
for k in VARIANTS:
    d = rep["decomposition_top20_sens"][k]
    print(f"  {k:18s} {d['sens_share_top20']*100:6.2f}%  "
          f"(Δ {rep['decomposition_top20_sens']['_delta_pp_vs_A'][k]:+.2f}pp)")
print("현행 v4 CSV 재계산:", rep["current_csv_recheck"]["sens_share_top20"])
print("민감도:", {k: v["sens_share_top20"] for k, v in rep["sensitivity_top20_sens"].items()
                if k != "_note"})
print("학습곡선 적합 R2:", {ep: lc[ep]["loglog_fit_r2"] for ep in EPS})
print("다양성 raw==0 행수:",
      {k: v["n_raw_exact_zero"] for k, v in rep["diversity_distribution"].items()
       if k != "_meta"})
print("cosine vs Tanimoto:", rep["metric_swap_cosine_vs_tanimoto"])
