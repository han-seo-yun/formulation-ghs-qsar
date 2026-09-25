---
title: Formulation Characteristics Extraction Pipeline
version: 2.0
date: 2026-07-10
status: active
---

# 제형 특성 추출 파이프라인 설계

## 1. 배경 및 목적

### 현재 상태 (As-Is)
```
소스 문서 (PDF/HTML)
      ↓
성분(ingredient) 레벨 하이라이팅
      ↓
CAS 번호 / 성분명 검증 (audit_status)
      ↓
review_queue.csv — Evidence_Snippet: Section 3 주변 200~500자만
```

현재 시스템은 **성분이 소스에 존재하는지** 만 확인함.
제형이 **무엇인지, 얼마나 위험한지, 어디에 쓰는지** 는 추출하지 않음.

**데이터 가용성 분석 결과 (794행 기준):**
| 추출 차원 | Evidence_Snippet만으로 가능 | PDF 재파싱 후 가능 |
|---------|--------------------------|-----------------|
| A1 제형 타입 (formulation_code) | 8.3% | ~50% |
| A2 위험도 (H코드, 신호어) | 14.4% | ~76% |
| A3 사용 목적 | ~60% | ~60% (변화 없음) |
| A4 규제 (EPA Reg) | 1.9% | ~17% |
| A5 성분 프로파일 | 40% (%) / 95% (명) | 변화 없음 |

→ A1/A2/A4는 PDF를 Section별로 재파싱해야 유의미한 추출이 가능함.

### 목표 (To-Be)
```
review_queue.csv (기존 성분 감사 결과)
      ↓
[Phase 0.5] PDF Section Extractor  ← 추가됨
highlighted/ PDF 358개 → section_extracts/ JSON
      ↓
section_extracts + review_queue 조인
      ↓  ↓  ↓  ↓  ↓  (5개 에이전트 병렬)
 제형타입  위험도  용도  규제  성분프로파일
      ↓
formulation_characteristics_output.csv
      ↓
master workbook 컬럼 통합
```

---

## 2. 에이전트 역할 분담

```
┌─────────────────────────────────────────────────────────────────┐
│                    입력: review_queue.csv                        │
│                    (1,675개 제형 행)                              │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 배치 분할 (50행/배치)
          ┌─────────────────┼─────────────────────────────────┐
          │                 │                                  │
          ▼                 ▼                                  ▼
  ┌───────────────┐ ┌───────────────┐                ┌───────────────┐
  │  A1: 제형타입  │ │  A2: 위험도   │      ...       │  A5: 성분프로파일│
  │  분류기        │ │  추출기       │                │              │
  └───────┬───────┘ └───────┬───────┘                └───────┬───────┘
          │                 │                                  │
          └─────────────────┼──────────────────────────────────┘
                            │ Formulation_ID 기준 JOIN
                            ▼
                 ┌──────────────────────┐
                 │ 오케스트레이터 병합   │
                 └──────────┬───────────┘
                            │
                            ▼
              formulation_characteristics_output.csv
```

| 에이전트 | 파일 | 추출 차원 | 핵심 출력 |
|---------|------|-----------|-----------|
| A1 | `formulation_type_classifier.md` | 제형 형태 | formulation_code, physical_form, product_category |
| A2 | `hazard_profile_extractor.md` | 위험도 | signal_word, h_codes, hazard_level_score |
| A3 | `use_application_extractor.md` | 사용 목적 | use_class, target_pest, application_method |
| A4 | `regulatory_feature_extractor.md` | 규제 정보 | epa_reg_number, regulatory_jurisdiction |
| A5 | `active_ingredient_profiler.md` | 성분 구성 | active_pct_total, concentration_class |
| O | `orchestrator.md` | 통합 조율 | 병합 + 출력 파일 생성 |

---

## 3. 배치 처리 전략

### 3.1 배치 설계
```
전체 행: 1,675
배치 크기: 50행
총 배치 수: 34배치 (마지막 배치: 25행)
```

### 3.2 병렬화 구조
```
시간 →
배치 1: [A1][A2][A3][A4][A5] ─── 완료
배치 2:          [A1][A2][A3][A4][A5] ─── 완료
배치 3:                   [A1][A2][A3][A4][A5] ─── 완료
```

- 같은 배치 내 5개 에이전트: **완전 병렬** (독립적)
- 배치 순서: **순차** (메모리/비용 제어)
- 예상 총 처리 시간: ~15-20분

### 3.3 입력 분기 전략 (소스 타입별)
```
Cancer_PID_* 계열 (17행):
  → Evidence_Snippet 없음
  → A1: product_category = research/cancer_study
  → A2: hazard_level_score = 0 (UNKNOWN)
  → A3: use_class = research/cancer_study
  → A4: registration_status = not_registered
  → A5: pct_data_quality = none
  → 즉시 처리 (LLM 호출 최소화)

EyeIrritation6pack_PID* 계열 (794+ 행):
  → Evidence_Snippet 풍부
  → 전체 추출 파이프라인 실행
  → 원 제품 특성 추출 (연구 목적이지만 실제 제품)

기타 (나머지):
  → Evidence_Snippet 풍부
  → 전체 추출 파이프라인 실행
```

---

## 4. 데이터 흐름 상세

### 4.1 입력 → 에이전트 매핑 (PDF 재파싱 이후)
```
Formulation_Name ──────────────────→ A1(타입), A3(용도)
Formulation_Ingredients ───────────→ A1, A3, A5
Formulation_Ingredients_Pct ───────→ A5(프로파일)
Source_Files ──────────────────────→ A3(용도), A4(규제)
Audit_Evidence_Snippet ────────────→ A1, A2, A3, A4
sec_formulation_code (PDF) ────────→ A1 우선 활용
sec_signal_word / sec_h_codes (PDF)→ A2 우선 활용
sec_epa_reg_number (PDF) ──────────→ A4 우선 활용
sec_product_name (PDF) ────────────→ A1, A3 보완
sec2_text (PDF Section 2 원문) ────→ A2 상세 분석
```

### 4.2 실제 데이터 커버리지 (enriched_queue 기준, 794행)
| 필드 | 커버리지 | 에이전트 |
|------|---------|---------|
| sec_signal_word | 43.5% | A2 |
| sec_hazard_level_score | 56.0% | A2 |
| sec_h_codes | 29.1% | A2 |
| sec_product_name | 51.4% | A1 |
| sec_formulation_code | 16.5% | A1 |
| sec_epa_reg_number | 5.3% | A4 |
| Formulation_Ingredients (명) | 95.0% | A5 |
| Formulation_Ingredients_Pct | 40.8% | A5 |

---

## 5. 스킬 사용 스케줄

파이프라인의 각 단계에서 어떤 스킬을 언제 사용하는지를 정의한다.

```
Phase 0.5  PDF Section Extractor  ────────────────── (스킬 없음, 규칙 기반)
Phase 1    Enrich with Sections   ────────────────── (스킬 없음, 데이터 조인)
           │
           ▼
     [deep-research]  ← 여기서 한 번
           │
Phase 2    LLM 5-Agent Extraction ─── [ultrawork]
           │
     [verify]  ← Phase 2 완료 직후
           │
Phase 3    Master Workbook 통합 ──── [document-skills:xlsx]
           │
     [dataviz]  ← 최종 리포팅
```

### `/deep-research` — Phase 2 직전 (1회)
**목적**: A3(사용목적) 에이전트의 성분명→용도 매핑 지식 베이스 구축

**트리거 조건**: Phase 1 완료 후, Phase 2 LLM 실행 전

**수행 내용**:
```
enriched_queue에서 고유 활성성분명 추출 (~50-80개)
  → deep-research로 각 성분의 용도/대상/작용기전 조사
  → agents/ingredient_use_mapping.json 생성
  → A3 에이전트 프롬프트에 참조 테이블로 주입
```

**기대 효과**: A3 use_class 커버리지 60% → 85%+

**실행 명령**:
```
/deep-research "농약 활성성분 용도 매핑: Cypermethrin, Abamectin,
Tebuconazole, Glyphosate, Chlorantraniliprole 등 50개 성분의
use_class(agricultural/insecticide 등), target_pest, application_method 정리"
```

---

### `/oh-my-claudecode:ultrawork` — Phase 2 (LLM 배치 실행)
**목적**: 34배치 × 5에이전트 = 170개 LLM 호출을 최대 병렬로 처리

**트리거 조건**: deep-research 완료 후, ANTHROPIC_API_KEY 설정됨

**수행 내용**:
```
extract_formulation_features.py 의 배치 루프를
ultrawork가 병렬 태스크로 분해하여 실행
```

**기대 효과**: 순차 실행 ~68분 → 병렬 실행 ~10-15분

**실행 명령**:
```
/oh-my-claudecode:ultrawork "workflow/extract_formulation_features.py 의
34개 배치를 각 배치별로 5개 에이전트 병렬 실행.
캐시(extraction_cache/) 활용해 중단 재시작 가능하게"
```

---

### `/verify` — Phase 2 완료 직후
**목적**: formulation_characteristics_output.csv 품질 기준 충족 여부 자동 검증

**트리거 조건**: output.csv 생성 직후

**검증 항목**:
```
1. 전체 794행 모두 존재하는가
2. signal_word UNKNOWN 비율 < 30%  (sec_* 덕분에 목표 달성 가능)
3. use_class 추출률 ≥ 85%
4. Cancer_PID 계열: use_class = research/cancer_study 100%
5. formulation_complexity_score 분포 이상치 없는가
6. active_pct_total 합계 > 100% 인 행 수 (오류 감지)
```

**실행 명령**: `/verify` (자동으로 output.csv 분석)

---

### `/document-skills:xlsx` — Phase 3 (마스터 통합)
**목적**: output.csv를 `formulation_ingredients_master_audited.xlsx`에 새 시트로 병합

**트리거 조건**: verify 통과 후

**수행 내용**:
```
formulation_ingredients_master_audited.xlsx
  + 새 시트 "Formulation_Characteristics"
    ← formulation_characteristics_output.csv 데이터
  + 기존 시트와 Formulation_ID 기준 연결 (하이퍼링크/VLOOKUP)
```

**실행 명령**:
```
/document-skills:xlsx "formulation_characteristics_output.csv를
formulation_ingredients_master_audited.xlsx에
'Formulation_Characteristics' 시트로 추가하고
Formulation_ID 기준으로 기존 시트와 연결"
```

---

### `/dataviz` — 최종 리포팅
**목적**: 추출 결과 시각화 대시보드 생성

**트리거 조건**: xlsx 통합 완료 후

**차트 목록**:
```
1. 제형 타입 분포 (formulation_code 파이차트)
2. 위험도 점수 분포 (hazard_level_score 히스토그램)
3. 사용목적 분류 (use_class 막대그래프)
4. 에이전트별 커버리지 비교 (Before/After PDF재파싱)
5. active_pct_total 분포 (박스플롯)
```

**실행 명령**: `/dataviz "formulation_characteristics_output.csv 기반 품질 대시보드"`

---

## 6. 파일 구조 (현재 상태)

```
/formulation/
├── agents/
│   ├── formulation_type_classifier.md     ← A1 ✓
│   ├── hazard_profile_extractor.md        ← A2 ✓
│   ├── use_application_extractor.md       ← A3 ✓
│   ├── regulatory_feature_extractor.md    ← A4 ✓
│   ├── active_ingredient_profiler.md      ← A5 ✓
│   ├── pdf_section_extractor.md           ← Phase 0.5 ✓
│   └── orchestrator.md                   ← 통합 조율 ✓
│
├── workflow/
│   ├── formulation_characteristics_pipeline.md  ← 이 문서 ✓
│   ├── pdf_section_extractor.py           ← Phase 0.5 구현 ✓ (실행 완료)
│   ├── enrich_with_sections.py            ← Phase 1 구현 ✓ (실행 완료)
│   ├── extract_formulation_features.py    ← Phase 2 구현 ✓ (대기 중)
│   └── run_pipeline.py                   ← 마스터 자동화 ✓
│
└── artifacts/
    ├── enriched_queue.csv                 ← Phase 1 출력 ✓ (794행 × 31컬럼)
    ├── extraction_cache/                  ← Phase 2 캐시 (LLM 호출 중단 복구용)
    └── formulation_characteristics_output.csv  ← Phase 2 출력 (생성 예정)
│
└── ingredient_source_audit/
    ├── highlighted/     ← 원본 PDF/HTML 411개 (수정 없음)
    └── section_extracts/ ← Phase 0.5 출력 ✓ (411개 JSON)
```

---

## 7. 실행 순서 요약

```
[현재 완료]
  ✓ Phase 0.5: pdf_section_extractor.py  (411개 JSON)
  ✓ Phase 1:   enrich_with_sections.py   (enriched_queue.csv, 794행)

[다음 단계 — 순서 중요]
  1. /deep-research  → ingredient_use_mapping.json 생성
  2. A3 에이전트 프롬프트에 매핑 테이블 주입 (수동 편집)
  3. ANTHROPIC_API_KEY 설정
  4. /oh-my-claudecode:ultrawork  → Phase 2 병렬 실행
  5. /verify  → 품질 검증
  6. /document-skills:xlsx  → 마스터 워크북 통합
  7. /dataviz  → 결과 시각화
```

---

## 8. 성공 기준 (794행 기준, 실측값 반영)

| 지표 | PDF 재파싱 전 | PDF 재파싱 후 | Phase 2 목표 |
|------|-------------|-------------|------------|
| 신호어 커버리지 | 4.8% | 43.5% | ≥ 70% |
| H코드 커버리지 | 14.4% | 29.1% | ≥ 50% |
| 제형 코드 | 8.3% | 16.5% | ≥ 40% |
| use_class 분류 | ~60% | ~60% | ≥ 85% (deep-research 후) |
| EPA Reg | 1.9% | 5.3% | ≥ 10% |
| 전체 행 커버 | — | 71.7% 매칭 | 100% (LLM fallback) |
