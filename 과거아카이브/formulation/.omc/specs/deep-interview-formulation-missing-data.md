# Deep Interview Spec: Formulation Missing Data Resolution

## Metadata
- Interview ID: di-formulation-missing-2026
- Rounds: 4 (+ Round 0 topology)
- Final Ambiguity Score: 17%
- Type: brownfield
- Generated: 2026-07-13
- Threshold: 0.2 (20%)
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

---

## Clarity Breakdown

| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.85 | 35% | 0.298 |
| Constraint Clarity | 0.85 | 25% | 0.213 |
| Success Criteria | 0.80 | 25% | 0.200 |
| Context Clarity | 0.82 | 15% | 0.123 |
| **Total Clarity** | | | **0.834** |
| **Ambiguity** | | | **16.6%** |

---

## Topology

| Component | Status | Description | Coverage Note |
|-----------|--------|-------------|---------------|
| Rule-based 즉시 보완 | active | sec_formulation_code(PDF) + Formulation_Name 패턴 통합 → pre-fill 컬럼 생성 | Phase 2 LLM 실행 전 전처리 단계 |
| PDF 파싱 고도화 | deferred | pdf_section_extractor.py regex 개선 | 현재 기존 411 JSON 활용. 이후 fill rate 모니터링 후 재검토 |
| Phase 2 LLM 실행 | active | A1(formulation_type_classifier) + A4(regulatory_feature_extractor) AWS Bedrock 실행 | enriched_queue + pre-fill 통합본 입력 |
| 구조적 모순 해소 | active | formulation_code 있는 행 → {physical_form, product_category, concentration_type} 반드시 동시 존재 | Phase 2 A1 실행으로 해소됨 |

---

## Goal

`formulation_code`(현재 7.8%)와 `epa_reg_number`(현재 2.5%)의 결측을 다음 2-step 파이프라인으로 보완한다:

1. **Pre-fill Step**: `sec_formulation_code`(PDF regex 추출)와 `Formulation_Name` 패턴 매칭(EC/SC/WP/FS/WG/GR/EW/SL/SP/CS/DC/RTU/ULV/TB)을 통합하여 `pre_formulation_code` 컬럼 생성 후 `enriched_queue.csv`에 합산.
2. **LLM Step**: pre-fill이 통합된 데이터를 입력으로 Phase 2 A1/A4 에이전트 실행. A1이 `formulation_code` + 지원 3필드, A4가 `epa_reg_number` + 규제 필드를 생성.

**핵심 invariant (구조적 모순 해소):** `formulation_code`가 non-null인 모든 행에서 `physical_form`, `product_category`, `concentration_type`도 반드시 non-null이어야 한다.

---

## Constraints

- LLM 실행 환경: **AWS Bedrock** (기존 `extract_formulation_features.py` 코드 유지)
- AWS 자격증명: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` 환경변수 필요
- Pre-fill 우선순위: `sec_formulation_code`(PDF) 존재 시 우선 사용 → 없으면 name 패턴으로 보완
- 입력 파일: `artifacts/enriched_queue.csv` (1,675행 × 31열)
- Phase 2 캐시: `extraction_cache/` 활용 (중단 후 재시작 지원)
- PDF 고도화(regex 개선)는 현재 **연기** — 이후 fill rate 점검 후 재검토

---

## Non-Goals

- PDF regex 패턴 개선 (현재 연기)
- 1,675행 전체의 formulation_code fill rate 목표치 없음 — "최대한 많이"가 아닌 "코드가 있으면 지원 필드도 있어야 한다"가 기준
- epa_reg_number의 specific 지원 필드 pair integrity (A4 전체 출력을 그대로 사용)
- Phase 3(Excel 통합) — Phase 2 완료 후 별도 단계

---

## Acceptance Criteria

- [ ] **Pre-fill 컬럼 생성**: `enriched_queue.csv`에 `pre_formulation_code` 컬럼 추가 (sec_ + name 패턴 통합, 중복 시 sec_ 우선)
- [ ] **Pair integrity 검증**: Phase 2 A1 실행 후, `formulation_code` non-null 행 중 `physical_form`, `product_category`, `concentration_type` 중 하나라도 null인 행이 0건
- [ ] **A1 실행 완료**: `formulation_characteristics_output.csv`에 `formulation_code`, `physical_form`, `product_category`, `concentration_type` 컬럼 존재
- [ ] **A4 실행 완료**: 동 파일에 `epa_reg_number`, `regulatory_jurisdiction`, `registration_status` 컬럼 존재
- [ ] **Pre-fill 효과 확인**: Phase 2 입력 전 `pre_formulation_code` non-null 행 ≥ 250건 (기존 131건 + name패턴 134건)
- [ ] **실행 재현성**: `extraction_cache/` 활용으로 중단 후 재시작 시 완료된 배치는 재실행되지 않음

---

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| Phase 2가 블로킹 상태(API 없음) | AWS Bedrock 키 있음? | **보유 확인** — 기존 코드 그대로 실행 가능 |
| PDF 파싱이 안 됐을 것 | 실제 phase 0.5 완료 여부 | **완료됨** — 411 JSON 존재, but fill rate가 낮음 |
| 구조적 모순 = 전체 행 fill rate 문제 | 완료 기준은 fill rate? | **아님** — "코드가 있는 행은 지원 필드도 있어야" = pair integrity |
| 지원 필드 = A1 전체 6개 | toxicity-relevant만? | **3개** — physical_form + product_category + concentration_type |
| Rule-based를 Phase 2 이후 fallback으로 | 언제 적용? | **이전에 통합** — sec_ + name 패턴 → pre_formulation_code → LLM 입력 |

---

## Technical Context

### 현재 파이프라인 상태

```
Phase 0.5 ✅  pdf_section_extractor.py → 411 JSON (section_extracts/)
Phase 1   ✅  enrich_with_sections.py → enriched_queue.csv (1675행 × 31열)
Phase 1.5 ❌  [신규] pre_fill_merge.py → enriched_queue + pre_formulation_code
Phase 2   ❌  extract_formulation_features.py → formulation_characteristics_output.csv
Phase 3   ⏳  Excel 통합 (Phase 2 완료 후)
```

### 신규 Phase 1.5 — Pre-fill 통합 로직

```python
# 적용 우선순위
def resolve_pre_formulation_code(row):
    if pd.notna(row['sec_formulation_code']):
        return row['sec_formulation_code']          # PDF 파싱 우선
    # Formulation_Name에서 패턴 추출
    codes = ['EC','SC','WP','WG','WDG','FS','EW','SL','SP','CS','DC','RTU','GR','ULV','TB']
    m = re.search(r'\b(' + '|'.join(codes) + r')\b', str(row['Formulation_Name']))
    return m.group(1) if m else None
```

예상 결과: `pre_formulation_code` non-null ≈ 250건+ (131 sec_ + 134 name-only, 일부 중복 제거)

### A1 에이전트 입력/출력 (formulation_type_classifier.md)

- 입력 컬럼: `Formulation_Name`, `Formulation_Ingredients`, `sec1_text`, `pre_formulation_code`(신규 추가)
- 출력: `formulation_code`, `physical_form`, `product_category`, `concentration_type`, `type_confidence`, `type_evidence_basis`
- **수정 필요**: `extract_formulation_features.py`의 A1 입력 컬럼 목록에 `pre_formulation_code` 추가

### A4 에이전트 입력/출력 (regulatory_feature_extractor.md)

- 입력 컬럼: `Formulation_Name`, `sec_epa_reg_number`, `sec_registration_type`, `sec1_text`
- 출력: `epa_reg_number`, `regulatory_jurisdiction`, `registration_status`, `active_ingredient_declared`, `label_type`, `regulatory_confidence`

### Fill Rate 현황 (before/expected after)

| Field | Before Phase 1.5 | After Phase 1.5 | After Phase 2 (expected) |
|-------|-----------------|-----------------|--------------------------|
| formulation_code | 7.8% (131) | ~14.9% (250+) | 50%+ (LLM 보완) |
| epa_reg_number | 2.5% (42) | 2.5% (변화 없음) | 10%+ (A4 추정) |
| physical_form | 0% | 0% | formulation_code와 pair |
| product_category | 0% | 0% | formulation_code와 pair |
| concentration_type | 0% | 0% | formulation_code와 pair |

---

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| Formulation | core domain | Formulation_ID, Formulation_Name, Formulation_Ingredients | has formulation_code, epa_reg_number |
| formulation_code | attribute | EC/SC/WP/FS/... 타입 코드 | requires physical_form, product_category, concentration_type |
| epa_reg_number | attribute | XXXXX-XXXX 형식 | belongs to Formulation, generated by A4 |
| pre_formulation_code | derived attribute | sec_ OR name-pattern 통합 | pre-fills LLM input |
| supporting_fields | attribute group | physical_form, product_category, concentration_type | pair integrity with formulation_code |
| pair_integrity | invariant | — | formulation_code non-null → supporting_fields non-null |
| Phase2-LLM | process | A1 + A4 agents, AWS Bedrock | generates formulation_code + supporting_fields |
| enriched_queue | data artifact | 1675행 × 31열 CSV | input to Phase 2 |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 5 | 5 | — | — | N/A |
| 2 | 7 | 2 | 0 | 5 | 71% |
| 3 | 10 | 3 | 0 | 7 | 78% |
| 4 | 13 | 3 | 0 | 10 | 85% |

---

## Interview Transcript

<details>
<summary>Full Q&A (4 rounds + Round 0)</summary>

### Round 0 (Topology)
**Q:** 4개 컴포넌트 구조가 맞나요?
**A:** 맞음 — 4개 모두 진행
**Ambiguity:** not scored yet

### Round 1
**Q:** Phase 2 LLM 실행 환경이 어떻게 되나요? (AWS Bedrock vs Anthropic API)
**A:** AWS Bedrock 키 있음 — 기존 코드 그대로
**Ambiguity:** 56% (Goal: 0.65, Constraints: 0.60, Criteria: 0.30, Context: 0.72)

### Round 2
**Q:** 결측 보완의 '완료' 기준이 뭔가요?
**A:** 코드가 채워진 행만나도 지원필드 보유 (pair integrity)
**Ambiguity:** 31% (Goal: 0.72, Constraints: 0.65, Criteria: 0.65, Context: 0.75)

### Round 3
**Q:** formulation_code와 함께 반드시 있어야 할 지원 필드는 어느 수준인가요?
**A:** physical_form + product_category + concentration_type (A1 중 3개, toxicity-relevant만)
**Ambiguity:** 22% (Goal: 0.80, Constraints: 0.78, Criteria: 0.75, Context: 0.80)

### Round 4
**Q:** Rule-based + Phase 2 LLM 실행 순서가 어떻게 되어야 하나요?
**A:** PDF 파싱 결과(sec_) + name패턴 통합 후 LLM 실행
**Ambiguity:** 17% ✅ (Goal: 0.85, Constraints: 0.85, Criteria: 0.80, Context: 0.82)

</details>
