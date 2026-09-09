#!/usr/bin/env python3
"""v5 — conformal 캘리브레이션 분할 결함 수정 + chain feature 과적합 진단 + 성능 보고.

smoke_baseline_v4_multitask.py 를 복제해 다음을 수정/추가했다.
v4 원본은 비교 기준으로 **수정하지 않는다**.

수정 1) split-conformal 캘리브레이션 분할 (CRITICAL)
  v4:  cal_pos, proper_pos = tr[:cal_n], tr[cal_n:]
       -> sklearn 의 train index 는 오름차순 정렬되어 반환되므로 이것은
          "무작위 분할"이 아니라 **행 순서 앞부분 잘라내기**다. 원본 데이터가
          label_source(ntp_measured / sds_* / phase1_sds) 블록별로 뭉쳐 정렬돼
          있어 cal 셋과 proper 셋의 출처 분포가 전혀 다르다 (실측 TV distance
          eye 0.55~0.58, skin 0.55~0.59). 또한 cal 행의 64~86% 가 proper 셋과
          group_key 를 공유한다 -> proper 모델이 cal 행의 그룹동료를 학습해
          cal 의 nonconformity score 가 낙관적으로 작아지고, 임계값이 너무
          타이트해져 test 커버리지가 목표 아래로 떨어진다.
       -> exchangeability(교환가능성) 위반. conformal 의 이론적 커버리지 보장 무효.
  v5:  group-aware 무작위 분할. 같은 group_key 는 cal/proper 중 한쪽에만 배정하고
       (그룹 단위 배정), 그룹의 다수 라벨을 stratum 으로 써 클래스 균형도 맞춘다.

수정 2) 유한표본 보정 분위수
  v4: np.quantile(scores, 1-alpha, method="higher")  -- 점근적 분위수
  v5: level = ceil((n_c + 1) * (1 - alpha)) / n_c  분위수 (Vovk/Lei 등의
      split-conformal 유한표본 보장). n_c 가 작아 level > 1 이면 해당 클래스는
      임계값 +inf (항상 예측집합에 포함) 로 두는 것이 이론상 정확한 처리다.
      (n_c < ceil((n+1)(1-alpha)) 이면 1-alpha 커버리지를 유한표본에서 보장할
      분위수가 존재하지 않으므로, 보수적으로 전체 라벨집합을 내놓아야 한다.
      v4 처럼 점근 분위수를 쓰면 보장이 깨진 채 좁은 집합을 내놓게 된다.)

수정 3) 다중 seed 평가
  cal/proper 분할 seed 5개로 반복해 커버리지 평균±표준편차를 보고한다.
  단일 seed 값을 최종값으로 쓰지 않는다.

추가 4) chain feature 과적합 진단
  v4 의 chain feature 는 "평가 fold 의 group 을 제외하고 학습한 prev-endpoint
  모델"의 예측을 **전체 D**에 적용한다. test 행에 대해서는 정직한 OOS 이지만,
  train 행 대부분은 그 prev 모델의 **학습셋에 포함**되어 있으므로 train 행의
  chain feature 는 in-sample fitted value(거의 정답 라벨)다. 즉 train/test 사이
  chain feature 의 분포가 다르다(stacking 의 전형적 오류). v5 는 train 행에도
  inner group-aware OOF 를 적용한 chain_mode="nested_oof" 를 함께 돌려
  두 모드의 train/test AUC 를 비교 보고한다.

추가 5) ROC-AUC / PR-AUC / F1 / balanced_accuracy 를 4개 변형(baseline, +chain,
  +read-across, +both)에 대해 CV seed 3개로 산출. dummy(다수클래스) 기준선과
  PR-AUC 의 양성유병률 기준선도 함께 보고.

CAVEAT: 본 스크립트는 **현행 v4 데이터**(04_모델산출물/v4/*) 기준이다. 별도
  에이전트가 v5 데이터셋(라벨 출처 오염 이슈 수정)을 병행 작업 중이므로,
  아래 수치는 데이터 오염이 남아 있는 상태의 값이다.

산출: 04_모델산출물/v4_conformal/conformal_fix_report.json
      04_모델산출물/v4_conformal/roc_auc_report.json
"""
import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                             f1_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold

ROOT = str(Path(__file__).resolve().parent.parent)
IN = f"{ROOT}/04_모델산출물/v4"        # 읽기 전용
OUT = f"{ROOT}/04_모델산출물/v4_conformal"  # 새 디렉터리
RNG = 0
ALPHA = 0.10
CAL_SEEDS = [0, 1, 2, 3, 4]
CV_SEEDS = [0, 1, 2]

os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- 데이터 로드
X = pd.read_parquet(f"{IN}/X_formulation.parquet")
Y = pd.read_parquet(f"{IN}/y_formulation.parquet")
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
FEATS = [c for c in X.columns if c != "Formulation_ID"]
Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
GROUPS = D["group_key"].astype(str).to_numpy()
SRC = {ep: D[f"y_{ep}_src"].fillna("NA").astype(str).to_numpy() for ep in ("eye", "skin", "sens")}

fp = np.load(f"{IN}/fp_form_pooled.npz", allow_pickle=True)
fp_fid = fp["fid"]
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

CHAIN = {"eye": None, "skin": "eye", "sens": "skin"}


def rf(seed=RNG):
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  min_samples_leaf=2, n_jobs=-1, random_state=seed)


def pos_proba(clf, Xm):
    proba = clf.predict_proba(Xm)
    pos_col = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
    return proba[:, pos_col]


# ------------------------------------------------------- chain feature (v4 방식)
def chain_feature_v4(prev_ycol, exclude_groups):
    """v4 그대로: exclude_groups(평가 fold 의 group) 를 뺀 나머지로 학습해
    전체 D 를 예측. => test 행은 OOS, train 행은 in-sample(fitted)."""
    m = D[prev_ycol].notna().to_numpy() & (~pd.Series(GROUPS).isin(exclude_groups).to_numpy())
    if m.sum() < 30 or D.loc[m, prev_ycol].nunique() < 2:
        return np.full(len(D), 0.5)
    y = D.loc[m, prev_ycol].astype(int).to_numpy()
    clf = rf().fit(Xall[m], y)
    return pos_proba(clf, Xall)


def chain_feature_nested_oof(prev_ycol, tr_idx, te_idx, n_inner=3, seed=RNG):
    """수정판: train 행에도 inner group-aware OOF 를 적용.
    - te_idx 행: outer test group 전체를 제외하고 학습한 모델로 예측 (v4 와 동일)
    - tr_idx 행: train 그룹을 n_inner 조각으로 나눠, 각 조각(inner val)의 group 과
      outer test group 을 모두 제외한 모델로 그 조각을 예측
    => train/test 양쪽 모두 chain feature 가 out-of-sample 이 되어 분포가 일치."""
    out = np.full(len(D), 0.5)
    te_groups = set(GROUPS[te_idx])
    out[te_idx] = chain_feature_v4(prev_ycol, te_groups)[te_idx]

    tr_groups = np.array(sorted(set(GROUPS[tr_idx])))
    rs = np.random.RandomState(seed)
    perm = rs.permutation(len(tr_groups))
    chunks = np.array_split(perm, n_inner)
    has_prev = D[prev_ycol].notna().to_numpy()
    for ch in chunks:
        val_groups = set(tr_groups[ch])
        fit_m = has_prev & (~pd.Series(GROUPS).isin(val_groups | te_groups).to_numpy())
        val_rows = tr_idx[np.isin(GROUPS[tr_idx], list(val_groups))]
        if len(val_rows) == 0:
            continue
        if fit_m.sum() < 30 or D.loc[fit_m, prev_ycol].nunique() < 2:
            out[val_rows] = 0.5
            continue
        yf = D.loc[fit_m, prev_ycol].astype(int).to_numpy()
        clf = rf().fit(Xall[fit_m], yf)
        out[val_rows] = pos_proba(clf, Xall[val_rows])
    return out


# ------------------------------------------------------------------ read-across
def readacross_feature(target_idx, all_idx, y_all, k=15):
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


# ---------------------------------------------------- cal/proper 분할 (핵심 수정)
def split_cal_proper_legacy(tr, cal_frac=0.25, min_cal=20, seed=0):
    """v4 원본 로직 재현 (버그): train index 의 앞부분을 잘라 cal 로 쓴다."""
    n_tr = len(tr)
    cal_n = max(min_cal, int(n_tr * cal_frac))
    return tr[:cal_n], tr[cal_n:]


def split_cal_proper_group_random(tr, y_tr, g_tr, cal_frac=0.25, min_cal=20, seed=0):
    # NOTE: 반환값은 legacy 와 동일하게 '라벨 서브셋 위치'(idx_all 에 바로 넣을 수
    # 있는 값)로 맞춘다. 내부 계산은 tr 내 상대위치로 하고 마지막에 tr[...] 로 변환.
    """group-aware 무작위 + 그룹단위 stratified 분할.

    - 같은 group_key 는 cal / proper 중 한쪽에만 들어간다 (그룹 리키지 차단).
    - 그룹의 다수 라벨(majority label)을 stratum 으로 두고 stratum 별로 목표
      행 수만큼 그룹을 무작위 배정 -> cal 의 클래스 구성이 train 과 비슷해진다
      (Mondrian 임계값의 클래스별 표본수를 확보하기 위함).
    - 반환값은 tr 내 '라벨 서브셋 위치' 배열.
    """
    rs = np.random.RandomState(seed)
    uniq = np.array(sorted(set(g_tr)))
    # 그룹별 다수 라벨 / 크기
    gmaj, gsize = {}, {}
    for u in uniq:
        yy = y_tr[g_tr == u]
        gmaj[u] = int(round(yy.mean()))
        gsize[u] = len(yy)
    target_total = max(min_cal, int(len(tr) * cal_frac))
    cal_groups = set()
    for lab in (0, 1):
        gl = np.array([u for u in uniq if gmaj[u] == lab])
        if len(gl) == 0:
            continue
        n_lab_rows = sum(gsize[u] for u in gl)
        target_lab = int(round(target_total * n_lab_rows / len(tr)))
        rs.shuffle(gl)
        acc = 0
        for u in gl:
            if acc >= target_lab:
                break
            cal_groups.add(u)
            acc += gsize[u]
    cal_mask = np.isin(g_tr, list(cal_groups))
    cal_pos = tr[np.where(cal_mask)[0]]
    proper_pos = tr[np.where(~cal_mask)[0]]
    return cal_pos, proper_pos


# ------------------------------------------------------------------- conformal
def mondrian_thresholds(clf, X_cal, y_cal, alpha=ALPHA, finite_sample=True):
    """클래스별(Mondrian) split-conformal 임계값.

    유한표본 보정: level = ceil((n_c + 1) * (1 - alpha)) / n_c 분위수.
    level > 1 이면 해당 클래스에 대해 1-alpha 커버리지를 유한표본에서 보장할
    경험분위수가 존재하지 않으므로 임계값 = +inf (항상 포함) 로 둔다 — 보수적
    이지만 이론적으로 올바른 처리다. (v4 는 이 경우에도 점근 분위수를 써서
    보장 없이 좁은 집합을 내놓았다.)
    """
    classes = clf.classes_
    proba_cal = clf.predict_proba(X_cal)
    thresh, ncal = {}, {}
    for ci, c in enumerate(classes):
        m = y_cal == c
        n_c = int(m.sum())
        ncal[int(c)] = n_c
        if n_c == 0:
            thresh[c] = np.inf
            continue
        scores = 1.0 - proba_cal[m, ci]  # nonconformity
        if finite_sample:
            level = math.ceil((n_c + 1) * (1 - alpha)) / n_c
            if level > 1.0:
                thresh[c] = np.inf
                continue
        else:
            level = 1 - alpha
        thresh[c] = float(np.quantile(scores, level, method="higher"))
    return classes, thresh, ncal


def predict_sets(clf, classes, thresh, X_te):
    proba_te = clf.predict_proba(X_te)
    sets = []
    for row in proba_te:
        s = {int(c) for ci, c in enumerate(classes) if (1.0 - row[ci]) <= thresh[c]}
        sets.append(s if s else {int(classes[int(np.argmax(row))])})
    return sets


# ================================================================= 실험 1: conformal
def run_conformal():
    rep = {"note": "v4 데이터 기준. legacy=v4 버그 재현, fixed=group-aware 무작위 분할",
           "alpha": ALPHA, "target_coverage": 1 - ALPHA, "cal_seeds": CAL_SEEDS,
           "cv": "StratifiedGroupKFold(5, shuffle=True, random_state=0) on group_key",
           "endpoints": {}}
    for ep in ("eye", "skin", "sens"):
        ycol = f"y_{ep}_bin"
        m = D[ycol].notna().to_numpy()
        if m.sum() < 60:
            continue
        y = D.loc[m, ycol].astype(int).to_numpy()
        g = GROUPS[m]
        idx_all = np.where(m)[0]
        src = SRC[ep]
        cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RNG)

        diag_folds = []
        cov = {"legacy": [], "fixed_asym": [], "fixed": []}
        siz = {"legacy": [], "fixed_asym": [], "fixed": []}
        inf_thresh_events = 0

        for fi, (tr, te) in enumerate(cv.split(Xall[m], y, groups=g)):
            tr_idx, te_idx = idx_all[tr], idx_all[te]
            te_groups = set(GROUPS[te_idx])

            # ---- 최종 피처행렬(X_both) 구성: v4 와 동일 (chain=v4 방식, read-across)
            prev_ep = CHAIN[ep]
            if prev_ep is not None:
                cf = chain_feature_v4(f"y_{prev_ep}_bin", te_groups)
            else:
                cf = np.full(len(D), 0.5)
            ra_all = np.full(len(D), 0.5)
            ra_all[tr_idx] = readacross_feature(tr_idx, tr_idx, y[tr])
            ra_all[te_idx] = readacross_feature(te_idx, tr_idx, y[tr])
            X_both = np.hstack([Xall, cf[:, None], ra_all[:, None]])

            # ---------- (A) legacy 진단 + 커버리지 (단일, seed 무관)
            cal_pos, proper_pos = split_cal_proper_legacy(tr)
            ci_l, pi_l = idx_all[cal_pos], idx_all[proper_pos]
            gp = set(GROUPS[pi_l])
            allsrc = sorted(set(src[ci_l]) | set(src[pi_l]))
            pc = np.array([np.mean(src[ci_l] == s) for s in allsrc])
            pp = np.array([np.mean(src[pi_l] == s) for s in allsrc])
            legacy_diag = {
                "cal_n": len(ci_l), "proper_n": len(pi_l),
                "cal_pos_rate": round(float(y[cal_pos].mean()), 4),
                "proper_pos_rate": round(float(y[proper_pos].mean()), 4),
                "test_pos_rate": round(float(y[te].mean()), 4),
                "cal_src_composition": {s: round(float(v), 4) for s, v in zip(allsrc, pc)},
                "proper_src_composition": {s: round(float(v), 4) for s, v in zip(allsrc, pp)},
                "src_total_variation_distance": round(float(0.5 * np.abs(pc - pp).sum()), 4),
                "cal_rows_sharing_group_with_proper": round(
                    float(np.mean([gg in gp for gg in GROUPS[ci_l]])), 4),
            }
            if len(np.unique(y[proper_pos])) >= 2:
                clf = rf().fit(X_both[pi_l], y[proper_pos])
                cls, th, _ = mondrian_thresholds(clf, X_both[ci_l], y[cal_pos],
                                                 finite_sample=False)
                ps = predict_sets(clf, cls, th, X_both[te_idx])
                cov["legacy"].append(float(np.mean([int(y[te][i]) in ps[i] for i in range(len(te))])))
                siz["legacy"].append(float(np.mean([len(s) for s in ps])))

            # ---------- (B) fixed: group-aware 무작위 분할 × cal seed 5개
            fixed_diag = []
            for cs in CAL_SEEDS:
                cal_pos, proper_pos = split_cal_proper_group_random(tr, y[tr], g[tr], seed=cs)
                if len(cal_pos) < 10 or len(np.unique(y[proper_pos])) < 2:
                    continue
                ci_f, pi_f = idx_all[cal_pos], idx_all[proper_pos]
                gpf = set(GROUPS[pi_f])
                allsrc = sorted(set(src[ci_f]) | set(src[pi_f]))
                pc = np.array([np.mean(src[ci_f] == s) for s in allsrc])
                pp = np.array([np.mean(src[pi_f] == s) for s in allsrc])
                fixed_diag.append({
                    "cal_seed": cs, "cal_n": len(ci_f), "proper_n": len(pi_f),
                    "cal_pos_rate": round(float(y[cal_pos].mean()), 4),
                    "proper_pos_rate": round(float(y[proper_pos].mean()), 4),
                    "src_total_variation_distance": round(float(0.5 * np.abs(pc - pp).sum()), 4),
                    "cal_rows_sharing_group_with_proper": round(
                        float(np.mean([gg in gpf for gg in GROUPS[ci_f]])), 4),
                })
                clf = rf().fit(X_both[pi_f], y[proper_pos])
                # 유한표본 보정 O / X 를 모두 기록
                for key, fs in (("fixed", True), ("fixed_asym", False)):
                    cls, th, ncal = mondrian_thresholds(clf, X_both[ci_f], y[cal_pos],
                                                        finite_sample=fs)
                    if fs and any(np.isinf(v) for v in th.values()):
                        inf_thresh_events += 1
                    ps = predict_sets(clf, cls, th, X_both[te_idx])
                    cov[key].append(float(np.mean([int(y[te][i]) in ps[i] for i in range(len(te))])))
                    siz[key].append(float(np.mean([len(s) for s in ps])))

            diag_folds.append({"fold": fi, "legacy": legacy_diag, "fixed": fixed_diag})
            print(f"[conformal] {ep} fold{fi} done")

        def ms(v):
            return {"mean": round(float(np.mean(v)), 4), "sd": round(float(np.std(v, ddof=1)), 4),
                    "n": len(v), "min": round(float(np.min(v)), 4),
                    "max": round(float(np.max(v)), 4)} if len(v) > 1 else (
                {"mean": round(float(np.mean(v)), 4), "sd": None, "n": len(v)} if v else None)

        rep["endpoints"][ep] = {
            "n_labeled": int(m.sum()), "pos_rate": round(float(y.mean()), 4),
            "n_groups": int(len(set(g))),
            "split_diagnostics": diag_folds,
            "coverage": {k: ms(v) for k, v in cov.items()},
            "avg_pred_set_size": {k: ms(v) for k, v in siz.items()},
            "n_infinite_threshold_events": inf_thresh_events,
        }
        print(f"{ep:5s} coverage legacy={ms(cov['legacy'])} fixed={ms(cov['fixed'])} "
              f"fixed_asym={ms(cov['fixed_asym'])}")
    with open(f"{OUT}/conformal_fix_report.json", "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(f"저장: {OUT}/conformal_fix_report.json")
    return rep


# =========================================== 실험 2: 성능지표 + chain 과적합 진단
def run_metrics():
    rep = {"note": "v4 데이터 기준. 각 지표는 fold 별 산출 후 CV seed 3개 평균±sd",
           "model": "RF(n_estimators=300, class_weight=balanced, min_samples_leaf=2)",
           "cv": "StratifiedGroupKFold(5, shuffle=True, random_state=cv_seed) on group_key",
           "cv_seeds": CV_SEEDS, "endpoints": {}}
    for ep in ("eye", "skin", "sens"):
        ycol = f"y_{ep}_bin"
        m = D[ycol].notna().to_numpy()
        if m.sum() < 60:
            continue
        y = D.loc[m, ycol].astype(int).to_numpy()
        g = GROUPS[m]
        idx_all = np.where(m)[0]
        prev_ep = CHAIN[ep]
        variants = ["baseline", "with_chain", "with_readacross", "with_both",
                    "with_chain_nestedoof", "with_both_nestedoof"]
        acc = {v: {k: [] for k in ("roc_auc", "pr_auc", "f1", "bal_acc")} for v in variants}
        dummy = {k: [] for k in ("f1", "bal_acc", "roc_auc", "pr_auc")}
        chain_diag = {"v4": {"train_auc_vs_prev": [], "test_auc_vs_prev": [],
                             "train_auc_vs_cur": [], "test_auc_vs_cur": [],
                             "train_mean": [], "test_mean": [],
                             "model_train_auc": [], "model_test_auc": [],
                             "chain_importance": []},
                      "nested_oof": {"train_auc_vs_prev": [], "test_auc_vs_prev": [],
                                     "train_auc_vs_cur": [], "test_auc_vs_cur": [],
                                     "train_mean": [], "test_mean": [],
                                     "model_train_auc": [], "model_test_auc": [],
                                     "chain_importance": []}}

        for cv_seed in CV_SEEDS:
            cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=cv_seed)
            for fi, (tr, te) in enumerate(cv.split(Xall[m], y, groups=g)):
                tr_idx, te_idx = idx_all[tr], idx_all[te]
                te_groups = set(GROUPS[te_idx])
                ytr, yte = y[tr], y[te]

                ra_all = np.full(len(D), 0.5)
                ra_all[tr_idx] = readacross_feature(tr_idx, tr_idx, ytr)
                ra_all[te_idx] = readacross_feature(te_idx, tr_idx, ytr)

                if prev_ep is not None:
                    cf_v4 = chain_feature_v4(f"y_{prev_ep}_bin", te_groups)
                    cf_no = chain_feature_nested_oof(f"y_{prev_ep}_bin", tr_idx, te_idx,
                                                     seed=cv_seed)
                else:
                    cf_v4 = np.full(len(D), 0.5)
                    cf_no = np.full(len(D), 0.5)

                mats = {
                    "baseline": Xall,
                    "with_chain": np.hstack([Xall, cf_v4[:, None]]),
                    "with_readacross": np.hstack([Xall, ra_all[:, None]]),
                    "with_both": np.hstack([Xall, cf_v4[:, None], ra_all[:, None]]),
                    "with_chain_nestedoof": np.hstack([Xall, cf_no[:, None]]),
                    "with_both_nestedoof": np.hstack([Xall, cf_no[:, None], ra_all[:, None]]),
                }
                for v, Xm in mats.items():
                    clf = rf().fit(Xm[tr_idx], ytr)
                    p = pos_proba(clf, Xm[te_idx])
                    pred = clf.predict(Xm[te_idx])
                    acc[v]["roc_auc"].append(roc_auc_score(yte, p) if len(set(yte)) > 1 else np.nan)
                    acc[v]["pr_auc"].append(average_precision_score(yte, p) if len(set(yte)) > 1 else np.nan)
                    acc[v]["f1"].append(f1_score(yte, pred, zero_division=0))
                    acc[v]["bal_acc"].append(balanced_accuracy_score(yte, pred))
                    if v in ("with_chain", "with_chain_nestedoof") and prev_ep is not None:
                        key = "v4" if v == "with_chain" else "nested_oof"
                        cd = chain_diag[key]
                        ptr = pos_proba(clf, Xm[tr_idx])
                        cd["model_train_auc"].append(roc_auc_score(ytr, ptr))
                        cd["model_test_auc"].append(roc_auc_score(yte, p) if len(set(yte)) > 1 else np.nan)
                        cd["chain_importance"].append(float(clf.feature_importances_[-1]))
                        cvec = cf_v4 if v == "with_chain" else cf_no
                        prevy = D[f"y_{prev_ep}_bin"].to_numpy()
                        for nm, rows, ycur in (("train", tr_idx, ytr), ("test", te_idx, yte)):
                            has = ~pd.isna(prevy[rows])
                            if has.sum() > 5 and len(set(prevy[rows][has].astype(int))) > 1:
                                cd[f"{nm}_auc_vs_prev"].append(
                                    roc_auc_score(prevy[rows][has].astype(int), cvec[rows][has]))
                            if len(set(ycur)) > 1:
                                cd[f"{nm}_auc_vs_cur"].append(roc_auc_score(ycur, cvec[rows]))
                            cd[f"{nm}_mean"].append(float(np.mean(cvec[rows])))

                # dummy: 다수클래스 예측
                maj = int(round(ytr.mean()))
                dpred = np.full(len(yte), maj)
                dummy["f1"].append(f1_score(yte, dpred, zero_division=0))
                dummy["bal_acc"].append(balanced_accuracy_score(yte, dpred))
                dummy["roc_auc"].append(0.5)
                dummy["pr_auc"].append(float(yte.mean()))
                print(f"[metrics] {ep} seed{cv_seed} fold{fi} done")

        def ms(v):
            v = [x for x in v if not (isinstance(x, float) and np.isnan(x))]
            if not v:
                return None
            return {"mean": round(float(np.mean(v)), 4),
                    "sd": round(float(np.std(v, ddof=1)), 4) if len(v) > 1 else None,
                    "n": len(v)}

        rep["endpoints"][ep] = {
            "n_labeled": int(m.sum()), "pos_prevalence": round(float(y.mean()), 4),
            "n_groups": int(len(set(g))),
            "metrics": {v: {k: ms(vals) for k, vals in d.items()} for v, d in acc.items()},
            "dummy_majority_baseline": {k: ms(v) for k, v in dummy.items()},
            "pr_auc_prevalence_baseline": round(float(y.mean()), 4),
            "chain_feature_diagnostics": {
                k: {kk: ms(vv) for kk, vv in d.items()} for k, d in chain_diag.items()
            } if prev_ep is not None else "N/A (eye는 chain 선행 엔드포인트 없음)",
        }
        for v in variants:
            mm = rep["endpoints"][ep]["metrics"][v]
            print(f"{ep:5s} {v:22s} ROC={mm['roc_auc']} PR={mm['pr_auc']} "
                  f"F1={mm['f1']} BA={mm['bal_acc']}")
    with open(f"{OUT}/roc_auc_report.json", "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(f"저장: {OUT}/roc_auc_report.json")
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["conformal", "metrics"], default=None)
    a = ap.parse_args()
    if a.only in (None, "conformal"):
        run_conformal()
    if a.only in (None, "metrics"):
        run_metrics()
