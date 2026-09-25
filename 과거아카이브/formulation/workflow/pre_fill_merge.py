"""
Phase 1.5 — Name-pattern formulation code extraction
Adds name_formulation_code column extracted from Formulation_Name only.
sec_formulation_code (PDF) is already in sec_cols — passed separately to A1.
A1 receives both and decides which to trust based on sec1_text.

단독 실행:
  python workflow/pre_fill_merge.py
출력: artifacts/enriched_queue_prefilled.csv (+name_formulation_code 컬럼)
"""
from __future__ import annotations

import re
import pandas as pd
from pathlib import Path
from typing import Optional

BASE_DIR  = Path(__file__).parent.parent
INPUT_CSV = BASE_DIR / "artifacts/enriched_queue.csv"
OUTPUT_CSV = BASE_DIR / "artifacts/enriched_queue_prefilled.csv"

# 긴 토큰 우선 (WDG가 WG보다 먼저 매칭되어야 함)
FORM_CODES = ['WDG', 'RTU', 'ULV', 'EC', 'SC', 'WP', 'WG', 'FS',
              'EW', 'SL', 'SP', 'CS', 'DC', 'GR', 'TB']
_PAT = re.compile(r'\b(' + '|'.join(FORM_CODES) + r')\b')


def extract_name_formulation_code(row: pd.Series) -> Optional[str]:
    """Formulation_Name에서만 제형 코드 추출 (PDF 파싱 결과와 병합 없음)."""
    m = _PAT.search(str(row.get('Formulation_Name', '')))
    return m.group(1) if m else None


def add_name_formulation_code(df: pd.DataFrame) -> pd.DataFrame:
    """name_formulation_code 컬럼 추가 후 반환. 멱등성 보장."""
    df = df.copy()
    df['name_formulation_code'] = df.apply(extract_name_formulation_code, axis=1)
    return df


def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            f"{INPUT_CSV} 없음. Phase 1을 먼저 실행하세요:\n"
            "  python workflow/enrich_with_sections.py"
        )
    df = pd.read_csv(INPUT_CSV, low_memory=False, encoding='utf-8-sig')
    df = add_name_formulation_code(df)

    name_filled  = df['name_formulation_code'].notna().sum()
    sec_filled   = df['sec_formulation_code'].notna().sum() if 'sec_formulation_code' in df.columns else 0

    # 불일치 분석 (정보 목적)
    if 'sec_formulation_code' in df.columns:
        both     = df['sec_formulation_code'].notna() & df['name_formulation_code'].notna()
        conflict = both & (df['sec_formulation_code'] != df['name_formulation_code'])
        agree    = both & (df['sec_formulation_code'] == df['name_formulation_code'])
        pdf_only = df['sec_formulation_code'].notna() & df['name_formulation_code'].isna()
        name_only = df['sec_formulation_code'].isna() & df['name_formulation_code'].notna()
    else:
        conflict = agree = pdf_only = name_only = pd.Series([False] * len(df))

    print(f"name_formulation_code: {name_filled}/{len(df)}행 추출")
    print(f"sec_formulation_code (PDF): {sec_filled}/{len(df)}행")
    print(f"  PDF만 있음    : {pdf_only.sum()}")
    print(f"  이름만 있음   : {name_only.sum()}")
    print(f"  둘 다 일치    : {agree.sum()}")
    print(f"  둘 다 불일치  : {conflict.sum()}  ← A1이 sec1_text로 판단")

    df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"\n저장 완료: {OUTPUT_CSV}")


if __name__ == '__main__':
    main()
