---
name: formulation-characteristics-orchestrator
description: 제형 특성 추출 오케스트레이터. PDF 재파싱 → 데이터 풍부화 → 5개 전문 에이전트 병렬 추출 → 통합의 전체 파이프라인을 조율하고, 각 단계별 스킬 투입 시점·데이터 계약·품질 게이트를 정의한다.
version: 2.0
date: 2026-07-10
metadata:
  type: orchestrator
  agents:
    - pdf-section-extractor        # Phase 0.5 (전처리, LLM 없음)
    - formulation-type-classifier  # A1
    - hazard-profile-extractor     # A2
    - use-application-extractor    # A3
    - regulatory-feature-extractor # A4
    - active-ingredient-profiler   # A5
  skills:
    - deep-research
    - oh-my-claudecode:ultrawork
    - verify
    - document-skills:xlsx
    - dataviz
---

# Formulation Characteristics Orchestrator

> 이 문서는 파이프라인 **실행 규범(runbook)** 이다. 무엇을 어떤 순서로 실행하며,
> 각 에이전트가 무엇을 받고 무엇을 내보내는지(데이터 계약), 언제 어떤 스킬을 투입하며,
> 각 단계를 언제 "통과"로 판정하는지(품질 게이트)를 정의한다.
> 개별 추출 규칙은 각 에이전트 `.md`, 전체 배경은 `workflow/formulation_characteristics_pipeline.md` 참조.

## 0. 현재 상태 스냅샷 (2026-07-10)

| 단계 | 스크립트 | 상태 | 산출물 |
|------|---------|------|--------|
| Phase 0.5 | `pdf_section_extractor.py` | ✓ 완료 | `section_extracts/` JSON 411개 |
| Phase 1 | `enrich_with_sections.py` | ○ 재실행 필요 | `enriched_queue.csv` (목표: 1,675행 × 31컬럼, 입력 master xlsx로 교체됨) |
| Phase 2 | `extract_formulation_features.py` | ○ 대기 (API 키 + deep-research 선행) | `formulation_characteristics_output.csv` |
| Phase 3 | `/document-skills:xlsx` | ○ 대기 | master workbook 통합 시트 |

| 항목 | 값 |
|------|-----|
| 실제 입력 | `artifacts/enriched_queue.csv` (폴백: raw review_queue) |
| 최종 출력 | `artifacts/formulation_characteristics_output.csv` + `.xlsx` |
| 총 행 수 | **1,675** (Cancer_PID 17행 규칙처리 + LLM 대상 1,658행) |
| 병렬 에이전트 | 5개 (차원별 독립) |
| 배치 크기 | 50행 → LLM 대상 1,658행 = 배치 34개 |
| 마스터 러너 | `workflow/run_pipeline.py` |

---

## 1. 파이프라인 아키텍처 (전체)

```
ingredient_source_audit/highlighted/  (원본 PDF/HTML 411개, 읽기 전용)
         │
         ▼  [Phase 0.5]  pdf_section_extractor.py  (LLM 없음, 규칙 기반, ~2분)
section_extracts/{source_id}_sections.json  (411개)
         │
         ▼  [Phase 1]  enrich_with_sections.py  (조인, 즉시)
artifacts/enriched_queue.csv  (1,675행: base 컬럼 + sec_* 16개, MATCH 881행은 sec_* 공란)
         │
         ▼  ─────────────  [deep-research]  A3 성분→용도 매핑 KB 구축 (1회)
         │
         ▼  [Phase 2]  extract_formulation_features.py  ([ultrawork] 병렬)
   ┌─────────────────────────────────────────────────────────┐
   │  Cancer_PID_* 계열 (17행)  →  규칙 기반 즉시 채움 (LLM 없음) │
   │  나머지 (1,658행) →  50행 배치 × 5 에이전트 병렬 (34배치)   │
   │    [A1]type  [A2]hazard  [A3]use  [A4]regulatory  [A5]profile│
   │    각 (agent, batch) 결과 → extraction_cache/ 저장 (재시작용) │
   └─────────────────────────────────────────────────────────┘
         │  Formulation_ID 기준 LEFT JOIN + Cancer_PID concat
         ▼
artifacts/formulation_characteristics_output.csv (.xlsx)
         │
         ▼  ─────────────  [verify]  품질 게이트 (§6)
         │
         ▼  [Phase 3]  [document-skills:xlsx]  master workbook 통합
         │
         ▼  ─────────────  [dataviz]  최종 리포팅 대시보드
```

### 설계 원칙
- **배치 간 순차, 에이전트 간 병렬**: 같은 배치의 5개 에이전트는 완전 독립 → 동시 호출. 배치 순서는 순차(메모리/rate limit 제어).
- **캐시 우선 재시작**: `extraction_cache/{agent_key}_batch_{idx:03d}.json` 이 존재하면 해당 (에이전트, 배치)는 재호출하지 않음. 중단 지점부터 무비용 재개.
- **sec_* 우선 원칙**: PDF에서 직접 파싱한 `sec_*` 구조 필드는 LLM 추론보다 신뢰도가 높으므로 각 에이전트가 우선 활용한다.
- **규칙 우선 분기**: LLM 불필요한 행(Cancer_PID)은 LLM 호출 전에 규칙으로 처리해 비용·시간 절약.

---

## 2. 에이전트 데이터 계약 (Inter-Agent Contract)

각 에이전트가 **받는 입력 컬럼**과 **내보내는 출력 필드**를 명시한다.
입력은 토큰 절약을 위해 에이전트별 필요 컬럼만 필터링된다 (`AGENT_INPUT_COLS` 참조).
모든 에이전트 출력은 반드시 `Formulation_ID`를 포함하는 JSON 배열이다.

### A1 · formulation-type-classifier
| 방향 | 필드 |
|------|------|
| **입력** | `Formulation_ID`, `Formulation_Name`, `Formulation_Ingredients`, `Formulation_Ingredients_Pct`, `Source_Files`, **`sec_formulation_code`**, **`sec_product_name`**, **`sec1_text`** |
| **출력** | `formulation_code`, `physical_form`, `product_category`, `concentration_type`, `type_confidence`, `type_evidence_basis` |
| **우선순위** | `sec_formulation_code` (PDF) > `Formulation_Name` 파싱 > `sec1_text`/Evidence 키워드 > 성분 패턴 추론 |

### A2 · hazard-profile-extractor
| 방향 | 필드 |
|------|------|
| **입력** | `Formulation_ID`, `Formulation_Name`, `Audit_Evidence_Snippet`, `Audit_Evidence_Terms`, **`sec_signal_word`**, **`sec_h_codes`**, **`sec_p_codes`**, **`sec_ghs_classes`**, **`sec_environmental_hazard`**, **`sec_hazard_level_score`**, **`sec_un_number`**, **`sec2_text`** |
| **출력** | `signal_word`, `ghs_hazard_classes`, `acute_toxicity_route`, `environmental_hazard`, `h_codes`, `hazard_level_score`, `hazard_confidence` |
| **우선순위** | `sec_*` (PDF Section 2 파싱값) > `sec2_text` 재분석 > Evidence_Snippet 키워드. sec 데이터 있으면 confidence=HIGH. |

### A3 · use-application-extractor
| 방향 | 필드 |
|------|------|
| **입력** | `Formulation_ID`, `Formulation_Name`, `Formulation_Ingredients`, `Formulation_Ingredients_CAS`, `Source_Files`, `Audit_Evidence_Snippet`, **`sec_product_name`** |
| **참조 KB** | `agents/ingredient_use_mapping.json` (deep-research 산출물 — Phase 2 전 주입) |
| **출력** | `use_class`, `target_pest_organism`, `target_crop_site`, `application_method`, `use_pattern`, `use_confidence` |
| **우선순위** | Source_Files 도메인(cancer/eye_irritation 즉시분류) > Name 키워드 > **성분→용도 매핑 KB** > Evidence 텍스트 |

### A4 · regulatory-feature-extractor
| 방향 | 필드 |
|------|------|
| **입력** | `Formulation_ID`, `Formulation_Name`, `Ingredient_Source`, `Source_Files`, `Audit_Evidence_Snippet`, `Audit_Evidence_Terms`, **`sec_epa_reg_number`**, **`sec_pcp_reg_number`**, **`sec_reach_id`**, **`sec_registration_type`**, **`sec_active_declared`**, **`sec1_text`** |
| **출력** | `epa_reg_number`, `regulatory_jurisdiction`, `registration_status`, `active_ingredient_declared`, `reach_registered`, `label_type`, `regulatory_confidence` |
| **우선순위** | `sec_epa_reg_number`/`sec_pcp_reg_number` (PDF) > Evidence 정규식 > Source_Files 도메인 |

### A5 · active-ingredient-profiler
| 방향 | 필드 |
|------|------|
| **입력** | `Formulation_ID`, `Formulation_Name`, `Formulation_Ingredients`, `Formulation_Ingredients_CAS`, `Formulation_Ingredients_Pct`, `Ingredient_Count`, `Audit_Source_Extracted_Ingredients` |
| **출력** | `active_pct_total`, `inert_pct_total`, `primary_active_ingredient`, `ingredient_count_class`, `concentration_class`, `pct_data_quality`, `formulation_complexity_score` |
| **특성** | 수치 계산 위주 → 가장 신뢰도 높음. sec_* 불필요 (워크북 % 데이터 사용). |

> **계약 위반 감지**: 에이전트가 `Formulation_ID` 없는 JSON을 반환하면 해당 배치 결과는 병합에서 제외되고 UNKNOWN으로 폴백된다 (`merge_results` 로직).

---

## 3. 실행 단계 상세

### Phase 0.5 — PDF Section Extractor `[완료]`
- **스킬**: 없음 (pymupdf 규칙 기반)
- **입력**: `highlighted/` PDF 358 + HTML 51 = 411개
- **출력**: `section_extracts/{source_id}_sections.json`
- **원본 불변 보장**: PDF는 읽기만 하며, 출력은 `section_extracts/`에만 기록.
- **실측 결과**: 411/411 성공, Section 2 추출 80.0%, EPA Reg 7.8%, formulation_code 22.9%.

### Phase 1 — Enrich with Sections `[완료]`
- **스킬**: 없음 (pandas 조인)
- **매칭 키**: `Audit_Highlighted_Source_File` 경로 → `source_id` 파싱 → section JSON 조인
- **평탄화 필드**: `extract_flat_fields()` — section JSON을 15개 `sec_*` 컬럼으로 전개
- **실측 결과**: (master xlsx 교체 후 재실행 시) 1,675행 처리. MATCH 881행은 PDF 없어 sec_* 전부 공란. 기존 매칭 569/794행(71.7%) → 전체 대비 569/1,675(34%) 예상. 미매칭 행은 Evidence로 폴백.

### deep-research `[Phase 2 직전, 1회]` — §5.1 참조

### Phase 2 — LLM Feature Extraction `[대기]`
- **선행 조건**: (1) `ingredient_use_mapping.json` 생성됨 (deep-research), (2) `ANTHROPIC_API_KEY` 설정됨
- **모델**: `claude-opus-4-8`
- **분기 처리**:
  ```
  Cancer_PID_* (17행)  → CANCER_DEFAULTS 규칙 채움 (LLM 0회)
  나머지 (1,658행)     → 50행 배치 34개 → (agent, batch) 별 병렬 LLM 호출
  ```
- **캐시**: 각 (agent_key, batch_idx) 응답을 `extraction_cache/`에 저장. 재실행 시 존재하면 스킵.
- **재시도**: 배치 호출 실패 시 5초 대기 후 최대 3회. 3회 실패 → 빈 결과(병합 시 UNKNOWN).
- **병합**: `Formulation_ID` LEFT JOIN (base 2컬럼 + 5 에이전트 출력) → Cancer_PID concat.

### Phase 3 — Master Workbook 통합 `[대기]` — §5.4 참조

---

## 4. 마스터 러너 (`run_pipeline.py`)

```bash
python workflow/run_pipeline.py --status         # 현재 단계 상태만 확인
python workflow/run_pipeline.py                  # 처음부터 (API 키 없으면 Phase 2 자동 스킵)
python workflow/run_pipeline.py --no-llm         # Phase 0.5~1 만
python workflow/run_pipeline.py --from-phase 2   # Phase 2부터 (캐시 활용)
python workflow/run_pipeline.py --force          # 출력 있어도 재실행
```
- 각 단계 출력 존재 시 자동 스킵 (`--force`로 무시).
- `ANTHROPIC_API_KEY` 미설정 시 Phase 2 자동 제외 + 경고.

---

## 5. 스킬 투입 스케줄

```
Phase 0.5 ──── (스킬 없음)
Phase 1   ──── (스킬 없음)
   │
   ├─ [deep-research]      ← Phase 2 직전 1회 (A3 KB 구축)
   │
Phase 2   ──── [ultrawork] ← 배치 병렬 실행
   │
   ├─ [verify]             ← output.csv 생성 직후 (§6 게이트)
   │
Phase 3   ──── [document-skills:xlsx]
   │
   └─ [dataviz]            ← 최종 리포팅
```

### 5.1 `/deep-research` — A3 성분→용도 매핑 KB
- **트리거**: Phase 1 완료 후, Phase 2 실행 전 (정확히 1회)
- **산출**: `agents/ingredient_use_mapping.json` → A3 프롬프트에 참조 테이블로 주입
- **명령**:
  ```
  /deep-research "농약 활성성분 용도 매핑: enriched_queue의 고유 활성성분 ~50개
  (Cypermethrin, Abamectin, Tebuconazole, Glyphosate, Chlorantraniliprole 등)의
  use_class(agricultural/insecticide 등), target_pest, application_method 정리"
  ```
- **기대 효과**: A3 use_class 커버리지 ~60% → 85%+

### 5.2 `/oh-my-claudecode:ultrawork` — Phase 2 병렬 실행
- **트리거**: deep-research 완료 + API 키 설정 후
- **명령**:
  ```
  /oh-my-claudecode:ultrawork "workflow/extract_formulation_features.py 의
  배치들을 각 배치별 5개 에이전트 병렬 실행. extraction_cache/ 활용해 중단 재시작 가능하게"
  ```
- **기대 효과**: 순차 ~136분(34배치×2분) → 병렬 ~15-20분

### 5.3 `/verify` — Phase 2 품질 게이트 (§6)
- **트리거**: `formulation_characteristics_output.csv` 생성 직후
- **명령**: `/verify` (자동 분석)

### 5.4 `/document-skills:xlsx` — Phase 3 통합
- **트리거**: verify 통과 후
- **명령**:
  ```
  /document-skills:xlsx "formulation_characteristics_output.csv를
  formulation_ingredients_master_audited.xlsx에 'Formulation_Characteristics' 시트로 추가,
  Formulation_ID 기준 기존 시트와 연결"
  ```

### 5.5 `/dataviz` — 최종 리포팅
- **트리거**: xlsx 통합 완료 후
- **명령**: `/dataviz "formulation_characteristics_output.csv 기반 품질 대시보드"`
- **차트**: 제형타입 분포 / 위험도 히스토그램 / use_class 막대 / 커버리지 Before-After / active_pct 박스플롯

---

## 6. 품질 게이트 (verify 항목)

Phase 2 완료 직후 자동 검증. **모든 항목 통과 시에만** Phase 3 진행.

| # | 검증 항목 | 통과 기준 | 실측 베이스라인 |
|---|----------|----------|----------------|
| G1 | 전체 행 존재 | 1,675행 모두 출력에 존재 | — |
| G2 | signal_word UNKNOWN 비율 | < 30% | sec_signal_word 43.5% 확보 |
| G3 | use_class 추출률 | ≥ 85% | deep-research 후 목표 |
| G4 | Cancer_PID use_class | = research/cancer_study 100% | 규칙 보장 |
| G5 | formulation_complexity_score | 이상치(범위 밖) 없음 | 1-5 |
| G6 | active_pct_total 합계 오류 | > 100% 인 행 = 0 (또는 inconsistent 태깅) | — |
| G7 | formulation_code UNKNOWN 비율 | < 40% | sec_formulation_code 16.5% |

**게이트 실패 시**: 실패 항목 로그 → 해당 차원 에이전트 프롬프트 조정 또는 재추출 → `--from-phase 2 --force`로 재실행.

### 단계 간 전제 조건 체크리스트
```
Phase 2 진입 전:  enriched_queue.csv 존재 ✓  +  ingredient_use_mapping.json 존재  +  API 키 설정
Phase 3 진입 전:  output.csv 존재  +  verify G1~G7 전부 통과
dataviz 진입 전:  master workbook 통합 완료
```

---

## 7. 에러 처리 및 복구

| 상황 | 처리 |
|------|------|
| 배치 LLM 호출 실패 | 5초 대기 후 최대 3회 재시도 → 3회 실패 시 빈 결과(UNKNOWN 병합) |
| JSON 파싱 실패 | `[` ~ `]` 추출 시도 → 실패 시 재시도 카운트 소진 후 스킵 |
| 프로세스 중단 | `extraction_cache/` 에 완료된 (agent,batch)는 재실행 시 스킵 → 중단점부터 재개 |
| sec_* 미매칭 행 (28.3%) | 에이전트가 Evidence_Snippet으로 폴백 추론 |
| Formulation_ID 누락 응답 | 병합 제외 → 해당 행 UNKNOWN 폴백 |
| API 키 미설정 | run_pipeline이 Phase 2 자동 스킵 + 재실행 안내 |

---

## 8. 출력 컬럼 전체 목록

### 식별자
`Formulation_ID`, `Formulation_Name` (원본 유지)

### A1 제형 타입
`formulation_code`, `physical_form`, `product_category`, `concentration_type`, `type_confidence`, `type_evidence_basis`

### A2 위험도
`signal_word`, `ghs_hazard_classes`, `acute_toxicity_route`, `environmental_hazard`, `h_codes`, `hazard_level_score`, `hazard_confidence`

### A3 사용 목적
`use_class`, `target_pest_organism`, `target_crop_site`, `application_method`, `use_pattern`, `use_confidence`

### A4 규제
`epa_reg_number`, `regulatory_jurisdiction`, `registration_status`, `active_ingredient_declared`, `reach_registered`, `label_type`, `regulatory_confidence`

### A5 성분 프로파일
`active_pct_total`, `inert_pct_total`, `primary_active_ingredient`, `ingredient_count_class`, `concentration_class`, `pct_data_quality`, `formulation_complexity_score`

---

## 9. 다음 액션 (실행 순서)

```
[완료]  Phase 0.5, Phase 1
   1.  /deep-research           → ingredient_use_mapping.json
   2.  A3 프롬프트에 KB 주입 (수동)
   3.  export ANTHROPIC_API_KEY=...
   4.  /oh-my-claudecode:ultrawork  또는  python workflow/run_pipeline.py --from-phase 2
   5.  /verify                  → G1~G7 게이트
   6.  /document-skills:xlsx    → master workbook
   7.  /dataviz                 → 대시보드
```
