#!/usr/bin/env python3
"""v4 → v5 6/30 감사 병합 변경 영향 정량화.

산출
  04_모델산출물/v4_fixed/v4_to_v5_row_diff.csv       성분값이 바뀐 행 전체
  04_모델산출물/v4_fixed/v5_residual_unrecovered.csv 아직 복구 못한 워크북 토큰 + 사유
  04_모델산출물/v4_fixed/v4_v5_impact_summary.csv    요약표
"""
import os
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = str(Path(__file__).resolve().parent.parent)
BASE = ROOT
OUT = f"{BASE}/04_모델산출물/v4_fixed"
sys.path.insert(0, f"{BASE}/01_파이프라인")
from audit_june_merge_defect import (is_nonchemical_token, nrm_name,   # noqa: E402
                                     split_pipe)

V4 = pd.read_excel(f"{BASE}/04_모델산출물/input_dataset_v4.xlsx", sheet_name="ingredient")
V5 = pd.read_excel(f"{BASE}/04_모델산출물/input_dataset_v5.xlsx", sheet_name="ingredient")
EV = pd.read_csv(f"{OUT}/june_merge_defect_evidence.csv")

K = ["Formulation_ID", "ing_idx"]
m = V4[K + ["ing_name_best", "ing_cas_best", "smiles", "desc_ok",
            "june_audit_recovered"]].merge(
    V5[K + ["ing_cas_best", "smiles", "desc_ok", "june_audit_recovered",
            "june_merge_basis"]], on=K, suffixes=("_v4", "_v5"))
assert len(m) == len(V4) == len(V5), "행수 불일치"


def sv(x):
    return x.astype(str).where(x.notna(), "")


cas_ch = sv(m["ing_cas_best_v4"]) != sv(m["ing_cas_best_v5"])
smi_ch = sv(m["smiles_v4"]) != sv(m["smiles_v5"])
r4, r5 = m["june_audit_recovered_v4"].fillna(False), m["june_audit_recovered_v5"].fillna(False)

evk = {(r.Formulation_ID, r.name_norm): r.verdict for r in EV.itertuples(index=False)}
m["v4_defect_verdict"] = [evk.get((f, nrm_name(n)), "not_in_evidence")
                          for f, n in zip(m["Formulation_ID"], m["ing_name_best"])]
BAD = {"CAS_WRONG", "CAS_WRONG_SHIFT", "NONCHEM_TOKEN_GOT_STRUCTURE", "SMILES_WRONG"}

rows = [
    ("대상 제형 (RECOVER_CAS_HIGH_CONFIDENCE)", 195),
    ("세 파이프리스트 길이 불일치 제형", 163),
    ("v4 june_audit_recovered 행", int(r4.sum())),
    ("v5 june_audit_recovered 행", int(r5.sum())),
    ("v4·v5 모두 병합된 행", int((r4 & r5).sum())),
    ("v4 병합 → v5 에서 철회된 행", int((r4 & ~r5).sum())),
    ("v5 신규 안전복구 행", int((~r4 & r5).sum())),
    ("v4 오염판정(증거CSV) 행 중 v5 에서 철회", int((r4 & ~r5 & m["v4_defect_verdict"].isin(BAD)).sum())),
    ("v4 오염판정 행 중 v5 에서 값 정정", int((r4 & r5 & m["v4_defect_verdict"].isin(BAD)
                                    & (cas_ch | smi_ch)).sum())),
    ("CAS 값이 바뀐 행", int(cas_ch.sum())),
    ("  · CAS 철회 (v4有→v5無)", int((m["ing_cas_best_v4"].notna() & m["ing_cas_best_v5"].isna()).sum())),
    ("  · CAS 정정 (양쪽 有, 값 다름)", int((m["ing_cas_best_v4"].notna() & m["ing_cas_best_v5"].notna() & cas_ch).sum())),
    ("  · CAS 신규 (v4無→v5有)", int((m["ing_cas_best_v4"].isna() & m["ing_cas_best_v5"].notna()).sum())),
    ("SMILES 값이 바뀐 행", int(smi_ch.sum())),
    ("  · SMILES 철회 (v4有→v5無)", int((m["smiles_v4"].notna() & m["smiles_v5"].isna()).sum())),
    ("  · SMILES 정정", int((m["smiles_v4"].notna() & m["smiles_v5"].notna() & smi_ch).sum())),
    ("  · SMILES 신규", int((m["smiles_v4"].isna() & m["smiles_v5"].notna()).sum())),
    ("구조 보유 행 desc_ok (v4)", int(m["desc_ok_v4"].sum())),
    ("구조 보유 행 desc_ok (v5)", int(m["desc_ok_v5"].sum())),
    ("영향받은 제형 수 (성분값 변동)", int(m.loc[cas_ch | smi_ch, "Formulation_ID"].nunique())),
]
S = pd.DataFrame(rows, columns=["metric", "value"])
S["note"] = ""
S.to_csv(f"{OUT}/v4_v5_impact_summary.csv", index=False)

m.loc[cas_ch | smi_ch, K + ["ing_name_best", "ing_cas_best_v4", "ing_cas_best_v5",
                            "smiles_v4", "smiles_v5", "june_audit_recovered_v4",
                            "june_audit_recovered_v5", "june_merge_basis",
                            "v4_defect_verdict"]] \
    .to_csv(f"{OUT}/v4_to_v5_row_diff.csv", index=False)

# ---- 잔여 미복구: v5 토큰 판정 로그 기준 (build_input_v5.py 가 남긴 실제 결정)
TD = pd.read_csv(f"{OUT}/june_merge_v5_token_decisions.csv")
evtok = {(r.Formulation_ID, r.pipe_index): (r.assigned_cas_v4, r.verdict)
         for r in EV.itertuples(index=False)}
TD["v4_assigned_cas"] = [evtok.get((f, i), (None, None))[0]
                         for f, i in zip(TD["Formulation_ID"], TD["pipe_index"])]
TD["v4_defect_verdict"] = [evtok.get((f, i), (None, None))[1]
                           for f, i in zip(TD["Formulation_ID"], TD["pipe_index"])]
R = TD[~TD["decision"].str.startswith("candidate:")].copy()
R.to_csv(f"{OUT}/v5_residual_unrecovered.csv", index=False)

print(S.to_string(index=False))
print("\nv5 basis 분포(병합행):")
print(m.loc[r5, "june_merge_basis"].value_counts().to_string())
print("\nv5 에서 철회된 v4 병합행의 결함판정:")
print(m.loc[r4 & ~r5, "v4_defect_verdict"].value_counts().to_string())
print("\nv4·v5 모두 병합된 행의 v4 결함판정:")
print(m.loc[r4 & r5, "v4_defect_verdict"].value_counts().to_string())
print("\nv5 토큰 판정 분포 (워크북 토큰 606건):")
print(TD["decision"].value_counts().to_string())
print("\n미복구 토큰 사유 × v4 결함판정:")
print(pd.crosstab(R["decision"], R["v4_defect_verdict"].fillna("-")).to_string())
print(f"\n→ {OUT}/v4_v5_impact_summary.csv")
print(f"→ {OUT}/v4_to_v5_row_diff.csv")
print(f"→ {OUT}/v5_residual_unrecovered.csv")
