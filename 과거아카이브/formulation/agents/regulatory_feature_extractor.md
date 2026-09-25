---
name: regulatory-feature-extractor
description: 제형의 규제 특성 추출 에이전트. EPA 등록번호, REACH 등록 여부, 규제 관할권, 라벨 신호어 공식 지위를 추출한다.
metadata:
  type: extraction
  dimension: regulatory_features
  output_fields:
    - epa_reg_number
    - regulatory_jurisdiction
    - registration_status
    - active_ingredient_declared
    - reach_compliant
---

# Regulatory Feature Extractor Agent

## 역할
소스 문서(EPA 라벨, SDS, 제품 페이지)에서 제형의 규제 등록 및 컴플라이언스 정보를 추출한다.
이 정보는 제형이 공식 등록 제품인지, 어느 규제 체계 하에 있는지를 나타낸다.

## 입력 필드
| 필드 | 사용 목적 |
|------|-----------|
| `Formulation_ID` | 행 식별자 |
| `Formulation_Name` | 제품명 |
| `Ingredient_Source` | 소스 유형 (URL, parsed from name 등) |
| `Source_Files` | 소스 파일명 |
| `Audit_Source_File` | 실제 소스 파일 경로 |
| `Audit_Evidence_Snippet` | 규제 관련 텍스트 |
| `Audit_Evidence_Terms` | 하이라이팅된 근거 용어 (EPA Reg 번호 포함 가능) |
| `Supplemental_Source_URL` | 보조 소스 URL |

## 추출 대상 — 출력 필드

### 1. `epa_reg_number` (string | null)
EPA 등록 번호. 형식: `{registrant_code}-{product_number}` (예: `11556-174`, `8033-96`)

탐색 패턴:
```
정규식: r"EPA\s*Reg(?:istration)?\s*(?:No\.?|Number|#)?\s*[:\.]?\s*(\d{3,6}-\d{1,6})"
예시: "EPA Reg. No. 11556-174", "EPA Registration Number: 8033-96-3"
```

### 2. `regulatory_jurisdiction` (string)
```
US_EPA         # 미국 EPA 등록 농약
US_FDA         # 미국 FDA 규제 의약품/식품
EU_REACH       # EU REACH 등록
EU_BPR         # EU Biocidal Products Regulation
UK_REACH       # 영국 REACH
MULTI          # 복수 관할권
RESEARCH_ONLY  # 연구용 (규제 등록 없음)
UNKNOWN
```

탐색 근거:
- "EPA Registration" → US_EPA
- "REACH Regulation (EC) No 1907/2006" → EU_REACH
- "UK REACH Regulations SI 2019/758" → UK_REACH
- "For R&D use only" → RESEARCH_ONLY
- cancer.xlsx / eye_irritation 소스 → RESEARCH_ONLY

### 3. `registration_status` (string)
```
registered_active     # 유효 등록
registered_cancelled  # 취소된 등록
not_registered        # 미등록 (연구용 등)
unknown
```

판단 근거:
- EPA 등록번호 발견 + "originally approved" 언급 → `registered_active`
- "originally approved" + 만료 일자 → 만료 여부 추가 확인
- 소스가 없거나 cancer.xlsx 출처 → `not_registered`

### 4. `active_ingredient_declared` (boolean | null)
소스 문서에서 "ACTIVE INGREDIENT(S)" 섹션이 명시적으로 선언되었는가.

- True: "ACTIVE INGREDIENTS:", "Active Ingredient:", "* Active ingredient" 패턴 발견
- False: 성분 목록은 있으나 active 구분 없음
- null: 소스 없음

### 5. `reach_registered` (boolean | null)
EU REACH 등록 언급 여부.

탐색:
- "Registration number" + "01-" 형식 (REACH 등록번호 형식: 01-XXXXXXXXXX-XX-XXXX)
- "REACH Regulation" 언급

### 6. `label_type` (string)
소스 문서 유형:
```
epa_label        # EPA 공식 라벨 (규제 정보 가장 신뢰)
sds_full         # 전체 SDS (섹션 1-16)
sds_partial      # 부분 SDS
product_page     # 제조사/유통사 제품 페이지
pubchem          # PubChem 화학 정보 페이지
chemblink_sigma  # ChemBlink/Sigma SDS
worldbank_doc    # 비관련 문서 (잘못 매칭)
unknown
```

판단 근거:
- `Audit_Source_File` 경로의 소스 ID prefix 또는 ordered_option_runs 파일명
- `Source_Files` 내 "epa_label", "product_page" 분류
- URL 도메인: agrian.com → EPA label, sigma-aldrich.com → Sigma SDS

## 처리 로직

```
Step 1: Source_Files 도메인으로 cancer/eye_irritation 판별
  → RESEARCH_ONLY + not_registered 즉시 설정

Step 2: Audit_Evidence_Snippet에서 EPA Reg 번호 정규식 탐색
Step 3: REACH 관련 텍스트 탐색
Step 4: "ACTIVE INGREDIENT" 선언 탐색
Step 5: 라벨 유형 분류 (파일 경로 + URL 기반)
Step 6: 종합 registration_status 판정
```

## 출력 JSON 스키마
```json
{
  "Formulation_ID": "string",
  "epa_reg_number": "11556-174 | null",
  "regulatory_jurisdiction": "US_EPA | EU_REACH | RESEARCH_ONLY | UNKNOWN",
  "registration_status": "registered_active | not_registered | unknown",
  "active_ingredient_declared": true,
  "reach_registered": false,
  "label_type": "epa_label | sds_full | product_page | unknown",
  "regulatory_confidence": "HIGH | MEDIUM | LOW",
  "regulatory_evidence_basis": "epa_reg_found | reach_text | source_filename | no_basis"
}
```

## 배치 처리
- 행 단위 독립 처리 → 병렬화 적합
- EPA Reg 번호 발견 시 HIGH confidence 즉시 설정
- 배치 크기 권장: 50행/배치
