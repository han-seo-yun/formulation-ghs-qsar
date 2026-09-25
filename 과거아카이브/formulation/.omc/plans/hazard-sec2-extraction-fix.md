# Hazard Extraction 개선 계획
**Status: pending approval**
**작성: 2026-07-13 | Planner → Architect → Critic (3 iterations) → ACCEPT-WITH-RESERVATIONS**

---

## RALPLAN-DR 요약

**원칙**
1. 원본 데이터 소스(SDS 원문)를 추론보다 항상 우선한다
2. 수정 범위를 최소화하여 부작용을 줄인다
3. 실행 결과를 검증 가능하게 만든다

**Decision Drivers**
1. PDF 구조화 추출(sec_signal_word 등)이 이미 정확하나 프롬프트가 이를 무시함
2. sec2_text 300자 잘림으로 H코드 완전성 손실
3. 배치 크기 변경 시 캐시 키 충돌 위험 회피 필요

**ADR**
- Decision: 프롬프트 수정(즉효) → Phase 0.5 재실행(H코드 완전성) 2단계 적용
- Alternatives considered: 배치 sec2_text 분기 처리 → 캐시 키 충돌 위험으로 기각
- Consequences: hazard 에이전트가 구조화 필드를 1순위로 사용, sec2_text 2000자로 확장
- Follow-ups: sec_p_codes 활용 방안, label_header text_excerpt 연결 검토

---

## Step 0: 파일 변경 적용 (실행 전 필수)

### 1. `agents/hazard_profile_extractor.md`

**입력 필드 테이블에 추가:**
```markdown
| `sec2_text`                | SDS Section 2 원문 발췌 (최대 2000자) |
| `sec_signal_word`          | PDF regex 추출 신호어 |
| `sec_h_codes`              | PDF regex 추출 H코드 목록 |
| `sec_ghs_classes`          | PDF regex 추출 GHS 분류 |
| `sec_environmental_hazard` | PDF regex 추출 환경 위험도 |
```

**처리 로직 맨 앞 Step 0 삽입:**
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
```

**기존 배치 크기 줄 교체:**
```
배치 크기 권장: 50행/배치  →  배치 크기 권장: 20행/배치
```

### 2. `workflow/pdf_section_extractor.py`

2곳만 수정 (label_header:229는 enrich_with_sections.py가 읽지 않으므로 제외):
```python
# line 213
result["section_1"]["text_excerpt"] = sections["section_1"][:2000]  # [:300] 변경
# line 216
result["section_2"]["text_excerpt"] = sections["section_2"][:2000]  # [:300] 변경
```

### 3. `workflow/extract_formulation_features.py`

```python
BATCH_SIZE = 20  # line 39: 25 → 20
```

---

## 실행 순서

### Step 1: 프롬프트 수정 즉각 효과 확인 (API 비용 소량)

```bash
# hazard 에이전트 캐시만 삭제 (type/use/regulatory/profile 캐시 유지)
rm artifacts/extraction_cache/hazard_batch_*.json

# Phase 2 재실행
python workflow/extract_formulation_features.py

# 수락 기준 확인
python3 -c "
import pandas as pd
df = pd.read_csv('artifacts/formulation_characteristics_output.csv')
sw = (df['signal_word'] != 'UNKNOWN').mean()
lc = (df['hazard_confidence'] == 'LOW').mean()
print(f'signal_word 추출률: {sw:.1%} (목표 > 50%)')
print(f'hazard LOW 비율:    {lc:.1%} (목표 < 40%)')
"
```

### Step 2: sec2_text 확장 (Phase 0.5→1→1.5 재실행, API 비용 중간)

```bash
# Phase 0.5 출력 삭제 (없으면 기존 JSON skip)
rm ingredient_source_audit/section_extracts/*.json

# Phase 0.5 → 1 → 1.5 재실행 (--force: enriched_queue.csv skip-guard 우회)
# (모두 needs_api=False이므로 --no-llm 포함)
python workflow/run_pipeline.py --from-phase 0.5 --no-llm --force

# sec2_text 확장 확인 (LLM 실행 전 검증)
python3 -c "
import pandas as pd
df = pd.read_csv('artifacts/enriched_queue.csv')
print(f'sec2_text 중앙값: {df[\"sec2_text\"].str.len().median():.0f}자 (목표 > 300자)')
"

# sec1_text/sec2_text 변경 에이전트 캐시 삭제
# (use: sec_product_name은 구조 필드로 text_excerpt 무관 → 유지)
# (profile: sec* 입력 없음 → 유지)
rm artifacts/extraction_cache/hazard_batch_*.json
rm artifacts/extraction_cache/type_batch_*.json
rm artifacts/extraction_cache/regulatory_batch_*.json

# Phase 2 재실행
python workflow/extract_formulation_features.py
```

### Step 3: BATCH_SIZE=20 적용 (선택, 전체 캐시 삭제)

```bash
# 배치 경계 변경으로 기존 캐시 전체 무효화
rm artifacts/extraction_cache/*.json
python workflow/extract_formulation_features.py
```
> Step 1+2 효과 확인 후 필요성 판단. 전체 Phase 2 재실행 비용 발생.

---

## 수락 기준

| 기준 | 측정 방법 | 확인 시점 |
|------|-----------|-----------|
| signal_word 추출률 > 50% | `(df.signal_word != 'UNKNOWN').mean()` | Step 1 후 |
| hazard_confidence LOW < 40% | `(df.hazard_confidence == 'LOW').mean()` | Step 1 후 |
| sec2_text 중앙값 > 300자 | `df['sec2_text'].str.len().median()` | Phase 1 후 (`--force`) |
| 전체 행 보존 1,675행 | `len(df)` | 각 Step 후 |
