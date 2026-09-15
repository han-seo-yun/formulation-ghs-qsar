#!/usr/bin/env python3
"""감작 열 제외 대조 — 규약 §5-3 결정용. 산출: 04_모델산출물/v8_노선2/감작제외_대조.csv

`f_ct_sens_*` 는 감작 **라벨**이 아니라 혼합물 가산 디스크립터라서 L2단독 arm 에
남아 있다. 그러나 감작은 학습에 쓰지 않는다는 원칙과 비대칭으로 보이므로, 이 열이
눈·피부 성능에 실제로 기여하는지 재서 총책임자 결정에 수치를 붙인다.

`run_model_lane2.py` 를 건드리지 않는다 — 대표 산출물의 재현성을 지키기 위해 같은
규약(공통폴드 · 폴드별 τ · 층내부 보정 AUC)을 별도 스크립트에 복제해 2 셀만 돌린다.
규약 문서가 '9 열' 이라고 적어 온 것은 틀렸다. 실측 7 열이고 이 스크립트가 그 목록을
그대로 찍는다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*, v8_누출감사/*
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             matthews_corrcoef, roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold

import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"
CANON_IMP = "native_복원"
N_INNER = 3
DOC_TIERS = ("문서독립", "문서공유_분류절", "문서공유_11절")
COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
AUDIT = L.ROOT / "04_모델산출물" / "v8_누출감사"
OUT = L.ROOT / "04_모델산출물" / "v8_노선2"
log = L.make_logger(OUT / "감작제외_대조.log")


def tau_youden(y, p):
    fpr, tpr, thr = roc_curve(y, p)
    return float(thr[int(np.argmax(tpr - fpr))])


def assemble_pred(p, tau_fold, n):
    """폴드별 τ 를 그 폴드 검증행에만 적용(규약 §2.4)."""
    pred = np.full(n, -1, dtype=int)
    for te, tau in tau_fold:
        pred[te] = (p[te] >= tau).astype(int)
    assert (pred >= 0).all(), "예측 미채움"
    return pred


def oof_probs(Xi, y, g, folds, seed_idx):
    n = len(y)
    p = np.full(n, np.nan)
    tpf = []
    for tr, te in folds:
        p[te] = L.rf(seed_idx).fit(Xi[tr], y[tr]).predict_proba(Xi[te])[:, 1]
        ytr, gtr = y[tr], g[tr]
        pin = np.full(len(tr), np.nan)
        inner = StratifiedGroupKFold(n_splits=N_INNER, shuffle=True,
                                     random_state=1000 + seed_idx)
        for itr, ite in inner.split(np.zeros((len(tr), 1)), ytr, gtr):
            pin[ite] = L.rf(seed_idx).fit(Xi[tr][itr], ytr[itr]) \
                                     .predict_proba(Xi[tr][ite])[:, 1]
        assert not np.isnan(pin).any(), "내부 OOF 미채움"
        tpf.append((te, tau_youden(ytr, pin)))
    assert not np.isnan(p).any(), "외부 OOF 미채움"
    return p, tpf


def mcc_ba(y, pred):
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    rec = tp / (tp + fn) if tp + fn else np.nan
    spec = tn / (tn + fp) if tn + fp else np.nan
    return {"MCC": matthews_corrcoef(y, pred), "BA": (rec + spec) / 2,
            "재현율": rec, "특이도": spec}


def auc_보정(per):
    """일치쌍 가중 층내부 AUC(규약 §4.1-3)."""
    num = den = 0.0
    for d in per:
        w = d["n"] * d["p"] * d["n"] * (1 - d["p"])
        num += w * d["auc"]
        den += w
    return num / den if den > 0 else float("nan")


log("=== 감작 열 제외 대조 (규약 §5-3) ===")
Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
ARM = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))["arm"]
SUB = np.load(AUDIT / "부분집합마스크.npz")
data = L.FormulationData(log)
assert (data.FID == Z["Formulation_ID"]).all(), "행 정렬 이탈"
XM = data.matrices()
CHEM_IDX = {c: i for i, c in enumerate(data.CHEM)}

BASE = ARM["L2단독"]
SENS = [c for c in BASE if "sens" in c]
DROP = [c for c in BASE if "sens" not in c]
log(f"L2단독 {len(BASE)}열 → 감작 {len(SENS)}열 제외 → {len(DROP)}열")
log(f"제외 열: {SENS}")
assert len(SENS) == 7, f"감작 열 수 예상 7, 실측 {len(SENS)} — 문서 수정 필요"


def mat(cols):
    miss = [c for c in cols if c not in CHEM_IDX]
    assert not miss, f"CHEM 에 없는 열 {miss[:5]}"
    return XM[("CT_권고", CANON_IMP)][:, [CHEM_IDX[c] for c in cols]]


RES = []
for ep in EPS:
    m = Z[f"mask_{ep}"]
    y = Z[f"y_{ep}"].astype(int)
    g = Z[f"group_{ep}"]
    folds = [[(np.setdiff1d(np.arange(len(y)), Z[f"te_{ep}_s{si}_f{fi}"]),
               Z[f"te_{ep}_s{si}_f{fi}"]) for fi in range(L.N_SPLITS)]
             for si in range(len(L.SEEDS))]
    for arm_name, cols in (("L2단독", BASE), ("L2단독_감작제외", DROP)):
        Xi = mat(cols)[m]
        assert len(Xi) == len(y)
        acc, 보정, taus = [], [], []
        for si in range(len(L.SEEDS)):
            p, tpf = oof_probs(Xi, y, g, folds[si], si)
            pred = assemble_pred(p, tpf, len(y))
            taus.extend(t for _, t in tpf)
            row = {"roc_auc": roc_auc_score(y, p),
                   "pr_auc": average_precision_score(y, p), **mcc_ba(y, pred)}
            acc.append(row)
            per = []
            for t in DOC_TIERS:
                k = SUB[f"{ep}__{t}"]
                assert len(k) == len(y)
                per.append({"n": int(k.sum()), "p": float(y[k].mean()),
                            "auc": roc_auc_score(y[k], p[k])})
            보정.append(auc_보정(per))
        rec = {"endpoint": ep, "피처셋": arm_name, "열수": len(cols),
               "관할": CANON_JUR_V8, "n": int(len(y)),
               "유병률": round(float(y.mean()), 4)}
        for k in acc[0]:
            rec[k] = round(float(np.mean([d[k] for d in acc])), 4)
            rec[k + "_sd"] = round(float(np.std([d[k] for d in acc], ddof=1)), 4)
        rec["roc_auc_보정"] = round(float(np.mean(보정)), 4)
        rec["roc_auc_보정_sd"] = round(float(np.std(보정, ddof=1)), 4)
        rec["tau_평균"] = round(float(np.mean(taus)), 4)
        rec["tau_sd"] = round(float(np.std(taus, ddof=1)), 4)
        RES.append(rec)
        log(f"  {ep:4} {arm_name:14} {len(cols):3}열 "
            f"pooled AUC={rec['roc_auc']:.4f}±{rec['roc_auc_sd']:.4f} "
            f"보정 AUC={rec['roc_auc_보정']:.4f} MCC={rec['MCC']:.4f} "
            f"BA={rec['BA']:.4f} τ={rec['tau_평균']:.3f}")

R = pd.DataFrame(RES)
R.to_csv(OUT / "감작제외_대조.csv", index=False, encoding="utf-8-sig")

log("--- 판정 ---")
결론 = []
for ep in EPS:
    a = next(r for r in RES if r["endpoint"] == ep and r["피처셋"] == "L2단독")
    b = next(r for r in RES if r["endpoint"] == ep and r["피처셋"] == "L2단독_감작제외")
    for k in ("roc_auc", "roc_auc_보정", "MCC", "BA"):
        d = b[k] - a[k]
        # 시드 SD 2배를 잡음 범위로 본다. 그 안이면 '기여 없음' 으로 읽는다.
        noise = 2 * max(a.get(k + "_sd", 0) or 0, b.get(k + "_sd", 0) or 0)
        결론.append({"endpoint": ep, "지표": k, "유지": a[k], "제외": b[k],
                    "차이": round(d, 4), "잡음범위_시드SD2배": round(noise, 4),
                    "판정": "유의한 손실" if d < -noise else
                            "유의한 개선" if d > noise else "잡음 범위 — 기여 없음"})
        log(f"  {ep:4} {k:12} 유지={a[k]:.4f} 제외={b[k]:.4f} "
            f"차이={d:+.4f} (±{noise:.4f}) → {결론[-1]['판정']}")
pd.DataFrame(결론).to_csv(OUT / "감작제외_판정.csv", index=False,
                         encoding="utf-8-sig")
log("주의 — 이 대조는 감작 열의 기여만 잰다. 제외 결정의 근거는 수치가 아니라 "
    "'감작을 학습에 쓰지 않는다'는 원칙이고, 이 표는 그 결정의 성능 비용을 밝히는 "
    "용도다. 차이가 잡음 범위면 비용 없이 원칙을 지킬 수 있다는 뜻이다.")
log(f"완료 → {OUT.relative_to(L.ROOT)}/감작제외_대조.csv · 감작제외_판정.csv")
