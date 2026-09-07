#!/usr/bin/env python3
"""v3(원본) vs v4(6/30 감사 병합) 동일 CV로 Track A 성능 비교.

smoke_baseline_v3.py와 완전히 동일한 설정(RF 300tree, class_weight=balanced,
StratifiedGroupKFold(group_key), random_state=0)을 두 산출물에 각각 적용한다.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold

BASE = "/Users/hanseoyun/Desktop/260830/04_모델산출물"


def eval_version(tag):
    OUT = f"{BASE}/{tag}"
    X = pd.read_parquet(f"{OUT}/X_formulation.parquet")
    Y = pd.read_parquet(f"{OUT}/y_formulation.parquet")
    D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
    FEATS = [c for c in X.columns if c != "Formulation_ID"]
    Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    out = {}
    for ep in ("eye", "skin", "sens"):
        ycol = f"y_{ep}_bin"
        m = D[ycol].notna().to_numpy()
        if m.sum() < 60:
            continue
        y = D.loc[m, ycol].astype(int).to_numpy()
        Xi = Xall[m]
        g = D.loc[m, "group_key"].astype(str).to_numpy()
        cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0)
        f1s, bas = [], []
        for tr, te in cv.split(Xi, y, groups=g):
            clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                          min_samples_leaf=2, n_jobs=-1, random_state=0)
            clf.fit(Xi[tr], y[tr])
            p = clf.predict(Xi[te])
            f1s.append(f1_score(y[te], p, zero_division=0))
            bas.append(balanced_accuracy_score(y[te], p))
        out[ep] = {"n": int(m.sum()), "n_features": len(FEATS),
                    "f1": round(float(np.mean(f1s)), 4), "balanced_acc": round(float(np.mean(bas)), 4)}
    return out


r3 = eval_version("v3")
r4 = eval_version("v4")

print(f"{'엔드포인트':6s} {'n(v3)':>6s} {'n(v4)':>6s} {'F1(v3)':>8s} {'F1(v4)':>8s} {'ΔF1':>7s} "
      f"{'balAcc(v3)':>11s} {'balAcc(v4)':>11s}")
for ep in ("eye", "skin", "sens"):
    a, b = r3.get(ep), r4.get(ep)
    if not a or not b:
        continue
    d = b["f1"] - a["f1"]
    print(f"{ep:6s} {a['n']:6d} {b['n']:6d} {a['f1']:8.4f} {b['f1']:8.4f} {d:+7.4f} "
          f"{a['balanced_acc']:11.4f} {b['balanced_acc']:11.4f}")

print(f"\n피처 수: v3={r3['eye']['n_features']} v4={r4['eye']['n_features']}")
