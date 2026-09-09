#!/usr/bin/env python3
"""out/v2 산출물이 정말 '모델에 바로 넣을 수 있는' 상태인지 확인하는 스모크 베이스라인.

성능을 주장하려는 게 아니다 — 배관(누출차단·그룹분할·불균형·Track A/B 분기)이
실제로 돌아가는지만 본다. 결과는 out/v2/smoke_report.json.
"""
import json
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold

warnings.filterwarnings("ignore")
ROOT = str(Path(__file__).resolve().parent.parent)
OUT = f"{ROOT}/04_모델산출물/v2"

X = pd.read_parquet(f"{OUT}/X_formulation.parquet")
Y = pd.read_parquet(f"{OUT}/y_formulation.parquet")
MAN = pd.read_csv(f"{OUT}/feature_manifest.csv")
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
assert len(D) == len(X)

leak = set(MAN.loc[MAN.role == "label_source", "column"])
bad = sorted(leak & set(X.columns))
print(f"X열 {X.shape[1]-1} · 누출후보 교차 {len(bad)}" + (f" !! {bad[:8]}" for _ in [0]).__next__()
      if bad else f"X열 {X.shape[1]-1} · 누출후보 교차 0 (OK)")

FEATS = [c for c in X.columns if c != "Formulation_ID"]
rep = {"n_features": len(FEATS), "leak_intersection": bad, "tasks": {}}

for ep in ("eye", "skin", "sens"):
    for mode, ycol in (("binary", f"y_{ep}_bin"), ("ordinal", f"y_{ep}_ord")):
        m = D[ycol].notna()
        if m.sum() < 60:
            continue
        y = D.loc[m, ycol].astype(int).to_numpy()
        Xi = D.loc[m, FEATS].to_numpy(dtype=np.float64)
        Xi = np.nan_to_num(Xi, nan=0.0, posinf=0.0, neginf=0.0)
        g = D.loc[m, "group_key"].astype(str).to_numpy()
        if len(np.unique(y)) < 2:
            continue
        # 클래스가 너무 희소한 층은 StratifiedGroupKFold 가 못 나눈다 → 이진으로 축약
        vc = pd.Series(y).value_counts()
        if (vc < 5).any() and mode == "ordinal":
            continue
        cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=0)
        f1s, bas, dum = [], [], []
        for tr, te in cv.split(Xi, y, groups=g):
            clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                         min_samples_leaf=2, n_jobs=-1, random_state=0)
            clf.fit(Xi[tr], y[tr])
            p = clf.predict(Xi[te])
            avg = "binary" if mode == "binary" else "macro"
            f1s.append(f1_score(y[te], p, average=avg, zero_division=0))
            bas.append(balanced_accuracy_score(y[te], p))
            dm = DummyClassifier(strategy="most_frequent").fit(Xi[tr], y[tr])
            dum.append(f1_score(y[te], dm.predict(Xi[te]), average=avg, zero_division=0))
        rep["tasks"][f"{ep}_{mode}"] = {
            "n": int(m.sum()), "classes": {str(k): int(v) for k, v in vc.items()},
            "f1": round(float(np.mean(f1s)), 4), "f1_std": round(float(np.std(f1s)), 4),
            "balanced_acc": round(float(np.mean(bas)), 4),
            "dummy_f1": round(float(np.mean(dum)), 4),
            "n_groups": int(len(np.unique(g)))}
        print(f"  {ep:5s} {mode:8s} n={m.sum():5d} F1={np.mean(f1s):.3f}"
              f"±{np.std(f1s):.3f}  balAcc={np.mean(bas):.3f}  dummy={np.mean(dum):.3f}")

# Track B: 잔차 방향 분류 (CT 가 과소/정확/과대인지) — CT 커버리지 있는 행만
for ep in ("eye", "skin", "sens"):
    col = f"resid_{ep}"
    m = D[col].notna()
    if m.sum() < 60:
        rep["tasks"][f"{ep}_trackB"] = {"n": int(m.sum()), "skipped": "표본 부족"}
        print(f"  {ep:5s} trackB   n={m.sum():5d}  (표본 부족 — 스킵)")
        continue
    y = np.sign(D.loc[m, col].to_numpy()).astype(int)
    Xi = np.nan_to_num(D.loc[m, FEATS].to_numpy(dtype=np.float64), nan=0.0,
                       posinf=0.0, neginf=0.0)
    g = D.loc[m, "group_key"].astype(str).to_numpy()
    vc = pd.Series(y).value_counts()
    if len(vc) < 2 or vc.min() < 5:
        rep["tasks"][f"{ep}_trackB"] = {"n": int(m.sum()), "skipped": f"클래스 희소 {dict(vc)}"}
        print(f"  {ep:5s} trackB   n={m.sum():5d}  (클래스 희소 {dict(vc)} — 스킵)")
        continue
    cv = StratifiedGroupKFold(n_splits=min(5, int(vc.min())), shuffle=True, random_state=0)
    f1s = []
    for tr, te in cv.split(Xi, y, groups=g):
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                     min_samples_leaf=2, n_jobs=-1, random_state=0)
        clf.fit(Xi[tr], y[tr])
        f1s.append(f1_score(y[te], clf.predict(Xi[te]), average="macro", zero_division=0))
    rep["tasks"][f"{ep}_trackB"] = {"n": int(m.sum()), "macro_f1": round(float(np.mean(f1s)), 4),
                                    "classes": {str(k): int(v) for k, v in vc.items()}}
    print(f"  {ep:5s} trackB   n={m.sum():5d} macroF1={np.mean(f1s):.3f} {dict(vc)}")

json.dump(rep, open(f"{OUT}/smoke_report.json", "w"), ensure_ascii=False, indent=1)
print(f"\n→ {OUT}/smoke_report.json")
