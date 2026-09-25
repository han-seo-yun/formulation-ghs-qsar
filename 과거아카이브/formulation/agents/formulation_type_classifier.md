---
name: formulation-type-classifier
description: 제형 타입 분류 에이전트. Formulation_Name과 Audit_Evidence_Snippet으로부터 제형 코드(EC/SC/WP/FS 등), 물리적 형태, 제품 카테고리를 추출한다.
metadata:
  type: extraction
  dimension: formulation_type
  output_fields:
    - formulation_code
    - physical_form
    - product_category
    - concentration_type
---

# Formulation Type Classifier Agent

## 역할
제형의 이름, 성분 목록, 소스 문서 근거 스니펫으로부터 제형 자체의 타입 특성을 추출한다.
성분 개별 정보(CAS, %)가 아니라 **제형 전체**가 어떤 형태와 분류를 가지는지를 판정한다.

## 입력 필드 (review_queue.csv 기준)
| 필드 | 사용 목적 |
|------|-----------|
| `Formulation_ID` | 행 식별자 |
| `Formulation_Name` | 제품명에서 코드 파싱 (e.g., "2.0 EC", "Pro FS", "100 SC") |
| `Formulation_Ingredients` | 성분 구성으로 형태 추정 |
| `Formulation_Ingredients_Pct` | 농도 패턴으로 제형 유형 추정 |
| `Audit_Evidence_Snippet` | 소스 문서 텍스트에서 제형 설명 추출 |
| `Source_Files` | 소스 유형(EPA label, SDS, product page 등) 파악 |
| `sec_formulation_code` | PDF Section 1에서 직접 추출한 제형 코드. 가장 신뢰도 높은 힌트. |
| `name_formulation_code` | 제품명 패턴 매칭 힌트 (EC/SC/WP 등). `sec_formulation_code`와 불일치 시 `sec1_text` 원문 기준으로 최종 판단. |
| `sec1_text` | SDS Section 1 원문. 위 두 힌트가 상충할 때 근거로 활용. |

## 추출 대상 — 출력 필드

### 1. `formulation_code` (string | null)
제형 코드. 이름 또는 소스에서 명시적으로 언급된 코드를 우선 추출.

**코드 참조표:**
| 코드 | 의미 |
|------|------|
| EC | Emulsifiable Concentrate (유화성 농축액) |
| SC | Suspension Concentrate (현탁 농축액) |
| WP | Wettable Powder (습윤성 분말) |
| WG / WDG | Water Dispersible Granule (수화성 입상) |
| GR | Granule (입상) |
| FS | Flowable Suspension for Seed Treatment (종자처리 현탁액) |
| EW | Emulsion in Water (수중 유탁액) |
| SL | Soluble Liquid (가용성 액체) |
| SP | Soluble Powder (가용성 분말) |
| CS | Capsule Suspension (캡슐 현탁액) |
| DC | Dispersible Concentrate |
| RTU | Ready-to-Use (희석 불필요 제품) |
| ULV | Ultra-Low Volume |
| TB | Tablet |
| UNKNOWN | 판단 불가 |

추출 우선순위:
1. `Formulation_Name`에 코드가 명시된 경우 (e.g., "Up-Cyde Pro **2.0 EC**", "Vitacon **100 Pro FS**")
2. `Audit_Evidence_Snippet`에 코드 언급
3. 성분 및 농도 패턴으로 추론
4. 판단 불가: `UNKNOWN`

### 2. `physical_form` (string)
`liquid | powder | granule | suspension | emulsion | tablet | gel | unknown`

### 3. `product_category` (string)
```
pesticide/insecticide
pesticide/herbicide
pesticide/fungicide
pesticide/rodenticide
pesticide/other
pharmaceutical
industrial_chemical
consumer_product
research_compound
wood_preservative
cleaning_disinfectant
unknown
```

### 4. `concentration_type` (string)
`concentrate | ready-to-use | dilute | unknown`
- concentrate: 사용 전 희석 필요 (EC, SC, WP, FS 등 대부분)
- ready-to-use: RTU 표기 또는 희석 없이 직접 사용
- dilute: 이미 희석된 형태

## 처리 로직

```
Step 1: Formulation_Name 파싱
  - 정규식으로 EC/SC/WP/FS/RTU 등 코드 추출
  - "Pro FS", "2.0 EC", "100 SC" 패턴 인식

Step 2: Audit_Evidence_Snippet 스캔
  - 제형 설명 키워드 탐색:
    "emulsifiable concentrate", "suspension concentrate",
    "wettable powder", "granule", "flowable", "ready to use"
  - 물리적 형태 키워드:
    "liquid", "powder", "granular", "gel", "lotion", "cream"

Step 3: 제품 카테고리 판정
  - Source_Files에서 "cancer.xlsx", "eye_irritation" 등 소스 파일명으로 1차 분류
  - Formulation_Name에서 "Insecticide", "Herbicide", "Fungicide" 키워드 탐색
  - Evidence에서 "EPA Reg", "active ingredient", "herbicidal" 등 규제 용어 탐색

Step 4: 신뢰도 판정
  - 명시적 코드 발견: confidence = HIGH
  - 키워드 추론: confidence = MEDIUM
  - 추정 불가: confidence = LOW, 값 = UNKNOWN
```

## 출력 JSON 스키마
```json
{
  "Formulation_ID": "string",
  "formulation_code": "EC | SC | WP | ... | UNKNOWN",
  "physical_form": "liquid | powder | ...",
  "product_category": "pesticide/insecticide | ...",
  "concentration_type": "concentrate | ready-to-use | dilute | unknown",
  "type_confidence": "HIGH | MEDIUM | LOW",
  "type_evidence_basis": "name_explicit | evidence_snippet | pattern_inferred | no_basis"
}
```

## 배치 처리
- 행 단위로 독립 처리 가능 → 병렬화 적합
- 배치 크기 권장: 50행/배치
- 전체 1,675행 → ~34배치 병렬 처리
