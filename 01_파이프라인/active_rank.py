#!/usr/bin/env python3
"""축 A — 능동학습 기반 수집 우선순위 재랭킹.

지시된 수집 범위(이서윤/김보경 pH 554건, 정채윤 GHS 331 CAS, 최소빈 S11판독 797건)는
바꾸지 않는다. 각 리스트 *내부*의 처리 순서에, 현재 partial 모델의 committee 불확실도
(query-by-committee) + 구조 다양성(diversity sampling) 기반 우선순위 점수만 추가한다.

산출: 04_모델산출물/v4/active_rank_priority.csv
(2026-08-29부터 v4를 기준 산출물로 전환 — 6/30 성분감사 병합분 포함)
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

OUT = "/Users/hanseoyun/Desktop/260830/04_모델산출물/v4"
ASSIGN = "/Users/hanseoyun/Desktop/260830/03_입력데이터/dataset_배정_20260824.xlsx"
SEEDS = [0, 1, 2, 3, 4]

X = pd.read_parquet(f"{OUT}/X_formulation.parquet")
Y = pd.read_parquet(f"{OUT}/y_formulation.parquet")
D = X.merge(Y, on="Formulation_ID", how="inner", suffixes=("", "__y"))
FEATS = [c for c in X.columns if c != "Formulation_ID"]
Xall = np.nan_to_num(D[FEATS].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)

fp = np.load(f"{OUT}/fp_form_pooled.npz", allow_pickle=True)
fp_fid = fp["fid"]
fp_vec = fp["morgan"][:, :2048]  # max-pooled 절반만 사용 (다양성 거리용)
fid_to_fprow = {fid: i for i, fid in enumerate(fp_fid)}


def committee_uncertainty(ycol):
    """query-by-committee: 시드 5개 RF의 양성클래스 확률 std = 불확실도."""
    m = D[ycol].notna().to_numpy()
    y = D.loc[m, ycol].astype(int).to_numpy()
    Xtr = Xall[m]
    preds = []
    for s in SEEDS:
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                      min_samples_leaf=2, n_jobs=-1, random_state=s)
        clf.fit(Xtr, y)
        proba = clf.predict_proba(Xall)
        pos_col = list(clf.classes_).index(1) if 1 in clf.classes_ else 0
        preds.append(proba[:, pos_col])
    P = np.vstack(preds)
    return P.std(axis=0), m


unc_eye, has_eye = committee_uncertainty("y_eye_bin")
unc_skin, has_skin = committee_uncertainty("y_skin_bin")
unc_sens, has_sens = committee_uncertainty("y_sens_bin")

D["unc_eye"], D["unc_skin"], D["unc_sens"] = unc_eye, unc_skin, unc_sens


def minmax(s):
    rng = s.max() - s.min()
    return (s - s.min()) / rng if rng > 0 else s * 0.0


D["unc_eye_n"] = minmax(D["unc_eye"])
D["unc_skin_n"] = minmax(D["unc_skin"])
D["unc_sens_n"] = minmax(D["unc_sens"])

labeled_mask = has_eye | has_skin | has_sens
labeled_fids = set(D.loc[labeled_mask, "Formulation_ID"])
pool_idx = [fid_to_fprow[f] for f in labeled_fids if f in fid_to_fprow]
pool_vecs = fp_vec[pool_idx]
pool_norms = np.linalg.norm(pool_vecs, axis=1) + 1e-9


def diversity_for_fid(fid):
    if fid not in fid_to_fprow or pool_vecs.shape[0] == 0:
        return 0.5  # 구조 미보유 -> 중립값
    v = fp_vec[fid_to_fprow[fid]]
    vn = np.linalg.norm(v)
    if vn == 0:
        return 0.5
    sims = (pool_vecs @ v) / (pool_norms * vn)
    return float(1.0 - sims.max())


D["diversity"] = [diversity_for_fid(f) for f in D["Formulation_ID"]]
D["diversity_n"] = minmax(D["diversity"])

# sens 가중치 2배: 05_제안 §1 근거 — sens 표본(n=595) 최소, sens_trackB(n=211) 성능 최약
D["priority_score"] = (
    0.6 * (D["unc_eye_n"] + D["unc_skin_n"] + 2 * D["unc_sens_n"]) / 4
    + 0.4 * D["diversity_n"]
)

fid2 = {
    "score": dict(zip(D["Formulation_ID"], D["priority_score"])),
    "eye": dict(zip(D["Formulation_ID"], D["unc_eye_n"])),
    "skin": dict(zip(D["Formulation_ID"], D["unc_skin_n"])),
    "sens": dict(zip(D["Formulation_ID"], D["unc_sens_n"])),
    "div": dict(zip(D["Formulation_ID"], D["diversity_n"])),
}

xl = pd.ExcelFile(ASSIGN)
rows = []

# 이서윤/김보경 — 특성 시트, pH작업_담당 (554건, 범위 불변)
phy = xl.parse("특성")
for person in ("이서윤", "김보경"):
    sub = phy[phy["pH작업_담당"] == person]
    for fid in sub["Formulation_ID"]:
        rows.append({
            "assigned_to": person, "task_type": "pH수집", "endpoint": "전체",
            "Formulation_ID": fid, "priority_score": fid2["score"].get(fid, np.nan),
            "unc_eye": fid2["eye"].get(fid), "unc_skin": fid2["skin"].get(fid),
            "unc_sens": fid2["sens"].get(fid), "diversity": fid2["div"].get(fid),
        })

# 정채윤 — 성분 시트, GHS조사_담당 (331 CAS, 범위 불변). CAS 단위 -> 해당 CAS 포함 제형들의
# 최대 불확실도로 집계 (그 CAS 정보 확보가 어떤 제형에 가장 큰 영향을 주는지 기준)
ing = xl.parse("성분")
sub = ing[ing["GHS조사_담당"] == "정채윤"]
cas_to_fids = ing.groupby("cas")["Formulation_ID"].apply(list).to_dict()
for _, r in sub.iterrows():
    cas = r["cas"]
    fids = cas_to_fids.get(cas, [r["Formulation_ID"]])
    scores = [fid2["score"][f] for f in fids if f in fid2["score"]]
    agg = max(scores) if scores else np.nan
    rows.append({
        "assigned_to": "정채윤", "task_type": "GHS조사", "endpoint": "전체",
        "Formulation_ID": r["Formulation_ID"], "cas": cas, "priority_score": agg,
        "unc_eye": None, "unc_skin": None, "unc_sens": None, "diversity": None,
    })

# 최소빈 — 독성코드 시트, S11_담당. 원문이 채워진(=실제 판독 대상) 항목만, 엔드포인트별로 분리
# (총 797건 = 눈268+피부269+감작성260, 범위 불변)
tox = xl.parse("독성코드")
sub = tox[tox["S11_담당"] == "최소빈"]
for _, r in sub.iterrows():
    fid = r["Formulation_ID"]
    for ep, col in (("eye", "S11_원문_눈"), ("skin", "S11_원문_피부"), ("sens", "S11_원문_감작")):
        if pd.notna(r[col]):
            unc = fid2[ep].get(fid)
            div = fid2["div"].get(fid)
            score = (0.6 * unc + 0.4 * div) if (unc is not None and div is not None) else fid2["score"].get(fid, np.nan)
            rows.append({
                "assigned_to": "최소빈", "task_type": "S11판독", "endpoint": ep,
                "Formulation_ID": fid, "priority_score": score,
                "unc_eye": fid2["eye"].get(fid), "unc_skin": fid2["skin"].get(fid),
                "unc_sens": fid2["sens"].get(fid), "diversity": div,
            })

out = pd.DataFrame(rows)
out["priority_rank"] = out.groupby("assigned_to")["priority_score"].rank(ascending=False, method="first")
out = out.sort_values(["assigned_to", "priority_rank"])
out_path = f"{OUT}/active_rank_priority.csv"
out.to_csv(out_path, index=False, encoding="utf-8-sig")

print(f"저장: {out_path}  {out.shape}")
for person in out["assigned_to"].unique():
    n = (out["assigned_to"] == person).sum()
    print(f"  {person}: {n}건")

# AC1 검증: 최소빈 리스트 우선순위 상위 20% 중 sens(감작성) 비율이
# 전체 비중(260/797≈32.6%)보다 높은지 확인
cs = out[out.assigned_to == "최소빈"].sort_values("priority_score", ascending=False)
top20 = cs.head(max(1, int(len(cs) * 0.2)))
print("\n[AC1 검증] 최소빈 리스트 전체 endpoint 분포:")
print(cs["endpoint"].value_counts(normalize=True).round(4))
print("[AC1 검증] 최소빈 리스트 top20% endpoint 분포:")
print(top20["endpoint"].value_counts(normalize=True).round(4))
