#!/usr/bin/env python3
"""label_source(y_*_src) 편향 감사 — 읽기 전용 진단.

critic 지적 검증:
  (1) y_*_src == 'sds_v1' 행의 양성률이 눈/피부/감작성 전부 100% 인가
  (2) X 피처만으로 is_sds_v1 을 복원할 수 있는가 (AUC ~0.948 재현)
  (3) sds_v1 제외 시 baseline(RF300) 성능이 어떻게 변하는가

기존 데이터/파이프라인 파일은 절대 수정하지 않는다. 새 리포트만 쓴다.
출력: 04_모델산출물/v4/label_source_bias_report.json
      04_모델산출물/v4/label_source_bias_crosstab.csv
      04_모델산출물/v4/label_source_sds_v1_recoverable_negatives.csv
"""
import json

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                             f1_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

BASE = "/Users/hanseoyun/Desktop/260830"
V4 = f"{BASE}/04_모델산출물/v4"
SRC_V1 = f"{BASE}/03_입력데이터/input_dataset.xlsx"
RNG = 0
EPS = ("eye", "skin", "sens")

X = pd.read_parquet(f"{V4}/X_formulation.parquet")
Y = pd.read_parquet(f"{V4}/y_formulation.parquet")
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
FEATS = [c for c in X.columns if c != "Formulation_ID"]
Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
GROUPS = D["group_key"].astype(str).to_numpy()
R = {"n_rows": int(len(D)), "n_features": len(FEATS)}


def rf():
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  min_samples_leaf=2, n_jobs=-1, random_state=RNG)


def metrics(y, pred, proba):
    """F1 / ROC-AUC / PR-AUC / balanced_acc. 단일클래스 fold 는 None."""
    out = {"f1": float(f1_score(y, pred, zero_division=0)),
           "balanced_acc": float(balanced_accuracy_score(y, pred))}
    out["roc_auc"] = float(roc_auc_score(y, proba)) if len(np.unique(y)) > 1 else None
    out["pr_auc"] = float(average_precision_score(y, proba)) if len(np.unique(y)) > 1 else None
    return out


def agg(fold_metrics):
    keys = ("f1", "roc_auc", "pr_auc", "balanced_acc")
    out = {}
    for k in keys:
        vals = [m[k] for m in fold_metrics if m[k] is not None]
        out[k] = round(float(np.mean(vals)), 4) if vals else None
        out[k + "_sd"] = round(float(np.std(vals)), 4) if vals else None
    out["n_folds"] = len(fold_metrics)
    return out


def cv_eval(mask, ycol, feats, n_splits=5):
    """group-aware StratifiedGroupKFold 평가. mask 로 행 부분집합 지정."""
    m = mask & D[ycol].notna().to_numpy()
    if m.sum() < 40:
        return {"error": f"n={int(m.sum())} too small"}
    y = D.loc[m, ycol].astype(int).to_numpy()
    if len(np.unique(y)) < 2:
        return {"error": "single class", "n": int(m.sum()), "pos": int(y.sum())}
    g = GROUPS[m]
    Xm = feats[m]
    n_splits = min(n_splits, len(np.unique(g)), int(np.bincount(y).min()))
    if n_splits < 2:
        return {"error": "cv infeasible", "n": int(m.sum()), "pos": int(y.sum())}
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=RNG)
    fm = []
    for tr, te in cv.split(Xm, y, groups=g):
        clf = rf().fit(Xm[tr], y[tr])
        pc = list(clf.classes_).index(1)
        proba = clf.predict_proba(Xm[te])[:, pc]
        fm.append(metrics(y[te], clf.predict(Xm[te]), proba))
    out = agg(fm)
    out.update(n=int(m.sum()), n_pos=int(y.sum()),
               pos_rate=round(float(y.mean()), 4),
               n_groups=int(len(np.unique(g))))
    return out


# ==================================================== 1. 사실 재확인: 교차표
print("[1] label_source x endpoint 교차표")
rows = []
for ep in EPS:
    s = Y[f"y_{ep}_src"].fillna("__NaN(라벨없음)__")
    b = Y[f"y_{ep}_bin"]
    for src, sub in b.groupby(s):
        rows.append({"endpoint": ep, "label_source": src, "n_rows": int(sub.size),
                     "n_labeled": int(sub.notna().sum()),
                     "n_pos": int(sub.fillna(0).sum()),
                     "pos_rate": (round(float(sub.mean()), 4)
                                  if sub.notna().any() else None),
                     "n_cat_unique": int(Y.loc[sub.index, f"y_{ep}"].nunique()),
                     "cats": ";".join(sorted(
                         Y.loc[sub.index, f"y_{ep}"].dropna().astype(str).unique()))})
CT = pd.DataFrame(rows)
CT.to_csv(f"{V4}/label_source_bias_crosstab.csv", index=False)
print(CT.to_string(index=False))
R["crosstab"] = CT.to_dict(orient="records")
R["fact_sds_v1_all_positive"] = {
    ep: bool(CT[(CT.endpoint == ep) & (CT.label_source == "sds_v1")]
             ["pos_rate"].iloc[0] == 1.0) for ep in EPS}

# ==================================================== 2. 근본 원인: v1 원본 대조
print("\n[2] v1 원본(input_dataset.xlsx) 대조 — sds_grade_status_* vs sds_ghs_*")
F1 = pd.read_excel(SRC_V1, sheet_name="formulation")
cause = {}
for ep in EPS:
    st, cat = f"sds_grade_status_{ep}", f"sds_ghs_{ep}"
    xt = pd.crosstab(F1[st].fillna("__NaN__"), F1[cat].notna())
    xt.columns = [f"cat_present={c}" for c in xt.columns]
    cause[ep] = {
        "status_counts": {str(k): int(v) for k, v in
                          F1[st].fillna("__NaN__").value_counts().items()},
        "cat_value_counts": {str(k): int(v) for k, v in
                             F1[cat].fillna("__NaN__").value_counts().items()},
        "status_x_cat_present": xt.to_dict(),
        "n_status_negative": int((F1[st] == "negative").sum()),
        "n_status_negative_with_cat": int(((F1[st] == "negative")
                                          & F1[cat].notna()).sum()),
        "n_status_ok_with_cat": int(((F1[st] == "ok") & F1[cat].notna()).sum()),
        "cat_has_any_NC": bool(F1[cat].astype(str).str.contains(
            r"nc|not classified", case=False, na=False).any()),
    }
    print(f"  {ep:5s} status=negative {cause[ep]['n_status_negative']:4d}건 "
          f"→ 그중 sds_ghs_{ep} 값 보유 {cause[ep]['n_status_negative_with_cat']}건 "
          f"| status=ok & 값보유 {cause[ep]['n_status_ok_with_cat']}건 "
          f"| 카테고리컬럼에 NC 존재? {cause[ep]['cat_has_any_NC']}")
R["root_cause_v1_source"] = cause

# 소실된 음성이 최종 y 에서 어떻게 되었는가
print("\n  소실된 명시적 음성(status=negative)의 최종 라벨 상태")
YF = Y.merge(F1[["Formulation_ID"] + [f"sds_grade_status_{e}" for e in EPS]],
             on="Formulation_ID", how="left")
lost = {}
rec_rows = []
for ep in EPS:
    m = YF[f"sds_grade_status_{ep}"] == "negative"
    sub = YF[m]
    lost[ep] = {
        "n_explicit_negative": int(m.sum()),
        "final_y_missing": int(sub[f"y_{ep}_bin"].isna().sum()),
        "final_y_from_other_src": {str(k): int(v) for k, v in
                                   sub[f"y_{ep}_src"].fillna("__NaN__")
                                   .value_counts().items()},
        "final_y_pos_among_labeled": int(sub[f"y_{ep}_bin"].fillna(0).sum()),
        "recoverable_negatives": int(sub[f"y_{ep}_bin"].isna().sum()),
    }
    print(f"  {ep:5s} 명시적음성 {lost[ep]['n_explicit_negative']:4d} → "
          f"최종 y 결측 {lost[ep]['final_y_missing']:4d} (즉시 복구가능 음성) · "
          f"타출처로 채워짐 {int(m.sum()) - lost[ep]['final_y_missing']:4d} "
          f"(그중 양성 {lost[ep]['final_y_pos_among_labeled']})")
    for fid in sub.loc[sub[f"y_{ep}_bin"].isna(), "Formulation_ID"]:
        rec_rows.append({"Formulation_ID": fid, "endpoint": ep,
                         "reason": "sds_grade_status=negative but y is NaN"})
pd.DataFrame(rec_rows).to_csv(
    f"{V4}/label_source_sds_v1_recoverable_negatives.csv", index=False)
R["lost_negatives"] = lost

# 사실상 sds_v1 의 실제 양성률 (명시적 음성을 되살렸다면)
R["counterfactual_sds_v1_pos_rate"] = {}
for ep in EPS:
    npos = int(CT[(CT.endpoint == ep) & (CT.label_source == "sds_v1")]["n_pos"].iloc[0])
    nneg = lost[ep]["recoverable_negatives"]
    R["counterfactual_sds_v1_pos_rate"][ep] = {
        "n_pos": npos, "n_recoverable_neg": nneg,
        "pos_rate_if_recovered": round(npos / (npos + nneg), 4) if npos + nneg else None}

# ==================================================== 3. 성능 영향 정량화
print("\n[3-a] label_source 원-핫만으로 y 예측 (group-aware CV, ROC-AUC)")
onehot_res = {}
for ep in EPS:
    m = Y[f"y_{ep}_bin"].notna().to_numpy()
    y = Y.loc[m, f"y_{ep}_bin"].astype(int).to_numpy()
    src = pd.get_dummies(Y.loc[m, f"y_{ep}_src"].fillna("__NaN__"), dtype=float)
    g = Y.loc[m, "group_key"].astype(str).to_numpy()
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)
    aucs, prs = [], []
    for tr, te in cv.split(src, y, groups=g):
        clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(
            src.to_numpy()[tr], y[tr])
        p = clf.predict_proba(src.to_numpy()[te])[:, list(clf.classes_).index(1)]
        if len(np.unique(y[te])) > 1:
            aucs.append(roc_auc_score(y[te], p))
            prs.append(average_precision_score(y[te], p))
    onehot_res[ep] = {"roc_auc": round(float(np.mean(aucs)), 4),
                      "pr_auc": round(float(np.mean(prs)), 4),
                      "n": int(m.sum()), "base_rate": round(float(y.mean()), 4)}
    print(f"  {ep:5s} n={m.sum():4d} ROC-AUC={np.mean(aucs):.4f} "
          f"PR-AUC={np.mean(prs):.4f} (base rate {y.mean():.3f})")
R["label_source_onehot_only"] = onehot_res

print("\n[3-b] X 피처만으로 is_sds_v1 복원 (critic 0.948 검증)")
recon = {}
for ep in EPS:
    lab = D[f"y_{ep}_src"].notna().to_numpy()
    tgt = (D[f"y_{ep}_src"] == "sds_v1").astype(int).to_numpy()
    res = {}
    for scope, m in (("labeled_rows_only", lab), ("all_rows", np.ones(len(D), bool))):
        mm = m.copy()
        yy = tgt[mm]
        if len(np.unique(yy)) < 2:
            res[scope] = {"error": "single class"}
            continue
        g = GROUPS[mm]
        cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)
        aucs, prs = [], []
        for tr, te in cv.split(Xall[mm], yy, groups=g):
            clf = rf().fit(Xall[mm][tr], yy[tr])
            p = clf.predict_proba(Xall[mm][te])[:, list(clf.classes_).index(1)]
            if len(np.unique(yy[te])) > 1:
                aucs.append(roc_auc_score(yy[te], p))
                prs.append(average_precision_score(yy[te], p))
        res[scope] = {"roc_auc": round(float(np.mean(aucs)), 4),
                      "pr_auc": round(float(np.mean(prs)), 4),
                      "n": int(mm.sum()), "n_sds_v1": int(yy.sum())}
        print(f"  {ep:5s} {scope:18s} n={mm.sum():4d} pos={yy.sum():4d} "
              f"ROC-AUC={np.mean(aucs):.4f} PR-AUC={np.mean(prs):.4f}")
    recon[ep] = res
# 엔드포인트 무관 "어느 엔드포인트든 sds_v1" 타깃
any_v1 = (D[[f"y_{e}_src" for e in EPS]] == "sds_v1").any(axis=1).astype(int).to_numpy()
cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)
aucs, prs = [], []
for tr, te in cv.split(Xall, any_v1, groups=GROUPS):
    clf = rf().fit(Xall[tr], any_v1[tr])
    p = clf.predict_proba(Xall[te])[:, list(clf.classes_).index(1)]
    aucs.append(roc_auc_score(any_v1[te], p))
    prs.append(average_precision_score(any_v1[te], p))
recon["any_endpoint_is_sds_v1"] = {"roc_auc": round(float(np.mean(aucs)), 4),
                                   "pr_auc": round(float(np.mean(prs)), 4),
                                   "n": int(len(D)), "n_sds_v1": int(any_v1.sum())}
print(f"  ANY   all_rows           n={len(D):4d} pos={any_v1.sum():4d} "
      f"ROC-AUC={np.mean(aucs):.4f} PR-AUC={np.mean(prs):.4f}")
# 어느 피처가 sds_v1 를 지목하는가
clf_full = rf().fit(Xall, any_v1)
imp = pd.Series(clf_full.feature_importances_, index=FEATS).sort_values(ascending=False)
recon["top_leaking_features"] = {k: round(float(v), 5) for k, v in imp.head(25).items()}
print("  상위 누출 피처:", ", ".join(imp.head(10).index))
R["is_sds_v1_reconstruction"] = recon

print("\n[3-c] baseline: 전체 vs sds_v1 제외 vs sds_v1 만")
perf = {}
for ep in EPS:
    ycol = f"y_{ep}_bin"
    is_v1 = (D[f"y_{ep}_src"] == "sds_v1").to_numpy()
    allm = np.ones(len(D), bool)
    perf[ep] = {
        "full": cv_eval(allm, ycol, Xall),
        "exclude_sds_v1": cv_eval(~is_v1, ycol, Xall),
        "only_sds_v1": cv_eval(is_v1, ycol, Xall),
    }
    for k, v in perf[ep].items():
        if "error" in v:
            print(f"  {ep:5s} {k:16s} {v}")
        else:
            print(f"  {ep:5s} {k:16s} n={v['n']:4d} pos={v['pos_rate']:.3f} "
                  f"F1={v['f1']} ROC-AUC={v['roc_auc']} "
                  f"PR-AUC={v['pr_auc']} bAcc={v['balanced_acc']}")
    f_, e_ = perf[ep]["full"], perf[ep]["exclude_sds_v1"]
    if "error" not in e_:
        perf[ep]["delta_exclude_minus_full"] = {
            k: (round(e_[k] - f_[k], 4) if e_[k] is not None and f_[k] is not None
                else None) for k in ("f1", "roc_auc", "pr_auc", "balanced_acc")}
R["baseline_performance"] = perf

# 보조: sds_2nd(양성률 0.80~0.86)도 함께 뺀 더 보수적인 하한
print("\n[3-d] 보수적 하한: sds_v1 + sds_2nd 동시 제외")
cons = {}
for ep in EPS:
    m = (~D[f"y_{ep}_src"].isin(["sds_v1", "sds_2nd"])).to_numpy()
    cons[ep] = cv_eval(m, f"y_{ep}_bin", Xall)
    v = cons[ep]
    if "error" in v:
        print(f"  {ep:5s} {v}")
    else:
        print(f"  {ep:5s} n={v['n']:4d} pos={v['pos_rate']:.3f} F1={v['f1']} "
              f"ROC-AUC={v['roc_auc']} PR-AUC={v['pr_auc']} bAcc={v['balanced_acc']}")
R["conservative_exclude_v1_and_2nd"] = cons

# ==================================================== 4. 누출이 화학 유래인가 메타 유래인가
print("\n[4] 누출원 분해: 수집/부기(bookkeeping) 피처 제거 후 is_sds_v1 복원력")
META_PAT = ("n_tox", "has_tox", "tox_n_", "has_y", "trainable", "has_ntp", "n_ntp",
            "n_y", "review_matched", "has_ghs_label", "has_epa_target", "ambiguous",
            "_isna", "t11_present", "t11_gated", "n_smiles", "n_ing", "n_pct",
            "align", "pc2_n_", "pc2_parse", "ice_n_", "n_structural")
CHEM = [c for c in FEATS if not any(p in c for p in META_PAT)]
META = [c for c in FEATS if c not in CHEM]
Xchem = np.nan_to_num(D[CHEM].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
decomp = {"n_chem_feats": len(CHEM), "n_meta_feats": len(META),
          "meta_feats_dropped": META, "per_endpoint": {}}
for ep in EPS:
    tgt = (D[f"y_{ep}_src"] == "sds_v1").astype(int).to_numpy()
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)
    a = []
    for tr, te in cv.split(Xchem, tgt, groups=GROUPS):
        c = rf().fit(Xchem[tr], tgt[tr])
        a.append(roc_auc_score(tgt[te], c.predict_proba(Xchem[te])[:, 1]))
    decomp["per_endpoint"][ep] = {
        "chem_only_roc_auc": round(float(np.mean(a)), 4),
        "full_X_roc_auc": recon[ep]["all_rows"]["roc_auc"]}
    print(f"  {ep:5s} chem-only AUC={np.mean(a):.4f} "
          f"(full X = {recon[ep]['all_rows']['roc_auc']})")
R["leak_decomposition"] = decomp

with open(f"{V4}/label_source_bias_report.json", "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)
print(f"\n저장: {V4}/label_source_bias_report.json")
