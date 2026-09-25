"""
Formulation Characteristics Extraction Pipeline
워크플로우: agents/ 폴더의 에이전트 스펙을 읽어 Claude API(AWS Bedrock)로 병렬 호출,
결과를 artifacts/formulation_characteristics_output.csv에 저장한다.

실행:
  pip install anthropic pandas openpyxl
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
  export AWS_DEFAULT_REGION=us-east-1
  python workflow/extract_formulation_features.py

모델 오버라이드 (선택):
  export BEDROCK_MODEL_ID=us.anthropic.claude-opus-4-5-20251101-v1:0
"""

import os
import json
import time
import asyncio
import pandas as pd
from pathlib import Path
from typing import Any

import anthropic

# workflow/ 에는 __init__.py 없음 — 사이드 임포트 사용
from pre_fill_merge import add_name_formulation_code

# ─── 경로 설정 ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
REVIEW_QUEUE = BASE_DIR / "artifacts/enriched_queue.csv"          # Phase 1 풍부화 데이터 (1,675행)
REVIEW_QUEUE_RAW = BASE_DIR / "formulation_ingredients_master_audited.xlsx"  # 폴백: master workbook
AGENTS_DIR = BASE_DIR / "agents"
OUTPUT_DIR = BASE_DIR / "artifacts"
OUTPUT_CSV = OUTPUT_DIR / "formulation_characteristics_output.csv"
CACHE_DIR = OUTPUT_DIR / "extraction_cache"

BATCH_SIZE = 25
# Bedrock 모델 ID (AWS 콘솔 → Bedrock → Base models 에서 활성화된 ID 확인)
MODEL = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5")

# 에이전트 파일 → 출력 키 매핑
AGENTS = {
    "type":       "formulation_type_classifier.md",
    "hazard":     "hazard_profile_extractor.md",
    "use":        "use_application_extractor.md",
    "regulatory": "regulatory_feature_extractor.md",
    "profile":    "active_ingredient_profiler.md",
}

# 각 에이전트가 반환하는 필드 (JSON 스키마 검증용)
AGENT_OUTPUT_FIELDS = {
    "type":       ["formulation_code", "physical_form", "product_category", "concentration_type", "type_confidence", "type_evidence_basis"],
    "hazard":     ["signal_word", "ghs_hazard_classes", "acute_toxicity_route", "environmental_hazard", "h_codes", "hazard_level_score", "hazard_confidence"],
    "use":        ["use_class", "target_pest_organism", "target_crop_site", "application_method", "use_pattern", "use_confidence"],
    "regulatory": ["epa_reg_number", "regulatory_jurisdiction", "registration_status", "active_ingredient_declared", "reach_registered", "label_type", "regulatory_confidence"],
    "profile":    ["active_pct_total", "inert_pct_total", "primary_active_ingredient", "ingredient_count_class", "concentration_class", "pct_data_quality", "formulation_complexity_score"],
}

# Cancer_PID 계열: LLM 없이 규칙 기반 즉시 처리
CANCER_DEFAULTS = {
    "formulation_code": "UNKNOWN", "physical_form": "unknown", "product_category": "research/cancer_study",
    "concentration_type": "unknown", "type_confidence": "HIGH", "type_evidence_basis": "source_filename",
    "signal_word": "UNKNOWN", "ghs_hazard_classes": "", "acute_toxicity_route": "",
    "environmental_hazard": "unknown", "h_codes": "", "hazard_level_score": 0, "hazard_confidence": "LOW",
    "use_class": "research/cancer_study", "target_pest_organism": "", "target_crop_site": "",
    "application_method": "unknown", "use_pattern": "unknown", "use_confidence": "HIGH",
    "epa_reg_number": None, "regulatory_jurisdiction": "RESEARCH_ONLY", "registration_status": "not_registered",
    "active_ingredient_declared": False, "reach_registered": False, "label_type": "unknown", "regulatory_confidence": "HIGH",
    "active_pct_total": None, "inert_pct_total": None, "primary_active_ingredient": None,
    "ingredient_count_class": "unknown", "concentration_class": "unknown", "pct_data_quality": "none",
    "formulation_complexity_score": 1,
}


def load_agent_spec(agent_file: str) -> str:
    """에이전트 .md 파일에서 프롬프트 시스템 텍스트 로드."""
    path = AGENTS_DIR / agent_file
    content = path.read_text(encoding="utf-8")
    # frontmatter(---) 제거
    if content.startswith("---"):
        end = content.find("---", 3)
        content = content[end + 3:].strip()
    return content


def load_review_queue() -> pd.DataFrame:
    """enriched_queue.csv 로드 (없으면 master workbook 폴백)."""
    use_enriched = REVIEW_QUEUE.exists()
    if not use_enriched:
        print("  경고: enriched_queue.csv 없음 → master workbook 폴백 사용 (sec_* 데이터 미포함)")

    base_cols = [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "Formulation_Ingredients_CAS",
        "Formulation_Ingredients_Pct", "Ingredient_Count",
        "Source_Files", "Audit_Status",
        "Audit_Evidence_Snippet", "Audit_Evidence_Terms",
        "Audit_Source_Extracted_Ingredients", "Ingredient_Source",
        "name_formulation_code",    # Phase 1.5 힌트; 없으면 폴백 루프가 ""로 초기화
    ]
    sec_cols = [
        "sec_formulation_code", "sec_epa_reg_number", "sec_pcp_reg_number",
        "sec_registration_type", "sec_product_name", "sec_active_declared",
        "sec_signal_word", "sec_h_codes", "sec_p_codes", "sec_ghs_classes",
        "sec_environmental_hazard", "sec_hazard_level_score", "sec_un_number",
        "sec1_text", "sec2_text",
    ]
    all_cols = base_cols + sec_cols
    if use_enriched:
        df = pd.read_csv(REVIEW_QUEUE, usecols=lambda c: c in all_cols, low_memory=False)
    else:
        df = pd.read_excel(REVIEW_QUEUE_RAW, sheet_name="Formulations",
                           usecols=lambda c: c in all_cols)
    for col in all_cols:
        if col not in df.columns:
            df[col] = ""
    df = add_name_formulation_code(df)   # in-memory 이름 패턴 추출 (멱등)
    return df.fillna("")


# 에이전트별 프롬프트에 포함할 컬럼 (토큰 절약 — 에이전트마다 필요한 것만)
AGENT_INPUT_COLS: dict[str, list[str]] = {
    "type": [
        "Formulation_ID", "Formulation_Name", "Formulation_Ingredients",
        "Formulation_Ingredients_Pct", "Source_Files",
        "sec_formulation_code", "sec_product_name", "sec1_text",
        "name_formulation_code",    # 이름 패턴 힌트; sec_formulation_code와 불일치 시 sec1_text 기준 판단
    ],
    "hazard": [
        "Formulation_ID", "Formulation_Name",
        "Audit_Evidence_Snippet", "Audit_Evidence_Terms",
        "sec_signal_word", "sec_h_codes", "sec_p_codes",
        "sec_ghs_classes", "sec_environmental_hazard",
        "sec_hazard_level_score", "sec_un_number", "sec2_text",
    ],
    "use": [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "Formulation_Ingredients_CAS",
        "Source_Files", "Audit_Evidence_Snippet",
        "sec_product_name",
    ],
    "regulatory": [
        "Formulation_ID", "Formulation_Name",
        "Ingredient_Source", "Source_Files",
        "Audit_Evidence_Snippet", "Audit_Evidence_Terms",
        "sec_epa_reg_number", "sec_pcp_reg_number", "sec_reach_id",
        "sec_registration_type", "sec_active_declared", "sec1_text",
    ],
    "profile": [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "Formulation_Ingredients_CAS",
        "Formulation_Ingredients_Pct", "Ingredient_Count",
        "Audit_Source_Extracted_Ingredients",
    ],
}


def build_batch_prompt(batch_df: pd.DataFrame, agent_key: str) -> str:
    """에이전트별 필요 컬럼만 골라 프롬프트 구성 (토큰 절약)."""
    keep = [c for c in AGENT_INPUT_COLS[agent_key] if c in batch_df.columns]
    rows = batch_df[keep].to_dict(orient="records")
    rows_json = json.dumps(rows, ensure_ascii=False, indent=2)
    fields = AGENT_OUTPUT_FIELDS[agent_key]
    fields_str = ", ".join(f'"{f}"' for f in fields)

    return f"""아래 제형 데이터 배치({len(rows)}행)에서 각 행의 특성을 추출하시오.
sec_* 필드는 PDF에서 직접 추출한 구조 데이터이므로 우선 활용할 것.

입력 데이터:
{rows_json}

출력 규칙:
1. 반드시 JSON 배열로 반환 (다른 텍스트 없이)
2. 각 원소는 반드시 "Formulation_ID" 필드 포함
3. 추출 불가 필드는 null 또는 "UNKNOWN" (숫자 필드는 0)
4. 각 원소의 필드: "Formulation_ID", {fields_str}

JSON 배열만 반환:"""


async def call_agent_async(
    client: anthropic.AsyncAnthropic,
    system_prompt: str,
    user_prompt: str,
    agent_key: str,
    batch_idx: int,
) -> list[dict]:
    """단일 에이전트 비동기 호출. 결과 JSON 파싱 후 반환."""
    cache_file = CACHE_DIR / f"{agent_key}_batch_{batch_idx:03d}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    for attempt in range(3):
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=16384,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = next(b.text for b in response.content if b.type == "text").strip()
            # 코드펜스 제거 (```json ... ``` 또는 ``` ... ```)
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rsplit("```", 1)[0].strip()
            # JSON 배열 파싱
            start = text.find("[")
            end = text.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError(f"JSON 배열 없음: {text[:200]}")
            results = json.loads(text[start:end])
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
            return results
        except Exception as e:
            print(f"  [재시도 {attempt+1}/3] {agent_key} batch {batch_idx}: {e}")
            if attempt < 2:
                await asyncio.sleep(5)

    # 3회 실패 → 빈 결과
    print(f"  [실패] {agent_key} batch {batch_idx} → UNKNOWN 채움")
    return []


async def process_batch(
    client: anthropic.AsyncAnthropic,
    batch_df: pd.DataFrame,
    batch_idx: int,
    agent_specs: dict[str, str],
) -> dict[str, list[dict]]:
    """배치 1개에 대해 5개 에이전트를 병렬 실행."""
    tasks = {
        key: call_agent_async(
            client,
            agent_specs[key],
            build_batch_prompt(batch_df, key),
            key,
            batch_idx,
        )
        for key in AGENTS
    }
    results = await asyncio.gather(*tasks.values())
    return dict(zip(tasks.keys(), results))


def merge_results(all_results: dict[str, list[dict]], base_df: pd.DataFrame) -> pd.DataFrame:
    """Formulation_ID 기준으로 5개 에이전트 결과 병합."""
    merged = base_df[["Formulation_ID", "Formulation_Name"]].copy()

    for key, records in all_results.items():
        if not records:
            continue
        agent_df = pd.DataFrame(records)
        if "Formulation_ID" not in agent_df.columns:
            continue
        merged = merged.merge(agent_df, on="Formulation_ID", how="left")

    return merged


def fill_cancer_defaults(df: pd.DataFrame) -> pd.DataFrame:
    """Cancer_PID 계열 행을 규칙 기반으로 즉시 채움."""
    mask = df["Formulation_ID"].str.startswith("Cancer_PID_")
    for field, value in CANCER_DEFAULTS.items():
        if field not in df.columns:
            df[field] = None  # object dtype — accepts both str and numeric
        df.loc[mask, field] = value
    return df


async def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 인증 방식 자동 선택 (파이프라인 동작에는 영향 없음):
    #  - AWS_BEARER_TOKEN_BEDROCK (Bedrock API 키, ABSK...) 가 있으면 그것을 사용
    #  - 없으면 기존 방식 그대로: IAM 키(AWS_ACCESS_KEY_ID/SECRET) 사용
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        client = anthropic.AsyncAnthropicBedrock(
            aws_region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    else:
        client = anthropic.AsyncAnthropicBedrock(
            aws_access_key=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )

    print("=== 제형 특성 추출 파이프라인 시작 ===")
    print(f"모델: {MODEL}")

    # 에이전트 스펙 로드
    agent_specs = {key: load_agent_spec(fname) for key, fname in AGENTS.items()}
    print(f"에이전트 {len(agent_specs)}개 로드 완료")

    # 데이터 로드
    df = load_review_queue()
    print(f"총 {len(df)}행 로드 완료")

    # Cancer_PID 분리 (규칙 기반)
    cancer_mask = df["Formulation_ID"].str.startswith("Cancer_PID_")
    cancer_df = df[cancer_mask].copy()
    work_df = df[~cancer_mask].copy()
    print(f"Cancer_PID 계열: {len(cancer_df)}행 (규칙 기반 처리)")
    print(f"추출 대상: {len(work_df)}행 (LLM 처리)")

    # 배치 분할
    batches = [work_df.iloc[i:i+BATCH_SIZE] for i in range(0, len(work_df), BATCH_SIZE)]
    print(f"배치 수: {len(batches)} × {BATCH_SIZE}행")

    # 배치별 처리
    all_results: dict[str, list[dict]] = {key: [] for key in AGENTS}
    for idx, batch in enumerate(batches):
        print(f"\n[배치 {idx+1}/{len(batches)}] {len(batch)}행 처리 중...")
        t0 = time.time()
        batch_results = await process_batch(client, batch, idx, agent_specs)
        elapsed = time.time() - t0
        for key, records in batch_results.items():
            all_results[key].extend(records)
        print(f"  완료 ({elapsed:.1f}초, 누적: {sum(len(v) for v in all_results.values())}건)")

    # 결과 병합
    print("\n=== 결과 병합 중... ===")
    output_df = merge_results(all_results, work_df)

    # Cancer_PID 처리 결과 추가
    cancer_output = fill_cancer_defaults(cancer_df[["Formulation_ID", "Formulation_Name"]].copy())
    output_df = pd.concat([output_df, cancer_output], ignore_index=True)

    # 저장
    output_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    excel_path = OUTPUT_CSV.with_suffix(".xlsx")
    output_df.to_excel(excel_path, index=False)
    print(f"\n저장 완료:")
    print(f"  CSV:  {OUTPUT_CSV}")
    print(f"  XLSX: {excel_path}")
    print(f"  총 행: {len(output_df)}, 총 컬럼: {len(output_df.columns)}")

    # 간단 품질 검증
    print("\n=== 품질 검증 ===")
    if "formulation_code" in output_df.columns:
        unknown_rate = (output_df["formulation_code"] == "UNKNOWN").mean()
        print(f"  formulation_code UNKNOWN 비율: {unknown_rate:.1%} (목표 < 40%)")
    if "use_class" in output_df.columns:
        filled_rate = output_df["use_class"].notna().mean()
        print(f"  use_class 추출률: {filled_rate:.1%} (목표 ≥ 85%)")
    if "epa_reg_number" in output_df.columns:
        epa_rate = output_df["epa_reg_number"].notna().mean()
        print(f"  epa_reg_number 발견률: {epa_rate:.1%} (목표 ≥ 25%)")
    print("\n=== 완료 ===")


if __name__ == "__main__":
    asyncio.run(main())
