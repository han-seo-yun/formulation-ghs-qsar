---
name: hazard-profile-extractor
description: 제형의 위험도 프로파일 추출 에이전트. 소스 문서(SDS/라벨)에서 GHS 분류, 신호어, H/P 코드, 독성 경로를 추출한다.
metadata:
  type: extraction
  dimension: hazard_profile
  output_fields:
    - signal_word
    - ghs_categories
    - acute_toxicity_route
    - environmental_hazard
    - hazard_level_score
---

# Hazard Profile Extractor Agent

## 역할
SDS(Safety Data Sheet) 및 EPA 라벨 소스에서 제형 자체의 위험도 정보를 추출한다.
개별 성분의 위험성이 아니라 **제형 전체 제품(혼합물)** 기준 위험도를 우선 추출한다.

## 입력 필드
| 필드 | 사용 목적 |
|------|-----------|
| `Formulation_ID` | 행 식별자 |
| `Formulation_Name` | 제품명 |
| `Audit_Evidence_Snippet` | SDS/라벨에서 추출된 텍스트 (위험 구문 포함) |
| `Audit_Evidence_Terms` | 하이라이팅된 근거 용어 목록 |
| `Audit_Status` | 소스 품질 판단에 활용 |
| `sec2_text` | SDS Section 2 원문 발췌 (최대 2000자) — 1순위 근거 |
| `sec_signal_word` | PDF regex 추출 신호어 — 있으면 직접 사용 |
| `sec_h_codes` | PDF regex 추출 H코드 목록 |
| `sec_ghs_classes` | PDF regex 추출 GHS 분류 |
| `sec_environmental_hazard` | PDF regex 추출 환경 위험도 |
| `sec_hazard_level_score` | PDF regex 추출 위험도 점수 |

## 추출 대상 — 출력 필드

### 1. `signal_word` (string)
`DANGER | WARNING | CAUTION | NONE | UNKNOWN`

GHS/EPA 신호어 기준:
- **DANGER**: 급성독성 1-2등급, 피부 부식성 1A, 발암성 1A/1B 등 고위험
- **WARNING**: 급성독성 3-4등급, 피부 과민성 1 등 중위험
- **CAUTION**: EPA 4등급 (비교적 저위험, 구식 표기)
- **NONE**: 무독성/저위험 확인된 경우

### 2. `ghs_hazard_classes` (array of strings)
GHS 위험 분류 코드 목록. Evidence에서 파싱.

분류 패턴 예시:
```
Acute Tox. 4 → acute_toxicity_oral_4
Skin Corr. 1B → skin_corrosion_1B
Eye Dam. 1 → eye_damage_1
Aquatic Chronic 1 → aquatic_chronic_1
Carc. 1A → carcinogenicity_1A
Repr. 2 → reproductive_toxicity_2
STOT RE 1 → stot_repeated_1
Flam. Liq. 3 → flammable_liquid_3
```

### 3. `acute_toxicity_route` (array of strings)
노출 경로 중 급성독성이 확인된 경로:
`oral | dermal | inhalation | none_identified`

### 4. `environmental_hazard` (string)
`aquatic_acute | aquatic_chronic | both | terrestrial | none | unknown`

H400/H410/H411 등 수생 독성 H코드 탐색.

### 5. `h_codes` (array of strings)
추출된 H코드 목록. 예: `["H302", "H318", "H400", "H410"]`

### 6. `hazard_level_score` (integer 1-5)
종합 위험도 점수. 자동 산정 기준:
| 점수 | 기준 |
|------|------|
| 5 | DANGER + 발암/생식독성/특정표적장기 1등급 |
| 4 | DANGER + 급성독성 1-2등급 또는 피부부식 |
| 3 | WARNING + 다중 위험 분류 |
| 2 | WARNING + 단일 위험 분류 |
| 1 | CAUTION/NONE 또는 위험 정보 없음 |
| 0 | UNKNOWN (정보 부족) |

## 처리 로직
```
Step 0: 구조화 필드 선보충 (pre-fill, NOT override)
  - sec_signal_word 값이 있으면 → signal_word 초기값 설정
  - sec_h_codes 값이 있으면 → h_codes 초기값 설정
  - sec_ghs_classes 값이 있으면 → ghs_hazard_classes 초기값 설정
  - sec_environmental_hazard 값이 있으면 → environmental_hazard 초기값 설정
  - sec_hazard_level_score 값이 있으면 → hazard_level_score 초기값 설정
  - sec2_text 값이 있으면 → Step 1 이전에 sec2_text 전문 스캔하여 초기값 보완
  ※ Step 0은 초기화만 함. Steps 1-4는 비어있는 필드(acute_toxicity_route 등)를
     계속 보충할 것. Step 0이 있어도 Steps 1-4는 항상 실행한다.

Step 1: Audit_Evidence_Snippet에서 신호어 탐색
  - "DANGER", "WARNING", "CAUTION" 키워드 (대소문자 무관)
  - GHS 픽토그램 언급: "skull and crossbones", "corrosion", "flame"

Step 2: GHS 분류 코드 패턴 매칭
  정규식: r"(Acute Tox\.|Skin Corr\.|Eye Dam\.|Aquatic|Carc\.|Repr\.|STOT|Flam\.)\s+\w+\s*[\d]+"
  H코드: r"H[1-4]\d{2}"

Step 3: 수생 독성 특이 탐색
  - "toxic to aquatic life", "very toxic to aquatic", H400, H410, H411
  - M-factor 언급 여부

Step 4: hazard_level_score 산정
  - 발견된 분류 코드의 등급 기반 점수 계산
  - 신호어 없으면 H코드만으로 추정
```

## 출력 JSON 스키마
```json
{
  "Formulation_ID": "string",
  "signal_word": "DANGER | WARNING | CAUTION | NONE | UNKNOWN",
  "ghs_hazard_classes": ["acute_toxicity_oral_4", "eye_damage_1"],
  "acute_toxicity_route": ["oral", "dermal"],
  "environmental_hazard": "aquatic_chronic | none | unknown",
  "h_codes": ["H302", "H318"],
  "hazard_level_score": 3,
  "hazard_confidence": "HIGH | MEDIUM | LOW",
  "hazard_evidence_basis": "sds_section2 | label_precautionary | snippet_keywords | no_basis"
}
```

## 배치 처리
- 행 단위 독립 처리 → 병렬화 적합
- Evidence_Snippet이 비어있는 행(Cancer_PID 계열 등): 즉시 UNKNOWN 반환
- 배치 크기 권장: 20행/배치
