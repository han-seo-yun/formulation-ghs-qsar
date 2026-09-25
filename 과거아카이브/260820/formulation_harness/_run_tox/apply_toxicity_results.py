#!/usr/bin/env python3
"""Apply final_merged_results.json (toxicity research, no verification) to
dataset_배정.xlsx's 독성코드 sheet.

Rules:
- 추출정보: only filled when resolution == 'found' AND sds_summary.ingredients_match
  is True AND a GHS signal word or H-statements were actually found. Otherwise blank.
- 메모: only the researcher's notes when non-empty (anomalies only, per harness prompt).
  Never a generic completeness summary.
- 신규소스링크: only filled when better_source_found is True and a source_url exists,
  and only if the cell is currently empty (never overwrite existing values).
- 검증여부: left untouched (verification was not run for this pass).
"""
import json
import sys

import openpyxl

XLSX = "/Users/hanseoyun/Desktop/제형/dataset_배정.xlsx"
SHEET = "독성코드"
RESULTS = "/Users/hanseoyun/Desktop/제형/formulation_harness/_run_tox/final_merged_results.json"
ROW_MAPPING = "/Users/hanseoyun/Desktop/제형/formulation_harness/_run_tox/row_mapping.json"


def build_extract_text(field_values):
    fv = field_values or {}
    # A minority of agent outputs put toxicity keys directly on field_values
    # instead of nesting under field_values.toxicity - handle both shapes.
    tox = fv.get("toxicity") if "toxicity" in fv else fv
    signal = (tox.get("ghs_signal_word") or "").strip()
    h_stmt = (tox.get("ghs_hazard_statements") or "").strip()
    if not signal and not h_stmt:
        return None
    parts = []
    if signal:
        parts.append(signal)
    if h_stmt:
        parts.append(h_stmt)
    return " / ".join(parts)


def main():
    results = json.load(open(RESULTS, encoding="utf-8"))
    row_mapping = json.load(open(ROW_MAPPING, encoding="utf-8"))

    wb = openpyxl.load_workbook(XLSX, data_only=False)
    ws = wb[SHEET]
    headers = [c.value for c in ws[1]]
    idx = {h: i + 1 for i, h in enumerate(headers)}

    required = ["product_name", "신규소스링크", "추출정보", "메모"]
    missing = [c for c in required if c not in idx]
    if missing:
        print(f"ERROR: missing columns: {missing}", file=sys.stderr)
        sys.exit(1)

    filled_extract = 0
    filled_memo = 0
    filled_source = 0
    blank_no_match = 0
    blank_unresolved = 0

    for r in results:
        product_name = r.get("product_name")
        rows = row_mapping.get(product_name)
        if not rows:
            continue

        resolution = r.get("resolution")
        sds_summary = r.get("sds_summary") or {}
        ingredients_match = sds_summary.get("ingredients_match") is True
        notes = (r.get("notes") or "").strip()
        better_source = r.get("better_source_found") is True
        source_url = r.get("source_url")

        extract_text = None
        if resolution == "found" and ingredients_match:
            extract_text = build_extract_text(r.get("field_values"))
        elif resolution == "found" and not ingredients_match:
            blank_no_match += 1
        else:
            blank_unresolved += 1

        for row in rows:
            if extract_text:
                ws.cell(row=row, column=idx["추출정보"]).value = extract_text
                filled_extract += 1

            if notes:
                ws.cell(row=row, column=idx["메모"]).value = notes
                filled_memo += 1

            if better_source and source_url:
                cell = ws.cell(row=row, column=idx["신규소스링크"])
                if cell.value is None:
                    cell.value = source_url
                    filled_source += 1

    wb.save(XLSX)

    print(f"추출정보 filled: {filled_extract}")
    print(f"메모 filled: {filled_memo}")
    print(f"신규소스링크 filled: {filled_source}")
    print(f"blank (found but ingredient mismatch): {blank_no_match}")
    print(f"blank (unresolved/no_code_in_sds/ingredient_mismatch): {blank_unresolved}")


if __name__ == "__main__":
    main()
