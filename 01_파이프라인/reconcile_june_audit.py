#!/usr/bin/env python3
"""6/30 성분 정밀감사(formulation_audit_team_share_highlighted_only_20260630.zip)를
현재(08-29) 파이프라인 산출물과 대조해 복구 후보를 정리한다.

원칙: 기존 파일(input_dataset_v3.xlsx, dataset_배정_20260824.xlsx)은 절대 덮어쓰지
않는다. 6/30 감사의 CAS/SMILES/판정을 "이미 검증된 근거"로만 취급해 복구 우선순위
목록을 만들고, 실제 반영 여부는 사람이 검토 후 결정한다.

산출: 04_모델산출물/v4/june_audit_reconciliation.csv
"""
import re

import pandas as pd

CUR_ING = "/Users/hanseoyun/Desktop/260830/04_모델산출물/input_dataset_v4.xlsx"  # 2026-08-29부터 v4 기준(병합 반영 후 잔여 격차 확인용)
JUNE_MASTER = ("/Users/hanseoyun/Desktop/260830/formulation_audit_team_share_highlighted_only_20260630/"
               "formulation_ingredients_master_audited.xlsx")
OUT = "/Users/hanseoyun/Desktop/260830/04_모델산출물/v4/june_audit_reconciliation_remaining_after_v4.csv"
# 주의: 원본 june_audit_reconciliation.csv(v3 기준)는 build_input_v4.py가 병합 대상
# 목록으로 그대로 참조하는 입력물이라 덮어쓰지 않는다. 이 파일은 "병합 후 남은 격차"
# 를 보여주는 별도 스냅샷이다.

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def cas_set(s):
    if pd.isna(s):
        return set()
    out = set()
    for tok in re.split(r"[|,]", str(s)):
        t = tok.strip()
        parts = t.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            t = f"{int(parts[0])}-{parts[1]}-{parts[2]}"
        if _CAS_RE.match(t):
            out.add(t)
    return out


# --- 현재 성분 상태: Formulation_ID 별 CAS/SMILES 보유 여부 ---
# ing_cas_best(2차수집 우선 + 원본 fallback, build_input_*.py가 실제로 쓰는 컬럼)를
# 기준으로 본다 — 원본 raw 'cas' 컬럼만 보면 2차/6.30감사 병합분이 안 잡힌다.
cur = pd.read_excel(CUR_ING, sheet_name="ingredient")
_cas_col = "ing_cas_best" if "ing_cas_best" in cur.columns else "cas"
cur_by_fid = cur.groupby("Formulation_ID").agg(
    current_cas=(_cas_col, lambda s: "|".join(sorted(cas_set("|".join(s.dropna().astype(str)))))),
    current_n_cas=(_cas_col, lambda s: len(cas_set("|".join(s.dropna().astype(str))))),
    current_n_smiles=("smiles", lambda s: s.notna().sum()),
    current_n_ing=("ingredient_name", "count"),
)

# --- 6/30 감사 마스터워크북 ---
june = pd.read_excel(JUNE_MASTER, sheet_name="Formulations")
june["june_cas_set"] = june["Formulation_Ingredients_CAS"].apply(cas_set)
june["june_n_cas"] = june["june_cas_set"].apply(len)
june["june_has_smiles"] = june["Formulation_Ingredients_SMILES"].notna()

df = june.merge(cur_by_fid, on="Formulation_ID", how="left")
df["current_n_cas"] = df["current_n_cas"].fillna(0).astype(int)
df["current_n_smiles"] = df["current_n_smiles"].fillna(0).astype(int)
df["current_cas_set"] = df["current_cas"].fillna("").apply(lambda s: set(s.split("|")) if s else set())


def recommend(row):
    lost = row["june_cas_set"] - row["current_cas_set"]
    if row["current_n_cas"] == 0 and row["june_n_cas"] > 0:
        if row["Audit_Status"] == "MATCH":
            return "RECOVER_CAS_HIGH_CONFIDENCE"
        if row["Audit_Status"] == "EVIDENCE_WEAK":
            return "RECOVER_CAS_REVIEW"
        if row["Audit_Status"] == "MISMATCH":
            return "RECOVER_CAS_LOW_CONFIDENCE"
        return "RECOVER_CAS_CHECK_STATUS"
    if lost and row["Audit_Status"] == "MISMATCH" and len(row["june_cas_set"]) <= 15:
        return "MANUAL_REVIEW_CONFLICTING_CAS"
    if row["current_n_smiles"] == 0 and row["june_has_smiles"]:
        return "RECOVER_SMILES_ONLY"
    return "NO_ACTION"


df["recommendation"] = df.apply(recommend, axis=1)

priority_order = {"RECOVER_CAS_HIGH_CONFIDENCE": 0, "RECOVER_CAS_REVIEW": 1, "RECOVER_SMILES_ONLY": 2,
                   "MANUAL_REVIEW_CONFLICTING_CAS": 3, "RECOVER_CAS_LOW_CONFIDENCE": 4,
                   "RECOVER_CAS_CHECK_STATUS": 5, "NO_ACTION": 9}
df["priority"] = df["recommendation"].map(priority_order)

print("[복구/검토 권고 분포]")
print(df["recommendation"].value_counts())

out_cols = ["Formulation_ID", "Formulation_Name", "Audit_Status", "recommendation", "priority",
            "current_n_cas", "current_n_smiles", "current_n_ing",
            "Formulation_Ingredients_CAS", "Formulation_Ingredients_SMILES",
            "Formulation_Ingredients_PubChem_CID", "Audit_Issues"]
df.sort_values("priority")[out_cols].to_csv(OUT, index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT}  ({len(df)}행)")
