#!/usr/bin/env python3
"""han-seo-yun/qsar-toxicity-modeling 저장소(같은 eye/skin/sens 엔드포인트를 다루는
2026 Spring ML 프로젝트)에서 채택 가능한 기법 검토 결과 — 가장 먼저 적용해볼 것:

  **out-of-fold 확률 기반 결정 임계값 튜닝** (modeling.py의 핵심 기법).
  기존 smoke_baseline_v3/v4.py는 predict()의 기본 임계값 0.5를 그대로 쓴다.
  라벨이 불균형(skin NC 614 vs 나머지, sens NC 209 vs 나머지)한 상태에서 0.5는
  최적이 아닐 수 있다. 학습 fold 내부에서만 OOF 확률로 balanced_accuracy를
  최대화하는 임계값을 찾고, 그 임계값을 held-out fold에 적용한다 — 데이터를
  새로 만들거나 라벨을 바꾸지 않는 순수 방법론 개선이며, threshold 선택에
  test fold 정보가 전혀 들어가지 않아 리키지가 없다.

  RDKit canonicalization/salt-strip(chemistry.py)은 별도로 `canonicalize_ingredient_smiles.py`에서 적용.

산출: 04_모델산출물/v4/threshold_tuning_report.json
"""
import json

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold

OUT = "/Users/hanseoyun/Desktop/260830/04_모델산출물/v4"
RNG = 0

X = pd.read_parquet(f"{OUT}/X_formulation.parquet")
Y = pd.read_parquet(f"{OUT}/y_formulation.parquet")
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
FEATS = [c for c in X.columns if c != "Formulation_ID"]
Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
GROUPS = D["group_key"].astype(str).to_numpy()

results = {}
for ep in ("eye", "skin", "sens"):
    ycol = f"y_{ep}_bin"
    m = D[ycol].notna().to_numpy()
    if m.sum() < 60:
        continue
    y = D.loc[m, ycol].astype(int).to_numpy()
    Xi = Xall[m]
    g = GROUPS[m]
    idx_all = np.where(m)[0]
    outer_cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)

    f1_default, f1_tuned, ba_default, ba_tuned, thresholds_used = [], [], [], [], []
    for tr, te in outer_cv.split(Xi, y, groups=g):
        Xtr, ytr, gtr = Xi[tr], y[tr], g[tr]
        Xte, yte = Xi[te], y[te]

        # --- 기본 임계값 0.5 (기존 방식) ---
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                      min_samples_leaf=2, n_jobs=-1, random_state=RNG)
        clf.fit(Xtr, ytr)
        pos_col = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
        proba_te = clf.predict_proba(Xte)[:, pos_col]
        pred_default = (proba_te >= 0.5).astype(int)
        f1_default.append(f1_score(yte, pred_default, zero_division=0))
        ba_default.append(balanced_accuracy_score(yte, pred_default))

        # --- OOF 임계값 튜닝: 학습 fold 내부에서만 nested CV로 OOF 확률 생성 ---
        inner_cv = StratifiedGroupKFold(n_splits=min(5, int(np.bincount(ytr).min())), shuffle=True, random_state=RNG)
        oof_proba = np.zeros(len(ytr))
        for itr, ite in inner_cv.split(Xtr, ytr, groups=gtr):
            inner_clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                                min_samples_leaf=2, n_jobs=-1, random_state=RNG)
            inner_clf.fit(Xtr[itr], ytr[itr])
            pc = list(inner_clf.classes_).index(1) if 1 in inner_clf.classes_ else 0
            oof_proba[ite] = inner_clf.predict_proba(Xtr[ite])[:, pc]

        best_t, best_ba = 0.5, -1
        for t in np.arange(0.10, 0.91, 0.02):
            ba = balanced_accuracy_score(ytr, (oof_proba >= t).astype(int))
            if ba > best_ba:
                best_ba, best_t = ba, t
        thresholds_used.append(float(best_t))

        pred_tuned = (proba_te >= best_t).astype(int)
        f1_tuned.append(f1_score(yte, pred_tuned, zero_division=0))
        ba_tuned.append(balanced_accuracy_score(yte, pred_tuned))

    results[ep] = {
        "n": int(m.sum()),
        "f1_default_0.5": round(float(np.mean(f1_default)), 4),
        "f1_oof_tuned": round(float(np.mean(f1_tuned)), 4),
        "balanced_acc_default_0.5": round(float(np.mean(ba_default)), 4),
        "balanced_acc_oof_tuned": round(float(np.mean(ba_tuned)), 4),
        "mean_selected_threshold": round(float(np.mean(thresholds_used)), 3),
        "thresholds_per_fold": thresholds_used,
    }
    r = results[ep]
    print(f"{ep:5s} n={r['n']:4d}  F1: {r['f1_default_0.5']:.4f} -> {r['f1_oof_tuned']:.4f}  "
          f"balAcc: {r['balanced_acc_default_0.5']:.4f} -> {r['balanced_acc_oof_tuned']:.4f}  "
          f"(선택임계값 평균={r['mean_selected_threshold']})")

with open(f"{OUT}/threshold_tuning_report.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print(f"\n저장: {OUT}/threshold_tuning_report.json")
