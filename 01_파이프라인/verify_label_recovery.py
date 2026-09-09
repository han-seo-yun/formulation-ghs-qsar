#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 회귀 검증 하네스 — v1 SDS 음성 라벨 복구 (build_input_v5.py) 검증.

읽기 전용. v4(기준선) 산출물은 절대 수정하지 않는다.

검증 항목
  C1  status=='negative' 행의 카테고리 보유수: 복구 전 0건 → 복구 후 몇 건
  C2  sds_v1 양성률 1.000 → 실측 (진단 예측치 eye .790 / skin .701 / sens .603 대조)
  C3  기존 sds_ghs_* 카테고리 값이 덮어써지지 않았음 (행 단위, 덮어썼으면 FAIL)
  C3b 최종 y 값 변화 분해 (신규추가 / 우선순위에 의한 정당한 승격 / 미설명)
  C4  y 라벨 총수 변화 (v4 기준 eye 1105 / skin 992 / sens 595)
  C5  label_source / y_*_src 및 파생이 X 에 없음
  C6  status=='negative' 인데 다른 출처가 양성 → 사람 재검 목록 (진단 32/10/5)

사용:  python3 01_파이프라인/verify_label_recovery.py
출력:  04_모델산출물/v4_fixed/label_recovery_verification.json  (+ 콘솔 PASS/FAIL)
"""
import json
import math
import os
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

ROOT = str(Path(__file__).resolve().parent.parent)
BASE = ROOT
SRC_V1 = f"{BASE}/03_입력데이터/input_dataset.xlsx"
V4 = f"{BASE}/04_모델산출물/v4"                 # 기준선 (읽기 전용!)
V5 = f"{BASE}/04_모델산출물/v4_fixed"           # 검증 대상
EPS = ("eye", "skin", "sens")

# 진단 리포트(04_모델산출물/v4/label_source_bias_report.json)의 예측/기준치
EXPECT = {
    "status_negative_rows": {"eye": 146, "skin": 169, "sens": 211},
    "recoverable_when_y_missing": {"eye": 48, "skin": 67, "sens": 121},
    "v4_y_total": {"eye": 1105, "skin": 992, "sens": 595},
    "v4_sds_v1_pos_rate": {"eye": 1.0, "skin": 1.0, "sens": 1.0},
    "predicted_sds_v1_pos_rate": {"eye": 0.790, "skin": 0.701, "sens": 0.603},
    "conflict_neg_vs_other_positive": {"eye": 32, "skin": 10, "sens": 5},
}
SEV = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
       "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1, "2A": 2, "2B": 1, "NC": 0},
       "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}

RESULTS = []


def check(cid, name, ok, detail):
    RESULTS.append({"id": cid, "check": name,
                    "status": "PASS" if ok else ("WARN" if ok is None else "FAIL"),
                    "detail": detail})
    tag = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
    print(f"  [{tag}] {cid} {name}")
    for k, v in (detail.items() if isinstance(detail, dict) else []):
        print(f"         {k}: {v}")


def nrm_cat(v):
    """build_input_v5.nrm_cat 과 동일 (독립 재구현 — 빌더 버그를 함께 옮기지 않기 위함)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    if re.search(r"not classified|^nc$|not_classified|미분류|no data|unknown", s, re.I):
        return "NC" if re.search(r"not classified|^nc$|not_classified|미분류", s, re.I) else None
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    m = re.match(r"\s*(?:cat(?:egory)?\.?\s*)?(1A|1B|1C|2A|2B|1|2|3|4)\b", s, re.I)
    return m.group(1).upper() if m else s.upper()[:24]


print("=" * 78)
print("P0 회귀 검증: v1 SDS 음성 라벨 복구")
print("=" * 78)

# ---------------------------------------------------------------- 적재
for p in (SRC_V1, f"{V4}/y_formulation.parquet", f"{V5}/y_formulation.parquet"):
    if not os.path.exists(p):
        sys.exit(f"필수 파일 없음: {p}")

F1 = pd.read_excel(SRC_V1, sheet_name="formulation")
Y4 = pd.read_parquet(f"{V4}/y_formulation.parquet")
Y5 = pd.read_parquet(f"{V5}/y_formulation.parquet")
X5 = pd.read_parquet(f"{V5}/X_formulation.parquet")
print(f"v1 formulation {F1.shape} · y_v4 {Y4.shape} · y_v5 {Y5.shape} · X_v5 {X5.shape}")

ST = {ep: F1[f"sds_grade_status_{ep}"].astype("string").str.strip().str.lower()
      for ep in EPS}
RAW = {ep: F1[f"sds_ghs_{ep}"] for ep in EPS}
FID = F1["Formulation_ID"]

# ---------------------------------------------------------------- C0 status 도메인
print("\n[C0] sds_grade_status_* 도메인")
DOMAIN_OK = {"negative", "not_stated", "verdict_only", "no_sds", "ok"}
dom_detail = {}
dom_ok = True
for ep in EPS:
    vc = {k: int(v) for k, v in ST[ep].value_counts(dropna=False).items()}
    unexpected = sorted(set(str(k) for k in vc) - DOMAIN_OK - {"<NA>", "nan"})
    dom_detail[ep] = {"counts": vc, "unexpected": unexpected}
    dom_ok &= not unexpected
check("C0", "status 도메인이 예상 5값 이내", dom_ok, dom_detail)

# ---------------------------------------------------------------- C1 복구 건수
print("\n[C1] status=='negative' 행의 카테고리 보유수 (전 → 후)")
c1 = {}
c1_ok = True
for ep in EPS:
    neg = ST[ep].eq("negative").fillna(False)
    before = int(RAW[ep][neg].map(lambda v: nrm_cat(v) is not None).sum())
    # 복구 후: v5 formulation 시트의 sds_ghs_*_eff / 복구 플래그로 확인
    y5 = Y5.set_index("Formulation_ID")
    flag_col = f"sds_v1_neg_recovered_{ep}"
    recovered = int(y5[flag_col].fillna(False).astype(bool).sum()) \
        if flag_col in y5.columns else None
    c1[ep] = {"n_status_negative": int(neg.sum()),
              "expected_n_status_negative": EXPECT["status_negative_rows"][ep],
              "n_with_category_before": before,
              "n_recovered_to_NC_after": recovered}
    c1_ok &= (before == 0) and (int(neg.sum()) == EXPECT["status_negative_rows"][ep]) \
        and (recovered == int(neg.sum()))
check("C1", "복구 전 0건 · 복구 후 전량 NC 부여", c1_ok, c1)

# ---------------------------------------------------------------- C2 sds_v1 양성률
print("\n[C2] sds_v1 양성률 변화")
c2 = {}
for ep in EPS:
    r = {}
    for tag, Y in (("v4", Y4), ("v5", Y5)):
        m = Y[f"y_{ep}_src"] == "sds_v1"
        b = pd.to_numeric(Y.loc[m, f"y_{ep}_bin"], errors="coerce")
        r[tag] = {"n_rows": int(m.sum()), "n_pos": int((b == 1).sum()),
                  "pos_rate": round(float(b.mean()), 4) if len(b) else None}
    r["predicted_by_diagnosis"] = EXPECT["predicted_sds_v1_pos_rate"][ep]
    r["delta_vs_prediction"] = (round(r["v5"]["pos_rate"]
                                      - EXPECT["predicted_sds_v1_pos_rate"][ep], 4)
                                if r["v5"]["pos_rate"] is not None else None)
    c2[ep] = r
    print(f"    {ep:5s} v4 {r['v4']['pos_rate']} ({r['v4']['n_pos']}/{r['v4']['n_rows']})"
          f"  →  v5 {r['v5']['pos_rate']} ({r['v5']['n_pos']}/{r['v5']['n_rows']})"
          f"   예측 {r['predicted_by_diagnosis']}")
# 판정: v4 가 1.000 이었고 v5 에서 유의하게 떨어졌으면 PASS.
# 예측치와의 일치는 요구하지 않는다(예측은 근사식이었다) — 차이는 리포트한다.
c2_ok = all(c2[ep]["v4"]["pos_rate"] == 1.0 and c2[ep]["v5"]["pos_rate"] < 0.95
            for ep in EPS)
check("C2", "sds_v1 양성률 1.000 → 1 미만으로 하락", c2_ok, c2)
pred_match = {ep: abs(c2[ep]["delta_vs_prediction"]) <= 0.02 for ep in EPS}
check("C2b", "진단 예측 양성률과 ±0.02 내 일치",
      all(pred_match.values()) or None,
      {"match": pred_match,
       "note": "불일치 원인: 진단의 반사실 계산은 '최종 y 가 결측인 행'만 분모에 더했으나, "
               "실제 복구는 우선순위상 sds_v1 이 하위출처(sds_2nd/phase1_sds/sec11)를 "
               "이기는 행까지 sds_v1 집단으로 끌어와 분모가 더 커진다."})

# ---------------------------------------------------------------- C3 덮어쓰기 없음
print("\n[C3] 기존 값 보존 (행 단위)")
# C3-a: 원본 sds_ghs_* 카테고리 자체가 바뀐 행 = 0 이어야 한다.
c3a = {}
c3a_ok = True
y5i = Y5.set_index("Formulation_ID")
for ep in EPS:
    has_cat = RAW[ep].map(lambda v: nrm_cat(v) is not None)
    flag = y5i[f"sds_v1_neg_recovered_{ep}"].reindex(FID).fillna(False).astype(bool).to_numpy()
    overwritten = int((has_cat.to_numpy() & flag).sum())
    c3a[ep] = {"n_existing_category": int(has_cat.sum()),
               "n_existing_category_touched_by_recovery": overwritten}
    c3a_ok &= overwritten == 0
check("C3a", "sds_ghs_* 기존 카테고리를 복구가 건드리지 않음", c3a_ok, c3a)

# C3-b: 최종 y 변화 분해.
#   added         v4 결측 → v5 값 있음  (기대되는 순증)
#   promoted      v4 에 값 있었으나 v5 에서 sds_v1(NC)이 하위출처를 우선순위로 이김
#                 → 우선순위 로직(불변)의 정상 결과. '카테고리 덮어쓰기'가 아니다.
#   unexplained   위 둘로 설명되지 않는 변화 → FAIL 사유
c3b = {}
c3b_ok = True
M = Y4[["Formulation_ID"]].merge(Y5[["Formulation_ID"]], on="Formulation_ID", how="outer")
J = (Y4.set_index("Formulation_ID"), Y5.set_index("Formulation_ID"))
for ep in EPS:
    a, b = J[0][f"y_{ep}"].reindex(M["Formulation_ID"]), \
        J[1][f"y_{ep}"].reindex(M["Formulation_ID"])
    s4 = J[0][f"y_{ep}_src"].reindex(M["Formulation_ID"])
    s5 = J[1][f"y_{ep}_src"].reindex(M["Formulation_ID"])
    rec = J[1][f"sds_v1_neg_recovered_{ep}"].reindex(M["Formulation_ID"]) \
        .fillna(False).astype(bool)
    na4, na5 = a.isna(), b.isna()
    added = (na4 & ~na5)
    lost = (~na4 & na5)
    changed = (~na4 & ~na5 & (a.astype("string") != b.astype("string")))
    promoted = changed & rec & (s5 == "sds_v1") & (s4 != "sds_v1")
    unexplained = (changed & ~promoted) | lost
    c3b[ep] = {
        "v4_n": int((~na4).sum()), "v5_n": int((~na5).sum()),
        "added": int(added.sum()), "lost": int(lost.sum()),
        "changed": int(changed.sum()),
        "changed_promoted_by_priority": int(promoted.sum()),
        "changed_unexplained": int(unexplained.sum()),
        "promoted_src_v4_breakdown": {str(k): int(v) for k, v
                                      in s4[promoted].value_counts().items()},
    }
    c3b_ok &= int(unexplained.sum()) == 0
check("C3b", "설명되지 않는 라벨 변화 0건 (변화는 신규추가 또는 우선순위 승격만)",
      c3b_ok, c3b)

# ---------------------------------------------------------------- C4 y 총수
print("\n[C4] y 라벨 총수")
c4 = {}
c4_ok = True
for ep in EPS:
    n4 = int(Y4[f"y_{ep}"].notna().sum())
    n5 = int(Y5[f"y_{ep}"].notna().sum())
    exp_add = EXPECT["recoverable_when_y_missing"][ep]
    c4[ep] = {"v4": n4, "v4_expected": EXPECT["v4_y_total"][ep], "v5": n5,
              "delta": n5 - n4, "expected_delta": exp_add,
              "delta_matches": (n5 - n4) == exp_add}
    c4_ok &= (n4 == EXPECT["v4_y_total"][ep]) and ((n5 - n4) == exp_add)
    print(f"    {ep:5s} {n4} → {n5}  (Δ{n5 - n4:+d}, 기대 +{exp_add})")
check("C4", "y 총수 증가분 = 진단의 복구가능 건수", c4_ok, c4)

# ---------------------------------------------------------------- C5 X 누출
print("\n[C5] X 에 라벨출처 컬럼 없음")
bad = []
for c in X5.columns:
    if c in ("Formulation_ID", "ing_idx"):
        continue
    base = re.sub(r"_isna$", "", c)
    if (base == "label_source" or base.startswith("label_source")
            or re.match(r"^y_(eye|skin|sens)(_|$)", base)
            or base.endswith("_src") or base.endswith("_src_detail")
            or "_from_neg_recovery" in base
            or base.startswith("sds_grade_status_")
            or base.startswith("sds_v1_neg_recovered_")
            or re.match(r"^sds_ghs_.*_eff$", base)
            or base in ("sds_ghs_eye", "sds_ghs_skin", "sds_ghs_sens")):
        bad.append(c)
# 매니페스트 교차확인
MANP = f"{V5}/feature_manifest.csv"
man_bad = []
if os.path.exists(MANP):
    MAN = pd.read_csv(MANP)
    man_bad = sorted(MAN[(MAN["role"].isin(["label_source", "provenance"]))
                         & (MAN["in_X"])]["column"].astype(str))
check("C5", "label_source / y_*_src 및 파생 원-핫이 X 에 없음",
      not bad and not man_bad,
      {"x_columns_flagged": bad, "manifest_label_source_in_X": man_bad,
       "n_X_cols": int(X5.shape[1])})

# ---------------------------------------------------------------- C6 충돌 재검
print("\n[C6] status=='negative' vs 다른 출처 양성 충돌 (사람 재검 대상)")
CFP = f"{V5}/label_conflict_manual_review.csv"
c6 = {"csv": CFP, "exists": os.path.exists(CFP)}
if c6["exists"]:
    CF = pd.read_csv(CFP)
    got = {ep: int((CF["endpoint"] == ep).sum()) for ep in EPS}
    c6["counts_v5"] = got
    c6["counts_expected_by_diagnosis"] = EXPECT["conflict_neg_vs_other_positive"]
    c6["match"] = {ep: got[ep] == EXPECT["conflict_neg_vs_other_positive"][ep]
                   for ep in EPS}
    c6["final_y_still_positive"] = {
        ep: int(((CF["endpoint"] == ep) & (CF["y_bin"] == 1)).sum()) for ep in EPS}
    # 정의 차이 해명: 진단(audit_label_source_bias.py 의 final_y_pos_among_labeled)은
    # "status=negative 인데 v4 의 **최종** y 가 양성" 을 셌다. v5 CSV 는
    # "status=negative 인데 **어떤** 다른 출처든 양성을 주장" 을 센다 → 상위집합.
    # 따라서 v5 건수 >= 진단 건수 여야 하며, 같지 않아도 결함이 아니다.
    c6["is_superset_of_diagnosis"] = {
        ep: got[ep] >= EXPECT["conflict_neg_vs_other_positive"][ep] for ep in EPS}
    c6["definition_note"] = (
        "진단치는 'v4 최종 y 가 양성'(승자 기준), v5 CSV 는 '다른 출처 중 하나라도 "
        "양성 주장'(주장 기준). v5 는 진단의 상위집합이므로 건수가 같거나 크다.")
    c6["note"] = ("자동 수정하지 않음. 최종 y 는 우선순위상 sds_v1(NC)이 이기지만 "
                  "근거문서 모순은 남아 있으므로 사람 재검 필요.")
    for ep in EPS:
        print(f"    {ep:5s} 실측 {got[ep]} / 진단 "
              f"{EXPECT['conflict_neg_vs_other_positive'][ep]}"
              f" · 최종 y 여전히 양성 {c6['final_y_still_positive'][ep]}")
if not c6["exists"]:
    _c6ok = False
elif not all(c6["is_superset_of_diagnosis"].values()):
    _c6ok = False     # 진단치보다 적으면 충돌을 놓친 것 → FAIL
elif all(c6["match"].values()):
    _c6ok = True
else:
    _c6ok = None      # 정의 차이만큼 초과 → WARN (건수 차이를 리포트)
check("C6", "충돌 목록 산출 + 진단치와 대조(상위집합)", _c6ok, c6)

# ---------------------------------------------------------------- C7 feature_role
print("\n[C7] feature_role 태깅")
FRP = f"{V5}/feature_role_manifest.csv"
c7 = {"csv": FRP, "exists": os.path.exists(FRP)}
if c7["exists"]:
    FR = pd.read_csv(FRP)
    c7["counts"] = {k: int(v) for k, v in FR["feature_role"].value_counts().items()}
    c7["counts_formulation"] = {
        k: int(v) for k, v
        in FR[FR["sheet"] == "formulation"]["feature_role"].value_counts().items()}
    c7["n_exclude_by_default"] = int(FR["exclude_by_default"].sum())
    c7["ambiguous_non_isna_formulation"] = sorted(
        FR[(FR["sheet"] == "formulation") & (FR["feature_role"] == "ambiguous")
           & (~FR["column"].str.endswith("_isna"))]["column"])
    # 감사에서 지목된 누출 상위 피처가 bookkeeping/ambiguous 로 잡혔는지
    leaky = ["n_tox_endpoints", "has_toxicity", "tox_n_composition_rows",
             "sds_epa_eye_ambiguous", "sds_epa_skin_ambiguous", "trainable",
             "has_y", "has_ntp_label", "n_y_conflict", "n_ing_missing",
             "n_ing_total_known"]
    got = FR.set_index("column")["feature_role"].to_dict()
    c7["leaky_top_features_role"] = {k: got.get(k, "(X에 없음)") for k in leaky}
    c7["leaky_all_excluded"] = all(
        got.get(k) in ("bookkeeping", "ambiguous") for k in leaky if k in got)
check("C7", "감사 지목 누출 피처가 전부 bookkeeping/ambiguous 로 태깅",
      c7.get("leaky_all_excluded", False), c7)

# ---------------------------------------------------------------- 요약
print("\n" + "=" * 78)
npass = sum(r["status"] == "PASS" for r in RESULTS)
nfail = sum(r["status"] == "FAIL" for r in RESULTS)
nwarn = sum(r["status"] == "WARN" for r in RESULTS)
print(f"PASS {npass} · FAIL {nfail} · WARN {nwarn}")
for r in RESULTS:
    if r["status"] != "PASS":
        print(f"  {r['status']}: {r['id']} {r['check']}")
OUT = {"summary": {"pass": npass, "fail": nfail, "warn": nwarn},
       "checks": RESULTS, "expectations_used": EXPECT}
json.dump(OUT, open(f"{V5}/label_recovery_verification.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print(f"→ {V5}/label_recovery_verification.json")
sys.exit(1 if nfail else 0)
