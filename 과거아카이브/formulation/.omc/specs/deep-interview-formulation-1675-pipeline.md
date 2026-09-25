# Deep Interview Spec: 1675행 전체 제형 특성 추출 파이프라인 정비

## Metadata
- Interview ID: di-formulation-20260711
- Rounds: 5
- Final Ambiguity Score: 11%
- Type: brownfield
- Generated: 2026-07-11
- Threshold: 0.20 (20%)
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

---

## Clarity Breakdown
| 차원 | 점수 | 가중치 | 가중합 |
|------|------|------|------|
| 목표 명확도 | 0.95 | 35% | 0.333 |
| 제약 명확도 | 0.85 | 25% | 0.213 |
| 성공 기준 | 0.85 | 25% | 0.213 |
| 컨텍스트 명확도 | 0.90 | 15% | 0.135 |
| **총 명확도** | | | **0.894** |
| **모호도** | | | **11%** |

---

## Topology
| 컴포넌트 | 상태 | 설명 | 커버리지 |
|---------|------|------|---------|
| 데이터 범위 확장 | active | 입력 794행 → 마스터 1,675행 전체 | master xlsx 직접 읽기로 결정 |
| 성분 카테고리 처리 | active | 동일 성분도 제형별 독립 처리 보장 | 현재 파이프라인 이미 준수, 명시적 확인만 필요 |
| 워크플로우·에이전트 정비 | active | 스크립트 4곳 + 에이전트 1곳 수정 | 4가지 범위 전부 확정 |

---

## Goal

`formulation_ingredients_master_audited.xlsx`의 전체 1,675개 제형에 대해 5차원 특성(A1 타입·A2 위험도·A3 용도·A4 규제·A5 성분 프로파일)을 추출하고, `artifacts/formulation_characteristics_output.csv`(1,675행)를 생성한다.

**현재 문제**: `enrich_with_sections.py`가 `review_queue.csv`(794행, highlighted_only 패키지)를 읽어 881개 MATCH행이 파이프라인에서 누락됨.

**목표 상태**: master xlsx 전체를 입력으로 읽어 MATCH 881행도 포함, 총 1,675행 추출.

---

## 데이터 구조 (코드베이스 실측)

```
formulation_ingredients_master_audited.xlsx
  └── Formulations 시트: 1,675행 × 49컬럼 (Formulation_ID 중복 0)

Audit_Status 분포:
  MATCH                881행  ← 현재 파이프라인 누락 (소스 파일 없음)
  EVIDENCE_WEAK        426행  ← 현재 처리 중
  MISMATCH             247행  ← 현재 처리 중
  SOURCE_UNAVAILABLE    74행  ← 현재 처리 중
  NO_DOWNLOADABLE_SOURCE 30행  ← 현재 처리 중
  NO_WORKBOOK_INGREDIENTS 17행  ← 현재 처리 중

MATCH 행 특성:
  Audit_Source_File:              0/881 (0%)   ← PDF 경로 없음
  Audit_Highlighted_Source_File:  0/881 (0%)   ← 하이라이트 PDF 없음
  Audit_Evidence_Snippet:       860/881 (97.6%) ← Evidence는 풍부
  Formulation_Ingredients:      874/881 (99.2%) ← 성분 데이터 풍부
  Formulation_Ingredients_Pct:  514/881 (58.3%)

Formulation_ID 계열:
  Cancer_PID_*          17행   → Source: cancer.xlsx
  EyeIrritation6pack_* 613행   → Source: eye_irritation_rabbit_mixture...xlsx
  MIX_PID_* 외          1,045행+ → Source: skin_irritation + acute_dermal + acute_oral (복수)
```

---

## Constraints

- **MATCH 행 (881개)**: `Audit_Source_File` = 빈값 → Phase 0.5 PDF 파싱 불가 → sec_* 컬럼 전부 빈값 → LLM이 Evidence_Snippet + 워크북 컬럼으로 폴백 추론
- **Cancer_PID_* 행 (17개)**: LLM 불필요, 기존 `CANCER_DEFAULTS` 규칙 처리 유지
- **EyeIrritation6pack 행 (613개)**: Audit_Source_File 없음 (위와 동일), Source_Files에 eye_irritation 파일명 존재 → A3 use_class 분류 힌트
- **MIX 행**: Source_Files에 여러 독성 연구 유형 혼재 가능 (skin + acute_dermal + acute_oral)
- **성분 카테고리**: 동일 CAS/성분명이 여러 제형에 반복 등장해도 각 제형별 독립 처리 — 중복 제거 금지
- **성분 추출 책임 분리**: 성분 레벨 추출은 다른 팀 담당. 우리 팀은 **제형 특성 5차원**에 집중.

---

## Non-Goals

- 독성 연구 엔드포인트(안독성/피부독성/급성독성) 분류는 이번 범위 밖 (다른 팀 담당)
- 새 에이전트 A6 생성 없음
- 성분 레벨 행 확장(ingredient-level rows) 없음 — 제형 수준 1행/제형 유지
- `Source_Files` URL 다운로드 및 신규 PDF 파싱 없음

---

## Acceptance Criteria

- [ ] `enrich_with_sections.py` 입력이 `formulation_ingredients_master_audited.xlsx` Formulations 시트를 읽음
- [ ] `artifacts/enriched_queue.csv` 행 수 = 1,675 (현재 794)
- [ ] `extract_formulation_features.py` 가 enriched_queue 1,675행 기준으로 배치 처리 (34배치, LLM 대상 1,658행)
- [ ] Cancer_PID_* 17행은 여전히 CANCER_DEFAULTS 규칙으로 처리됨 (LLM 0회)
- [ ] `artifacts/formulation_characteristics_output.csv` 행 수 = 1,675
- [ ] MATCH 881행은 sec_* 컬럼 빈값, 나머지 컬럼은 워크북 값으로 채워짐
- [ ] `agents/use_application_extractor.md` 에 Source_Files 파일명 기반 study type 힌트 추가
- [ ] `agents/orchestrator.md` 수치(794→1675, 16→34배치 등) 갱신

---

## 구체적 변경 목록

### 1. `workflow/enrich_with_sections.py` — 입력 소스 교체

```python
# 변경 전
REVIEW_QUEUE = BASE_DIR / "artifacts/share_packages/...review_queue.csv"
df = pd.read_csv(REVIEW_QUEUE, usecols=..., low_memory=False).fillna("")

# 변경 후
MASTER_WORKBOOK = BASE_DIR / "formulation_ingredients_master_audited.xlsx"
df = pd.read_excel(MASTER_WORKBOOK, sheet_name="Formulations").fillna("")
```

- `INPUT_COLS` 리스트는 master xlsx의 49컬럼과 동일 → 그대로 유지
- 출력 `artifacts/enriched_queue.csv`: 794행 → **1,675행**

### 2. `workflow/extract_formulation_features.py` — 경로 및 배치 수 대응

```python
# REVIEW_QUEUE 경로를 새 enriched_queue(1675행)로 자동 대응
# (이미 enriched_queue.csv가 1675행이 되면 별도 경로 변경 불필요)

# Cancer_PID 분리 로직 유지 (17행)
# LLM 대상: 1675 - 17 = 1658행
# 배치 수:  ceil(1658 / 50) = 34배치  (기존 16배치 → 34배치)
# 총 LLM 호출: 34 × 5 에이전트 = 170회
```

- `CANCER_DEFAULTS` 로직 변경 없음
- 캐시 경로 변경 없음 (`extraction_cache/`)

### 3. `agents/use_application_extractor.md` (A3) — Source_Files 힌트 추가

Source_Files 컬럼의 파일명에서 연구 유형을 우선 추론하는 규칙을 프롬프트에 추가:

```
Source_Files 파일명 → use_class 우선 매핑:
  eye_irritation_rabbit*        → use_class: "study/eye_irritation"
  eye_irritation*               → use_class: "study/eye_irritation"
  skin_irritation_human*        → use_class: "study/skin_irritation"
  acute_dermal*                 → use_class: "study/acute_dermal"
  acute_oral*                   → use_class: "study/acute_oral"
  cancer*                       → use_class: "research/cancer_study" (CANCER_DEFAULTS 처리됨)
  (위 없을 때 기존 농약 use_class 로직 유지)
```

### 4. `agents/orchestrator.md` — 수치 갱신

| 항목 | 현재값 | 신규값 |
|------|------|------|
| 총 행 수 | 794 | **1,675** |
| Cancer_PID 행 | 17 | 17 (유지) |
| LLM 대상 | 777 | **1,658** |
| 배치 수 | 16 | **34** |
| 입력 소스 | enriched_queue.csv (from review_queue.csv) | enriched_queue.csv (from master xlsx) |
| sec_* 매칭률 | 71.7% (569/794) | ~34% (569/1675, MATCH행 sec_* 없음) |

---

## Assumptions Exposed & Resolved

| 가정 | 검증 방법 | 결론 |
|------|---------|------|
| MATCH 행에 원본 PDF가 있을 것 | 코드로 Audit_Source_File 확인 | 0% — 완전히 없음. 워크북+Evidence만 사용 |
| 1675행이 성분 레벨 확장 행일 것 | master xlsx 직접 확인 | 제형 레벨 1행/제형, 중복 없음 |
| "중복컬럼" = 데이터 구조 문제 | 인터뷰 | 동일 성분의 다른 카테고리 가능성 — 독립 처리 강조 |
| 독성 유형 분류가 우리 팀 업무 | 인터뷰 | 성분 추출은 다른 팀. 제형 특성만 우리 담당 |

---

## Technical Context (Brownfield)

```
/formulation/
├── formulation_ingredients_master_audited.xlsx   ← 신규 입력 소스 (1,675행)
├── artifacts/
│   ├── enriched_queue.csv                         ← 794행 → 1,675행으로 확장 예정
│   ├── formulation_characteristics_output.csv     ← 794행 → 1,675행 목표
│   └── extraction_cache/                          ← 캐시 16×5 → 34×5개
├── agents/
│   ├── use_application_extractor.md               ← Source_Files 힌트 추가
│   └── orchestrator.md                            ← 수치 갱신
└── workflow/
    ├── enrich_with_sections.py                    ← 입력 소스 교체 (핵심)
    └── extract_formulation_features.py            ← 배치 수 자동 대응
```

---

## Ontology (Key Entities)

| 엔티티 | 유형 | 핵심 필드 | 관계 |
|--------|------|---------|------|
| Formulation | core domain | Formulation_ID, Formulation_Name, Audit_Status | 1:1 행 대응 |
| Ingredient | supporting | CAS, name, pct | Formulation has many Ingredients (파이프 구분) |
| AuditRecord | supporting | Audit_Status, Evidence_Snippet, Highlighted_Source | Formulation has 1 AuditRecord |
| PDFSection | external | sec_signal_word, sec_h_codes, sec_formulation_code | 매칭된 Formulation에 조인 |
| ExtractionAgent | core domain | agent_key (type/hazard/use/regulatory/profile) | Batch처리 |
| StudyType | supporting | eye_irritation / skin_irritation / acute / cancer | Formulation_ID prefix + Source_Files에서 추론 |

---

## Interview Transcript
<details>
<summary>Full Q&A (5 rounds)</summary>

### Round 0 — 토폴로지 확인
**Q:** 3개 컴포넌트(데이터 범위 확장 / 성분 카테고리 처리 / 워크플로우 에이전트 정비) 맞나요?
**A:** 맞습니다 (3개 모두 진행)

### Round 1 — MATCH 행 소스 데이터
**Q:** MATCH 881행은 Audit_Source_File = 0%, Audit_Highlighted_Source_File = 0%. 어떤 소스 써야?
**코드 확인:** Audit_Source_File 0/881, Evidence_Snippet 860/881(97.6%), Ingredients 874/881(99.2%)
**A:** 코드로 확인 후 결정 → 결과: 워크북 컬럼만 사용 (원본 PDF 자체가 없음)
**모호도:** 66% → 46%

### Round 2 — 중복컬럼의 의미
**Q:** '중복컬럼이 있더라도 실제 성분은 다른 카테고리'가 무슨 상황?
**A:** 독성이 안독성인지, 피부독성인지, 급성독성인지 알 수 없어서
**코드 확인:** Formulation_ID 계열 = Cancer(17)/EyeIrritation6pack(613)/MIX(1045+)
**A(추가):** 성분 추출은 다른 팀. 우리는 formulation 부분에 특화
**모호도:** 46% → 36%

### Round 3 — 독성 유형 출력 형태
**Q:** 독성 유형 분류를 어떤 형태 출력?
**A:** 우리가 할 필요 없음 (다른 팀 담당)
**모호도:** 36% → 27%

### Round 4 — 입력 소스 결정
**Q:** 1,675행 처리를 위해 입력 소스를 어떻게 바꿔야 하나?
**A:** master xlsx 직접 읽기
**모호도:** 27% → 19.5%

### Round 5 — 정비 범위 최종 확정
**Q:** 워크플로우 정비 범위는?
**A:** Phase 0.5/1 입력 소스 교체 + A1-A5 에이전트 프롬프트 개선 + extract_formulation_features.py 수정 + orchestrator.md 기록 갱신 (4가지 모두)
**모호도:** 19.5% → 11%

</details>

---

*Status: pending approval*
