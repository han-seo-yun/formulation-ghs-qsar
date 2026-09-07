#!/usr/bin/env python3
"""축 C-3 — 구조(SMILES) 표준화 검증 (Fourches et al. "trust, but verify" 방식).

1) 이미 확보된 SMILES 3574건에 RDKit SanitizeMol 재검증 (형식상 유효한지)
2) 구조 결측 1713건을 '원리적 결측'(비활성/혼합성분 등 의도적 미조사) vs
   '수집누락 후보'로 재태깅 — structure_completeness/evidence_class 근거 사용
3) 동일 CAS인데 canonical SMILES가 다른 경우(잠재 오류) 탐지

산출: 04_모델산출물/v4/structure_quality_audit.csv (v3 원본 불변, 신규 태그만 추가)
"""
import pandas as pd
from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")

SRC = "/Users/hanseoyun/Desktop/260830/04_모델산출물/input_dataset_v4.xlsx"  # 2026-08-29부터 v4 기준
OUT = "/Users/hanseoyun/Desktop/260830/04_모델산출물/v4/structure_quality_audit.csv"

ing = pd.read_excel(SRC, sheet_name="ingredient")


def check_smiles(s):
    if pd.isna(s):
        return None, None
    mol = Chem.MolFromSmiles(str(s), sanitize=False)
    if mol is None:
        return False, None
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return False, None
    return True, Chem.MolToSmiles(mol)


valid, canon = [], []
for s in ing["smiles"]:
    v, c = check_smiles(s)
    valid.append(v)
    canon.append(c)
ing["smiles_valid"] = valid
ing["smiles_canonical"] = canon

n_present = ing["smiles"].notna().sum()
n_valid = (ing["smiles_valid"] == True).sum()  # noqa: E712
n_invalid = (ing["smiles_valid"] == False).sum()  # noqa: E712
print(f"[구조 유효성] SMILES 보유 {n_present}건 중 RDKit 재검증 통과 {n_valid}건, 실패(형식오류) {n_invalid}건")
if n_invalid:
    print(ing.loc[ing["smiles_valid"] == False, ["Formulation_ID", "ingredient_name", "cas", "smiles"]].head(10).to_string())

# --- 결측 원인 재태깅 ---
PRINCIPLED = {"active_only", "complete_ex_mixture"}


def reason(row):
    if pd.notna(row["smiles"]):
        return "present"
    if row.get("evidence_class") == "B_NOT_SDS_RESEARCHED":
        return "collection_gap_candidate"
    if row["structure_completeness"] in PRINCIPLED:
        return "principled_skip"  # 비활성/혼합성분 등, 해당 제형의 구조작업 자체는 완료로 판정됨
    return "collection_gap_candidate"  # structure_completeness in (none, partial) 등


ing["missing_reason"] = ing.apply(reason, axis=1)
print("\n[결측 원인 재태깅]")
print(ing["missing_reason"].value_counts())

# --- CAS 동일 & canonical SMILES 다른 경우 (잠재 데이터 오류) ---
have = ing[ing["smiles_canonical"].notna() & ing["cas"].notna()]
grp = have.groupby("cas")["smiles_canonical"].nunique()
conflicting_cas = grp[grp > 1].index.tolist()
print(f"\n[동일 CAS, 상이한 canonical SMILES] {len(conflicting_cas)}개 CAS")
if conflicting_cas:
    ex = have[have["cas"].isin(conflicting_cas[:5])][["cas", "ingredient_name", "smiles", "smiles_canonical"]]
    print(ex.drop_duplicates(subset=["cas", "smiles_canonical"]).to_string())

cols = ["Formulation_ID", "ingredient_name", "cas", "smiles", "smiles_valid", "smiles_canonical",
        "structure_completeness", "missing_reason"]
ing[cols].to_csv(OUT, index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT}  ({len(ing)}행)")
