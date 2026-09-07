#!/usr/bin/env python3
"""축 C-2 — 라벨 출처 신뢰도·충돌 해소 재현성 감사.

`lib_tox11.resolve_y_conflict`가 기존 v3 산출물의 y_conflict_rule/y_preferred
99건을 100% 재현하는지 회귀 검증하고, 전체 1675건에 대해 동일 로직을 재적용해
`04_모델산출물/v4/y_conflict_audit.csv`로 산출한다. 라벨 값 자체는 바꾸지 않는다
(v3 보존, 신규 산출물만 v4에 기록).
"""
import sys

import pandas as pd

sys.path.insert(0, "/Users/hanseoyun/Desktop/260830/01_파이프라인")
from lib_tox11 import resolve_y_conflict

SRC = "/Users/hanseoyun/Desktop/260830/04_모델산출물/input_dataset_v4.xlsx"  # 2026-08-29부터 v4 기준
OUT = "/Users/hanseoyun/Desktop/260830/04_모델산출물/v4/y_conflict_audit.csv"

df = pd.read_excel(SRC, sheet_name="formulation")

pred = df.apply(
    lambda r: resolve_y_conflict(
        r.get("ice_ingredient_cas"), r.get("tox_comp_cas"),
        r.get("product_name"), r.get("tox_doc_product"),
    ), axis=1, result_type="expand",
)
pred.columns = ["pred_rule", "pred_preferred", "pred_evidence"]
df = pd.concat([df, pred], axis=1)

# --- 회귀 검증: 기존 99건(y_conflict_rule 존재) 대비 재현율 ---
existing = df[df["n_y_conflict"] > 0]
rule_acc = (existing["pred_rule"] == existing["y_conflict_rule"]).mean()
pref_acc = (existing["pred_preferred"].fillna("NA") == existing["y_preferred"].fillna("NA")).mean()
print(f"[회귀검증] y_conflict_rule 재현율: {rule_acc:.4f} ({(existing['pred_rule']==existing['y_conflict_rule']).sum()}/{len(existing)})")
print(f"[회귀검증] y_preferred 재현율:    {pref_acc:.4f}")
mism = existing[existing["pred_rule"] != existing["y_conflict_rule"]]
if len(mism):
    print("\n불일치 행:")
    print(mism[["Formulation_ID", "pred_rule", "y_conflict_rule"]].to_string())

assert rule_acc == 1.0 and pref_acc == 1.0, "회귀검증 실패 — resolve_y_conflict 로직을 lib_tox11.py 원본과 다시 대조할 것"

# --- 전체 1675건에 재적용: 기존에 n_y_conflict=0이었던 행 중 새로 잡히는 충돌 여부 확인 ---
newly_flagged = df[(df["n_y_conflict"].fillna(0) == 0) & (df["pred_rule"] != "no_composition_data")]
print(f"\n[신규 탐지] 기존 n_y_conflict=0인데 조성 비교상 mismatch/partial로 새로 잡히는 행: {len(newly_flagged)}건")
print("  (기존 미검토 라벨 소스 간 잠재 충돌 후보 — 값 변경 없이 참고용으로만 기록)")

out_cols = ["Formulation_ID", "product_name", "n_y_conflict", "y_conflict_rule", "y_preferred",
            "pred_rule", "pred_preferred", "pred_evidence"]
df[out_cols].to_csv(OUT, index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT}  ({len(df)}행)")
