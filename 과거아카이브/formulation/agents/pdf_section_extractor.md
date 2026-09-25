---
name: pdf-section-extractor
description: PDF 구역 추출 에이전트 (Phase 0.5). highlighted/ 폴더의 PDF에서 Section별 텍스트를 추출하여 A1~A4 에이전트의 풍부한 입력 데이터를 생성한다. LLM 없이 규칙 기반으로 실행된다.
metadata:
  type: preprocessor
  requires_llm: false
  tool: pymupdf
  output: ingredient_source_audit/section_extracts/{source_id}_sections.json
---

# PDF Section Extractor Agent (Phase 0.5)

## 역할
이미 다운로드된 `highlighted/` PDF에서 **SDS(Safety Data Sheet) 섹션별 텍스트**를 추출한다.
현재 `Audit_Evidence_Snippet`은 Section 3 주변 200~500자만 담고 있어
제형 타입(A1), 위험도(A2), 규제(A4) 추출이 불가능했던 문제를 해결한다.

## 입력
```
ingredient_source_audit/highlighted/{source_id}_highlighted.pdf  (358개)
ingredient_source_audit/highlighted/{source_id}_highlighted.html (51개)
```

## 추출 대상 섹션

### SDS 표준 섹션 (GHS/OSHA 기준)
| 섹션 번호 | 내용 | 활용 에이전트 |
|----------|------|-------------|
| Section 1 | 제품 식별 (Product Identification) | A1, A4 |
| Section 2 | 위험성 (Hazard Identification) | A2 |
| Section 3 | 성분 (Composition) | A5 (기존) |
| Section 14 | 운송 정보 (Transport) | A2 보완 |
| Section 15 | 규제 정보 (Regulatory) | A4 |

### EPA 라벨 헤더 (SDS가 아닌 라벨 문서)
| 항목 | 패턴 | 활용 |
|------|------|------|
| 제품명 + 제형 코드 | "Imidan® WP", "Pro 2.0 EC" | A1 |
| EPA Reg No. | `EPA Reg. No. \d{3,6}-\d{1,6}` | A4 |
| 신호어 | DANGER/WARNING/CAUTION 블록 | A2 |
| Active Ingredient 선언 | "ACTIVE INGREDIENT(S):" 이하 | A4 |

## 섹션 경계 감지 로직

```python
SECTION_PATTERNS = {
    "section_1": [
        r"SECTION\s*1[\.\:]",
        r"Section\s*1[\.\:]",
        r"1\.\s*IDENTIFICATION",
        r"1\.\s*Product identifier",
        r"PRODUCT AND COMPANY IDENTIFICATION",
    ],
    "section_2": [
        r"SECTION\s*2[\.\:]",
        r"Section\s*2[\.\:]",
        r"2\.\s*Hazard",
        r"HAZARDS? IDENTIFICATION",
        r"GHS CLASSIFICATION",
    ],
    "section_3": [
        r"SECTION\s*3[\.\:]",
        r"3\.\s*Composition",
        r"COMPOSITION.{0,30}INFORMATION ON INGREDIENTS",
    ],
    "section_14": [
        r"SECTION\s*14[\.\:]",
        r"14\.\s*Transport",
        r"TRANSPORT INFORMATION",
    ],
    "section_15": [
        r"SECTION\s*15[\.\:]",
        r"15\.\s*Regulatory",
        r"REGULATORY INFORMATION",
    ],
    "label_header": [   # EPA 라벨 (섹션 구조 없는 경우)
        r"EPA Reg(?:istration)?\s*No",
        r"ACTIVE INGREDIENT",
        r"PRECAUTIONARY STATEMENTS",
    ],
}

# 다음 섹션 시작 전까지 텍스트 수집 (최대 2000자)
```

## 핵심 추출 항목 (규칙 기반)

### from Section 1
```python
# 제품명 + 제형 코드
product_name_line = first_line_after("Product Name|Trade name|Product identifier")
formulation_code_in_name = re.search(r'\b(EC|SC|WP|WG|WDG|GR|FS|EW|SL|SP|CS|RTU)\b', product_name_line)

# EPA Reg 번호
epa_reg = re.search(r'EPA\s*Reg(?:istration)?\s*(?:No\.?|#)?\s*[:\.]?\s*(\d{3,6}-\d{1,6})', section1_text)

# 캐나다 PCP 등록
pcp_reg = re.search(r'PCP\s*Reg(?:istration)?\s*No\.?\s*[:\.]?\s*(\d+)', section1_text)
```

### from Section 2
```python
# 신호어
signal_word = re.search(r'\b(DANGER|WARNING|CAUTION)\b', section2_text, re.IGNORECASE)

# H코드 전체
h_codes = re.findall(r'H[1-4]\d{2}', section2_text)

# GHS 분류 코드
ghs_classes = re.findall(
    r'(Acute Tox\.\s*\d|Skin Corr\.\s*\w+|Eye Dam\.\s*\d|'
    r'Aquatic (?:Acute|Chronic)\s*\d|Carc\.\s*\w+|'
    r'Repr\.\s*\d|STOT\s*(?:SE|RE)\s*\d|Flam\. Liq\.\s*\d|'
    r'Skin Sens\.\s*\d|Resp\. Sens\.\s*\d)',
    section2_text
)

# P코드 (예방/대응)
p_codes = re.findall(r'P[1-5]\d{2}', section2_text)
```

### from Section 14
```python
# UN 번호 (위험물 운송 등급)
un_number = re.search(r'UN\s*(\d{4})', section14_text)
# UN 3077 → 환경 위험물, UN 2588 → 농약 고체, UN 3352 → 농약 액체
```

## 출력 형식

```json
{
  "source_id": "07483aea85397703",
  "file_path": "ingredient_source_audit/highlighted/07483aea85397703_highlighted.pdf",
  "page_count": 10,
  "full_text_length": 20938,
  "sections_found": ["section_1", "section_2", "section_3"],
  "section_1": {
    "text": "Product identifier: Imidan® WP insecticide...",
    "product_name": "Imidan® WP insecticide",
    "formulation_code_extracted": "WP",
    "epa_reg_number": null,
    "pcp_reg_number": "29064",
    "registration_type": "PCP"
  },
  "section_2": {
    "text": "DANGER ... H301 H332 H317...",
    "signal_word": "DANGER",
    "h_codes": ["H301", "H332", "H317", "H320", "H411"],
    "ghs_classes": ["Acute Tox. 3", "Skin Sens. 1", "Aquatic Chronic 2"],
    "p_codes": ["P260", "P273", "P280"],
    "hazard_level_score": 4
  },
  "section_14": {
    "text": "UN 3077 ...",
    "un_number": "3077",
    "transport_hazard_class": "9"
  },
  "label_header": {
    "active_ingredient_declared": false,
    "epa_reg_number": null
  }
}
```

## 처리 통계 (예상)
| 항목 | 예상 수 |
|------|---------|
| 전체 PDF | 358개 |
| SDS 구조 있음 (section_found=True) | 315개 |
| Section 2 추출 성공 예상 | ~270개 (76%) |
| Section 1 추출 성공 예상 | ~290개 (81%) |
| EPA Reg 발견 예상 | ~60개 (17%) |
| formulation_code 발견 예상 | ~180개 (50%) |
| 처리 시간 (LLM 없음) | < 2분 (전체) |
