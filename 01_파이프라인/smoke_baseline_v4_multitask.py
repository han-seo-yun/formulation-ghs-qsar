#!/usr/bin/env python3
"""축 B — 모델링 고도화: 멀티태스크(chain) + read-across + conformal prediction.

smoke_baseline_v3.py의 베이스라인(RF, StratifiedGroupKFold(group_key))을 그대로
재현한 뒤, 그 위에 세 가지를 누적 비교한다. Track A/B 자체나 CT 공식은 바꾸지 않는다.

  1) eye -> skin -> sens 체인 피처: 앞 엔드포인트의 out-of-fold 예측확률을 다음
     엔드포인트의 입력 피처로 추가. 리키지 방지: 체인 피처를 만드는 eye/skin
     모델은 "현재 평가 fold의 group_key를 가진 행"을 학습에서 완전히 제외한 뒤
     예측한다 (group-aware nested OOF).
  2) read-across: fp_form_pooled.npz(제형 pooled fingerprint) 기반 Tanimoto
     최근접 k=15 이웃 가중투표. 이웃 탐색 시에도 같은 group_key는 제외
     (leave-group-out) — 계획서 §4 리스크 완화 항목 그대로 적용.
  3) conformal prediction: split-conformal, 클래스별(Mondrian) 캘리브레이션.
     alpha=0.10(90% 커버리지 목표)에서 실제 커버리지·평균 예측집합 크기 산출.

산출: 04_모델산출물/v4/multitask_report.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold

ROOT = str(Path(__file__).resolve().parent.parent)
OUT = f"{ROOT}/04_모델산출물/v4"  # 2026-08-29부터 v4 기준
OUT4 = f"{ROOT}/04_모델산출물/v4"
RNG = 0

X = pd.read_parquet(f"{OUT}/X_formulation.parquet")
Y = pd.read_parquet(f"{OUT}/y_formulation.parquet")
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
FEATS = [c for c in X.columns if c != "Formulation_ID"]
Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
GROUPS = D["group_key"].astype(str).to_numpy()

fp = np.load(f"{OUT}/fp_form_pooled.npz", allow_pickle=True)
fp_fid = fp["fid"]
# max-pool(앞 2048열)은 농도와 무관하게 "어떤 성분이든 그 비트를 가지면 1" —
# 미량 불활성 성분(계면활성제 극소량 등)이 유사도를 지배해 노이즈를 키운다.
# wmean(뒤 2048열)은 농도가중 평균이라 "농도가 독성을 만든다"는 원칙에 더 부합.
# 농도데이터가 없어 wmean이 전부 0인 행(225/1675)만 max로 대체(완전 결측 방지).
fp_max = fp["morgan"][:, :2048]
fp_wmean = fp["morgan"][:, 2048:]
degenerate = fp_wmean.sum(axis=1) == 0
fp_vec = np.where(degenerate[:, None], fp_max, fp_wmean)
fid_to_fprow = {fid: i for i, fid in enumerate(fp_fid)}
FP = np.zeros((len(D), fp_vec.shape[1]), dtype=np.float32)
for i, fid in enumerate(D["Formulation_ID"]):
    r = fid_to_fprow.get(fid)
    if r is not None:
        FP[i] = fp_vec[r]
FP_NORM = np.linalg.norm(FP, axis=1) + 1e-9


def rf():
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                   min_samples_leaf=2, n_jobs=-1, random_state=RNG)


def pos_proba(clf, X):
    proba = clf.predict_proba(X)
    pos_col = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
    return proba[:, pos_col]


def oof_chain_feature(ycol, exclude_groups, feats):
    """exclude_groups(그 fold의 group_key 집합)를 학습에서 빼고 나머지 라벨행으로
    학습한 모델로, 전체 D에 대한 예측확률을 반환 (다음 엔드포인트의 체인 피처용)."""
    m = D[ycol].notna().to_numpy() & (~pd.Series(GROUPS).isin(exclude_groups).to_numpy())
    if m.sum() < 30 or D.loc[m, ycol].nunique() < 2:
        return np.full(len(D), 0.5)
    y = D.loc[m, ycol].astype(int).to_numpy()
    clf = rf().fit(feats[m], y)
    return pos_proba(clf, feats)


def readacross_feature(target_idx, all_idx, y_all, exclude_groups_per_row, k=15):
    """target_idx 각 행에 대해, all_idx(라벨보유) 중 같은 group_key를 제외한 Tanimoto
    최근접 k개의 가중(유사도) 투표 확률을 반환."""
    out = np.full(len(target_idx), 0.5)
    pool_fp = FP[all_idx]
    pool_norm = FP_NORM[all_idx]
    pool_groups = GROUPS[all_idx]
    for pos, ti in enumerate(target_idx):
        v, vn = FP[ti], FP_NORM[ti]
        if vn <= 1e-9:
            continue
        sims = (pool_fp @ v) / (pool_norm * vn)
        mask = pool_groups != GROUPS[ti]
        sims_m = np.where(mask, sims, -1.0)
        if (sims_m > -1.0).sum() == 0:
            continue
        top = np.argsort(sims_m)[-k:]
        top = top[sims_m[top] > -1.0]
        w = np.clip(sims_m[top], 0, None) + 1e-6
        out[pos] = float(np.average(y_all[top], weights=w))
    return out


def conformal_eval(clf, X_cal, y_cal, X_te, alpha=0.10):
    """split-conformal, 클래스별(Mondrian) 임계값. 반환: (coverage, avg_set_size)."""
    classes = clf.classes_
    proba_cal = clf.predict_proba(X_cal)
    thresh = {}
    for ci, c in enumerate(classes):
        m = y_cal == c
        if m.sum() < 5:
            thresh[c] = 0.0
            continue
        scores = 1.0 - proba_cal[m, ci]  # nonconformity
        thresh[c] = float(np.quantile(scores, 1 - alpha, method="higher"))
    proba_te = clf.predict_proba(X_te)
    pred_sets = []
    for row in proba_te:
        s = {c for ci, c in enumerate(classes) if (1.0 - row[ci]) <= thresh[c]}
        pred_sets.append(s if s else {classes[int(np.argmax(row))]})
    return pred_sets


results = {"baseline": {}, "with_chain": {}, "with_readacross": {}, "with_both": {},
           "conformal": {}}

CHAIN = {"eye": None, "skin": "eye", "sens": "skin"}  # 체인 순서: eye -> skin -> sens

for ep in ("eye", "skin", "sens"):
    ycol = f"y_{ep}_bin"
    m = D[ycol].notna().to_numpy()
    if m.sum() < 60:
        continue
    y = D.loc[m, ycol].astype(int).to_numpy()
    g = GROUPS[m]
    idx_all = np.where(m)[0]
    cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)

    f1_base, f1_chain, f1_ra, f1_both = [], [], [], []
    cov_list, size_list = [], []

    for tr, te in cv.split(Xall[m], y, groups=g):
        tr_idx, te_idx = idx_all[tr], idx_all[te]
        test_groups = set(GROUPS[te_idx])

        # --- baseline ---
        clf_b = rf().fit(Xall[tr_idx], y[tr])
        pred_b = clf_b.predict(Xall[te_idx])
        f1_base.append(f1_score(y[te], pred_b, zero_division=0))

        # --- + chain feature (앞 엔드포인트, group-aware nested OOF) ---
        prev_ep = CHAIN[ep]
        if prev_ep is not None:
            prev_ycol = f"y_{prev_ep}_bin"
            chain_feat_all = oof_chain_feature(prev_ycol, test_groups, Xall)
            X_chain = np.hstack([Xall, chain_feat_all[:, None]])
        else:
            X_chain = np.hstack([Xall, np.full((len(Xall), 1), 0.5)])
        clf_c = rf().fit(X_chain[tr_idx], y[tr])
        pred_c = clf_c.predict(X_chain[te_idx])
        f1_chain.append(f1_score(y[te], pred_c, zero_division=0))

        # --- + read-across feature (leave-group-out kNN) ---
        ra_all = np.full(len(D), 0.5)
        ra_tr = readacross_feature(tr_idx, tr_idx, y[tr], None, k=15)
        ra_te = readacross_feature(te_idx, tr_idx, y[tr], None, k=15)
        ra_all[tr_idx], ra_all[te_idx] = ra_tr, ra_te
        X_ra = np.hstack([Xall, ra_all[:, None]])
        clf_r = rf().fit(X_ra[tr_idx], y[tr])
        pred_r = clf_r.predict(X_ra[te_idx])
        f1_ra.append(f1_score(y[te], pred_r, zero_division=0))

        # --- + chain + read-across ---
        X_both = np.hstack([X_chain, ra_all[:, None]])
        clf_bo = rf().fit(X_both[tr_idx], y[tr])
        pred_bo = clf_bo.predict(X_both[te_idx])
        f1_both.append(f1_score(y[te], pred_bo, zero_division=0))

        # --- conformal (X_both 최종모델 기준, train 절반을 calibration으로) ---
        # tr/te 는 라벨 서브셋(y) 기준 위치, tr_idx/te_idx 는 전체 D 기준 위치 — 둘을
        # 같은 순서로 나눠야 y[cal_pos]/X_both[cal_idx]가 같은 행을 가리킨다.
        n_tr = len(tr)
        cal_n = max(20, n_tr // 4)
        cal_pos, proper_pos = tr[:cal_n], tr[cal_n:]
        cal_idx, proper_idx = idx_all[cal_pos], idx_all[proper_pos]
        y_cal, y_proper = y[cal_pos], y[proper_pos]
        if len(np.unique(y_proper)) < 2:
            continue
        clf_conf = rf().fit(X_both[proper_idx], y_proper)
        pred_sets = conformal_eval(clf_conf, X_both[cal_idx], y_cal, X_both[te_idx], alpha=0.10)
        covered = [int(y[te][i]) in pred_sets[i] for i in range(len(te))]
        cov_list.append(np.mean(covered))
        size_list.append(np.mean([len(s) for s in pred_sets]))

    results["baseline"][ep] = round(float(np.mean(f1_base)), 4)
    results["with_chain"][ep] = round(float(np.mean(f1_chain)), 4)
    results["with_readacross"][ep] = round(float(np.mean(f1_ra)), 4)
    results["with_both"][ep] = round(float(np.mean(f1_both)), 4)
    results["conformal"][ep] = {
        "target_coverage": 0.90,
        "empirical_coverage": round(float(np.mean(cov_list)), 4) if cov_list else None,
        "avg_pred_set_size": round(float(np.mean(size_list)), 4) if size_list else None,
    }
    print(f"{ep:5s}  baseline={np.mean(f1_base):.3f}  +chain={np.mean(f1_chain):.3f}  "
          f"+readacross={np.mean(f1_ra):.3f}  +both={np.mean(f1_both):.3f}  "
          f"| conformal cov={np.mean(cov_list):.3f} size={np.mean(size_list):.2f}")

with open(f"{OUT4}/multitask_report.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print(f"\n저장: {OUT4}/multitask_report.json")
