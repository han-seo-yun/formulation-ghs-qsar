---
name: use-application-extractor
description: 제형의 사용 목적 및 적용 정보 추출 에이전트. 제품명과 소스 텍스트에서 대상 생물/작물, 적용 방법, 적용 장소를 추출한다.
metadata:
  type: extraction
  dimension: use_application
  output_fields:
    - use_class
    - target_pest_organism
    - target_crop_site
    - application_method
    - use_pattern
---

# Use & Application Extractor Agent

## 역할
제형의 실제 사용 맥락을 추출한다. "무엇을 죽이는가", "어디에 쓰는가", "어떻게 쓰는가"를 제형 레벨에서 파악한다.

## 입력 필드
| 필드 | 사용 목적 |
|------|-----------|
| `Formulation_ID` | 행 식별자 |
| `Formulation_Name` | 제품명 (용도 힌트: "Insecticide", "Herbicide" 등) |
| `Formulation_Ingredients` | 활성성분명으로 용도 추정 (e.g., DEET → 방충) |
| `Audit_Evidence_Snippet` | 소스 문서 용도 설명 텍스트 |
| `Source_Files` | 소스 파일명으로 도메인 판단 |

## 추출 대상 — 출력 필드

### 1. `use_class` (string)
```
agricultural/insecticide        # 농업용 살충제
agricultural/herbicide          # 농업용 제초제
agricultural/fungicide          # 농업용 살균제
agricultural/seed_treatment     # 종자처리제
agricultural/other
veterinary                      # 동물용
public_health/insect_repellent  # 방충제 (인체)
public_health/disinfectant      # 소독제
wood_preservation               # 목재 보존
industrial/cleaning             # 산업용 세정
industrial/chemical             # 산업용 화학제
pharmaceutical                  # 의약품
research/cancer_study           # 암 연구용 (Cancer_PID 계열)
study/eye_irritation_rabbit     # 안독성 연구 (토끼)
study/eye_irritation            # 안독성 연구
study/skin_irritation_human     # 피부독성 연구 (인체)
study/skin_irritation           # 피부독성 연구
study/acute_dermal              # 급성 피부독성 연구
study/acute_oral                # 급성 경구독성 연구
research/toxicology             # 기타 독성 연구
unknown
```

### 2. `target_pest_organism` (array of strings | null)
방제 대상 해충/병원체 목록. 해당 없으면 null.

탐색 키워드 예시:
```
mosquitoes, ticks, termites, aphids, mites, beetles,
fungi, rust, blight, mold,
weeds, grasses, broadleaf,
bacteria, viruses
```

### 3. `target_crop_site` (array of strings | null)
적용 작물/장소. 해당 없으면 null.

탐색 패턴:
```
작물: corn, wheat, barley, rice, soybean, cotton, turf, ornamental
장소: residential, commercial, food handling, animal quarters, soil
```

### 4. `application_method` (string)
```
spray | drench | seed_treatment | bait | fumigation |
topical | systemic | soil_injection | unknown
```

### 5. `use_pattern` (string)
```
pre_emergent | post_emergent | preventive | curative |
systemic | contact | residual | unknown
```

## 처리 로직

```
Step 1: Source_Files 파일명 기반 연구 유형 우선 분류 (즉시 결정 — 이하 단계 불필요)
  Source_Files 컬럼의 파일명에 아래 패턴 포함 시 즉시 use_class 확정:
  - "cancer"                      → research/cancer_study
  - "eye_irritation_rabbit"       → study/eye_irritation_rabbit
  - "eye_irritation"              → study/eye_irritation
  - "skin_irritation_human"       → study/skin_irritation_human
  - "skin_irritation"             → study/skin_irritation
  - "acute_dermal"                → study/acute_dermal
  - "acute_oral"                  → study/acute_oral
  복수 패턴 매칭 시: 가장 구체적인 패턴 우선 (rabbit > human > 기본)
  Source_Files에 위 패턴 없음 → 다음 단계 (농약 용도 분류)

Step 2: Formulation_Name 키워드 매칭
  - "Insecticide", "Herbicide", "Fungicide", "Termiticide",
    "Repellent", "Preservative", "Fungicide" 등
  - 제품명 내 용도 코드: "FS" → seed treatment 가능성

Step 3: Formulation_Ingredients 성분 기반 추론
  활성성분 → 용도 매핑:
  - DEET (134-62-3) → insect_repellent
  - Glyphosate (1071-83-6) → herbicide
  - Cypermethrin (52315-07-8) → insecticide
  - Tebuconazole (107534-96-3) → fungicide
  - Carboxin (5234-68-4) → fungicide/seed_treatment
  - Chlorantraniliprole (500008-45-7) → insecticide
  - DDVP / Dichlorvos → insecticide

Step 4: Audit_Evidence_Snippet 텍스트 탐색
  - 대상 생물: "mosquitoes", "ticks", "fungi", "weeds"
  - 적용 작물/장소: "approved for N sites including..."
  - 적용 방법: "spray", "drench", "seed", "baiting"
  - "EPA Reg" 번호 확인 → 농약 등록 제품
```

## 출력 JSON 스키마
```json
{
  "Formulation_ID": "string",
  "use_class": "agricultural/insecticide",
  "target_pest_organism": ["mosquitoes", "ticks", "flies"],
  "target_crop_site": ["corn", "soybean"],
  "application_method": "spray",
  "use_pattern": "contact",
  "use_confidence": "HIGH | MEDIUM | LOW",
  "use_evidence_basis": "product_name | ingredient_inference | source_text | source_filename | no_basis"
}
```

## 특수 케이스
- `Cancer_PID_*`: CANCER_DEFAULTS 규칙 처리 → LLM 호출 없음
- `EyeIrritation6pack_*`: Source_Files = "eye_irritation_rabbit..." → Step 1에서 `study/eye_irritation_rabbit` 즉시 분류
- `MIX_PID_*`: Source_Files에 여러 연구 유형 혼재 가능 (예: "skin_irritation_human_subset..., acute_dermal..., acute_oral...") → 가장 구체적인 패턴 우선 적용
- 연구용 제형(`study/*`, `research/*`)은 `target_pest_organism`, `target_crop_site` = null, `application_method` = "unknown"

## 배치 처리
- 행 단위 독립 처리 → 병렬화 적합
- 배치 크기 권장: 50행/배치
