"""
Phase 1 — Enrich review_queue with PDF section data
section_extracts/{id}_sections.json 을 review_queue.csv 행에 조인한다.
출력: artifacts/enriched_queue.csv

단독 실행 가능:
  python workflow/enrich_with_sections.py
"""

import json
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
MASTER_WORKBOOK = BASE_DIR / "formulation_ingredients_master_audited.xlsx"
SECTION_DIR  = BASE_DIR / "ingredient_source_audit/section_extracts"
OUTPUT_CSV   = BASE_DIR / "artifacts/enriched_queue.csv"

INPUT_COLS = [
    "Formulation_ID", "Formulation_Name",
    "Formulation_Ingredients", "Formulation_Ingredients_CAS",
    "Formulation_Ingredients_Pct", "Ingredient_Count",
    "Source_Files", "Audit_Status", "Audit_Issues",
    "Audit_Evidence_Snippet", "Audit_Evidence_Terms",
    "Audit_Source_Extracted_Ingredients", "Ingredient_Source",
    "Audit_Source_File", "Audit_Highlighted_Source_File",
]


def load_section_index() -> dict[str, dict]:
    """source_id → section JSON 매핑 로드."""
    index = {}
    for path in SECTION_DIR.glob("*_sections.json"):
        sid = path.stem.replace("_sections", "")
        try:
            index[sid] = json.loads(path.read_text())
        except Exception:
            pass
    return index


def extract_flat_fields(sec: dict) -> dict:
    """section JSON에서 에이전트에 필요한 필드만 평탄화."""
    out = {}

    s1 = sec.get("section_1", {})
    s2 = sec.get("section_2", {})
    s14 = sec.get("section_14", {})
    lh = sec.get("label_header", {})
    fb = sec.get("fallback", {})

    # A1/A4용 — 제형 코드, 등록번호
    out["sec_formulation_code"]  = (s1.get("formulation_code") or
                                    lh.get("formulation_code") or
                                    fb.get("formulation_code") or "")
    out["sec_epa_reg_number"]    = (s1.get("epa_reg_number") or
                                    lh.get("epa_reg_number") or
                                    fb.get("epa_reg_number") or "")
    out["sec_pcp_reg_number"]    = s1.get("pcp_reg_number", "") or ""
    out["sec_reach_id"]          = s1.get("reach_id", "") or ""
    out["sec_registration_type"] = s1.get("registration_type", "") or ""
    out["sec_product_name"]      = s1.get("product_name", "") or ""
    out["sec_active_declared"]   = str(s1.get("active_ingredient_declared", "") or
                                       lh.get("active_ingredient_declared", ""))

    # A2용 — 위험도
    out["sec_signal_word"]        = (s2.get("signal_word") or lh.get("signal_word") or "")
    out["sec_h_codes"]            = ";".join(s2.get("h_codes", []))
    out["sec_p_codes"]            = ";".join(s2.get("p_codes", []))
    out["sec_ghs_classes"]        = ";".join(s2.get("ghs_classes", []))
    out["sec_environmental_hazard"]= s2.get("environmental_hazard", "") or ""
    out["sec_hazard_level_score"] = str(s2.get("hazard_level_score", ""))
    out["sec_un_number"]          = s14.get("un_number", "") or ""

    # 섹션 텍스트 발췌 (에이전트 컨텍스트용)
    out["sec1_text"] = s1.get("text_excerpt", "") or ""
    out["sec2_text"] = s2.get("text_excerpt", "") or ""

    return out


def source_id_from_row(row: pd.Series) -> str | None:
    """행에서 source_id 추출 — Audit_Highlighted_Source_File 경로에서 파싱."""
    for col in ["Audit_Highlighted_Source_File", "Audit_Source_File"]:
        val = str(row.get(col, ""))
        if "_highlighted" in val:
            stem = Path(val).stem  # e.g. "07483aea85397703_highlighted"
            return stem.replace("_highlighted", "")
    return None


def main():
    print("=== Phase 1: Enrich with PDF Sections ===")

    df = pd.read_excel(MASTER_WORKBOOK, sheet_name="Formulations",
                       usecols=lambda c: c in INPUT_COLS).fillna("")
    print(f"master workbook 로드: {len(df)}행 (전체 제형)")

    section_index = load_section_index()
    print(f"section_extracts 로드: {len(section_index)}개 JSON")

    # 빈 enrichment 컬럼 초기화
    sample_flat = extract_flat_fields({})
    for col in sample_flat:
        df[col] = ""

    matched = 0
    for idx, row in df.iterrows():
        sid = source_id_from_row(row)
        if sid and sid in section_index:
            flat = extract_flat_fields(section_index[sid])
            for col, val in flat.items():
                df.at[idx, col] = val
            matched += 1

    print(f"섹션 데이터 매칭: {matched}/{len(df)}행 ({matched/len(df):.1%})")

    # 매칭 후 신규 커버리지
    has_signal = (df["sec_signal_word"] != "").sum()
    has_hcode  = (df["sec_h_codes"] != "").sum()
    has_code   = (df["sec_formulation_code"] != "").sum()
    has_epa    = (df["sec_epa_reg_number"] != "").sum()
    print(f"\n풍부화 후 커버리지:")
    print(f"  신호어:    {has_signal}/{len(df)} ({has_signal/len(df):.1%})")
    print(f"  H코드:     {has_hcode}/{len(df)}  ({has_hcode/len(df):.1%})")
    print(f"  제형코드:  {has_code}/{len(df)}  ({has_code/len(df):.1%})")
    print(f"  EPA Reg:   {has_epa}/{len(df)}   ({has_epa/len(df):.1%})")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUTPUT_CSV}")
    return df


if __name__ == "__main__":
    main()
