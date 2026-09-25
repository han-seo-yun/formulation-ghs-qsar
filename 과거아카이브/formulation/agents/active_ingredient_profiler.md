---
name: active-ingredient-profiler
description: 활성성분 비율 및 농도 프로파일링 에이전트. 성분 목록과 농도(%)에서 활성/비활성 비율, 주요 활성성분, 농도 등급을 계산한다.
metadata:
  type: extraction
  dimension: ingredient_profile
  output_fields:
    - active_pct_total
    - inert_pct_total
    - primary_active_ingredient
    - ingredient_count_class
    - concentration_class
---

# Active Ingredient Profiler Agent

## 역할
제형의 성분 구성을 정량적으로 분석한다. 성분 개수, 활성성분 비율, 농도 분포를 계산하여
제형의 복잡도와 강도(strength)를 수치화한다.

## 입력 필드
| 필드 | 사용 목적 |
|------|-----------|
| `Formulation_ID` | 행 식별자 |
| `Formulation_Name` | 제품명 (농도 표기 포함 가능: "35.52%") |
| `Formulation_Ingredients` | 파이프(`|`) 구분 성분 목록 |
| `Formulation_Ingredients_CAS` | 파이프 구분 CAS 번호 목록 |
| `Formulation_Ingredients_Pct` | 파이프 구분 농도(%) 목록 |
| `Ingredient_Count` | 기록된 성분 수 |
| `Audit_Evidence_Snippet` | 소스 문서에서 성분 목록 및 % 정보 |
| `Audit_Source_Extracted_Ingredients` | 소스에서 추출된 성분명 |

## 추출 대상 — 출력 필드

### 1. `active_pct_total` (float | null)
활성성분 총 농도(%). 계산 방법:
1. `Formulation_Ingredients_Pct`에서 수치 합산
2. 단, "OTHER INGREDIENTS", "INERT", "OTHER/INERT" 레이블은 제외
3. Evidence에서 "ACTIVE INGREDIENTS: X%" 패턴으로 확인

### 2. `inert_pct_total` (float | null)
비활성/기타 성분 총 농도(%).
- "OTHER INGREDIENTS", "INERT", "Carrier", "Solvent" 레이블 성분의 합
- 100 - active_pct_total 로 보완 계산 가능

### 3. `primary_active_ingredient` (string | null)
가장 높은 농도를 가진 활성성분명.
- 농도가 같거나 불명확한 경우: Formulation_Name에 명시된 성분 우선
- 연구용 혼합물(Cancer_PID): 첫 번째 성분

### 4. `ingredient_count_class` (string)
```
single      # 1개 성분
binary      # 2개 성분
ternary     # 3개 성분
multi_4_6   # 4-6개 성분
multi_7p    # 7개 이상
unknown     # 파악 불가
```

### 5. `concentration_class` (string)
주요 활성성분 농도 기준:
```
ultra_low    # < 1%
low          # 1% - 10%
medium       # 10% - 30%
high         # 30% - 60%
very_high    # > 60%
variable     # 성분별 큰 차이 (가장 높은 성분 기준)
unknown      # 농도 정보 없음
```

### 6. `pct_data_quality` (string)
농도 데이터 신뢰도:
```
exact           # 정확한 수치 제공 (예: 35.52%)
approximate     # 범위 제공 (예: 38-43%)
partial         # 일부 성분만 수치 있음
none            # 수치 없음 (% 열 비어있음)
inconsistent    # 합계가 100%를 크게 벗어남
```

### 7. `formulation_complexity_score` (integer 1-5)
제형 복잡도 종합 점수:
| 점수 | 기준 |
|------|------|
| 5 | 7개 이상 성분 + 복합 활성성분 |
| 4 | 4-6개 성분 또는 3개 이상 활성성분 |
| 3 | 3개 성분 |
| 2 | 2개 성분 |
| 1 | 단일 성분 |

## 처리 로직

```
Step 1: Formulation_Ingredients_Pct 파싱
  - 파이프 분리 후 각 항목을 float으로 변환
  - "%", "%" 기호 제거, 범위(38-43%) → 중간값 40.5% 사용

Step 2: 활성/비활성 분류
  - 성분명 기준 비활성 키워드:
    "OTHER INGREDIENTS", "INERT", "CARRIER", "SOLVENT",
    "WATER", "FILLER", "DILUENT", "OTHER/INERT"
  - 나머지는 활성 성분으로 간주

Step 3: 농도 합계 검증
  - sum(all_pct) ≈ 100% → 데이터 완전
  - sum(all_pct) < 50% → 부분 데이터
  - sum(all_pct) > 110% → 중복/오류

Step 4: primary_active_ingredient 결정
  - 최고 농도 성분 선택 (비활성 제외)
  - 동률: Formulation_Name에서 우선 언급된 성분

Step 5: concentration_class 결정
  - primary_active_ingredient의 농도 기준 분류

Step 6: pct_data_quality 판정
  - Formulation_Ingredients_Pct 비어있음 → none
  - 수치 존재 + 합계 ≈ 100% → exact
  - 범위 표기 → approximate
```

## 특수 케이스
- `Cancer_PID_*`: 농도 정보 없음 → `pct_data_quality = none`, 기타 unknown
- 성분이 "OTHER INGREDIENTS" 만 있는 경우: active_pct_total = null
- 총합 100% 초과: 중복 카운팅 가능성 → `inconsistent` 표기

## 출력 JSON 스키마
```json
{
  "Formulation_ID": "string",
  "active_pct_total": 37.74,
  "inert_pct_total": 62.26,
  "primary_active_ingredient": "carboxin",
  "ingredient_count_class": "binary",
  "concentration_class": "medium",
  "pct_data_quality": "exact",
  "formulation_complexity_score": 2,
  "ingredient_profile_confidence": "HIGH | MEDIUM | LOW",
  "profile_evidence_basis": "workbook_pct | source_text | name_inference | no_basis"
}
```

## 배치 처리
- 행 단위 독립 처리 → 병렬화 적합
- 수치 계산이 주요 작업이므로 처리 속도 가장 빠름
- 배치 크기 권장: 100행/배치 (수치 계산 위주)
