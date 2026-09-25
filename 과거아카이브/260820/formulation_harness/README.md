# SDS 리서치 하네스

농약/화학제품 리뷰 시트를 대상으로, 사용자가 맡은 역할(성분/제형코드/CAS넘버/물리화학적특성/독성정보)에
따라 SDS·라벨·제조사 웹사이트를 조사해 값을 채우고, 조사에 쓴 SDS가 어떤 정보를 담고 있는지
요약해주는 병렬 리서치 하네스입니다. **검증은 리서치가 끝난 뒤 사용자 승인을 받아야만 시작합니다.**

```
xlsx 시트 → [1] extract_targets.py → unique_products.json + row_mapping.json + fields.json
                                            ↓
                    [1.5] collect_pdfs.py (unstructured.io)  ← 선택 단계, PDF 미리 수집·파싱
                                            ↓
                          unique_products_with_pdfs.json (pdf_excerpts 필드 추가)
                                            ↓
                         [2] Workflow: sds-research.js  (리서치만, 자동 검증 없음)
                                            ↓
                                results.json  ← 사용자가 결과를 확인
                                            ↓
                    ⏸  사용자 승인 대기  ⏸  (승인 전에는 절대 진행하지 않음)
                                            ↓
                         [3] Workflow: sds-verify.js  (1차 전항목 검증 → 이상값 2차 재검증)
                                            ↓
                              final_results.json + anomaly_report
                                            ↓
                            [4] apply_results.py → xlsx 갱신
```

## 구성 파일

- `cipac_codes.json` — CIPAC 현행 66개 + CropLife 지원중단 30개, 총 96개 제형 코드/정의/한글명 정본.
- `field_specs.json` — 성분(Section 3)/물리화학적특성(Section 9)/독성정보(Section 11) 표준 체크리스트
  정본, 그리고 "더 나은 SDS" 판단 기준(완제형 여부, 성분·물리화학·독성 정보 유무 등).
- `../.claude/workflows/sds-research.js` — 리서치 전용 워크플로우. 위 두 참조 표를 스크립트 안에
  그대로 내장(Workflow는 파일시스템을 못 읽으므로 — **표를 고치면 세 파일을 함께 갱신**).
- `../.claude/workflows/sds-verify.js` — 검증 전용 워크플로우. **자동으로 실행되지 않음** — 리서치
  결과를 사용자가 보고 "검증 진행해" 라고 승인한 뒤에만 별도로 호출.
- `extract_targets.py` — xlsx에서 대상 행 추출·중복제거.
- `collect_pdfs.py` — [unstructured.io](https://unstructured.io)로 `tox_source_url`/
  `ingredient_source`/`supplemental_source_url`/`doc_source`/`source_url`에 걸린 SDS/라벨 PDF를
  미리 내려받아 텍스트로 파싱. 에이전트가 매번 직접 PDF를 웹패치하는 대신 미리 추출된 텍스트를
  참고하게 해서, Section 9(물리화학)/11(독성) 표처럼 라이브 스크래핑으로 놓치기 쉬운 내용을 보완.
- `apply_results.py` — 최종(검증 후) 결과를 xlsx에 반영. 컬럼이 없는 필드는 셀 코멘트로 남겨 정보
  손실 없이 보관(컬럼 설계는 추후 결정 사항).

## 사용법

### 1) 대상 추출

```bash
# 제형코드만 (기존 컬럼 기준으로 비어있는 행만)
python3 formulation_harness/extract_targets.py <xlsx> <시트명> --fields formulation_code

# 독성정보/물리화학적특성처럼 아직 컬럼이 없는 필드는 --all-rows로 전체 행을 대상으로
python3 formulation_harness/extract_targets.py <xlsx> <시트명> \
  --fields toxicity,physicochemical,cas_number --all-rows
```

### 1.5) (선택) PDF 미리 수집·파싱

```bash
pip install "unstructured[pdf]"   # 최초 1회
python3 formulation_harness/collect_pdfs.py formulation_harness/_run/unique_products.json
```

- `unique_products.json`의 URL 필드 중 `.pdf`로 끝나는 것만 골라 다운로드 후 unstructured로 텍스트
  추출. 같은 URL은 제품이 여러 개 참조해도 한 번만 받음.
- 결과: `_run/pdfs/*.pdf` + `*.txt`(파싱된 전체 텍스트) + `_run/pdfs/manifest.json`(URL별 상태),
  그리고 `_run/unique_products_with_pdfs.json`(원본 + 제품별 `pdf_excerpts` 필드 추가).
- 다음 단계(Workflow)에는 `unique_products.json` 대신 **`unique_products_with_pdfs.json`**을
  `args.products`로 넘길 것.
- 표(Table) 추출은 기본 `--strategy fast`에서는 잘 안 잡힐 수 있음 — 표가 중요한 경우
  `--strategy hi_res`(추가 ML 의존성 필요, 느림)를 시도.
- 다운로드/파싱 실패는 개별 URL 단위로 `manifest.json`에 에러만 기록하고 계속 진행(전체가 멈추지 않음).

### 2) 리서치 워크플로우 실행 (검증 없음)

```
Workflow({
  scriptPath: ".claude/workflows/sds-research.js",
  args: { products: <unique_products.json 또는 unique_products_with_pdfs.json 내용>,
          fields: ["formulation_code","toxicity"], batchSize: 8 }
})
```

- `pdf_excerpts`가 있는 제품은 에이전트가 그 텍스트를 1차 근거로 사용하고, 부족하면 평소처럼
  추가 웹서치로 보완하도록 안내.

- 배치마다 에이전트 1개가 요청된 필드를 전부 조사. SDS가 부실하면(단일물질뿐/성분·독성·물리화학 정보
  2개 이상 누락) 제조사 웹사이트 등으로 추가 서치해서 더 나은 출처로 교체하도록 지시되어 있음
  (`better_source_found`로 표시).
- 결과의 모든 항목에 `sds_summary`(성분정보O/X, 독성정보O/X, 제형정보O/X, 완제형 여부)가 항상 포함됨.
- **이 단계는 검증하지 않음.** 반환값을 `results.json`으로 저장하고 사용자에게 보여줄 것.

### 3) ⏸ 사용자 승인 후에만 검증 실행

사용자가 리서치 결과를 충분히 확인하고 "검증 시작"이라고 승인하면:

```
Workflow({
  scriptPath: ".claude/workflows/sds-verify.js",
  args: { results: <results.json 내용> }
})
```

- **1차 검증(Verify-1)**: 신뢰도와 무관하게 **전항목 100%**를 독립된 에이전트가 처음부터 다시
  조사해서 원래 값에 동의하는지 확인. `agrees=false` 또는 `anomaly=true`인 항목은 전부 flag.
- **2차 검증(Verify-2)**: 1차에서 flag된 항목만 세 번째 독립 조사로 최종 확정(tie-break).
- 반환값: `final_results`(최종 반영용) + `anomaly_report`(1차에서 뭐가 이상했고 2차에서 어떻게
  결론났는지 사람이 읽을 수 있는 요약) — **반영 전에 anomaly_report를 사용자에게 먼저 보여줄 것.**

### 4) xlsx에 반영

```bash
python3 formulation_harness/apply_results.py <xlsx> <시트명> final_results.json row_mapping.json
```

- `formulation_code`는 기존 컬럼(`formulation_type`/`formulation_code`/`formulation_type_ko`/
  `not_formulation_reason`)에 바로 반영.
- 그 외 필드(`ingredients`/`cas_number`/`physicochemical`/`toxicity`)는 대응 컬럼이 아직 없으므로
  **제품명 셀에 코멘트로 저장**(값 손실 없음). 컬럼이 정해지면 `--column-map column_map.json`으로
  매핑을 넘기면 그 컬럼에 바로 씀: `{"toxicity": {"col": "toxicity_info"}}`.
- `sds_summary.summary_ko`도 같은 방식(메모 컬럼 지정 시 그 컬럼, 아니면 코멘트).
- 출처는 항상 `formulation_src` 갱신 + 비어있는 `tox_source_url`/`ingredient_source`/
  `supplemental_source_url` 중 첫 칸에 파란 글씨로 추가(기존 값은 절대 덮어쓰지 않음).

## 핵심 규칙 (스크립트에 내장됨)

- **검증은 절대 자동으로 이어지지 않음.** 리서치 → 사용자 승인 → 검증. 이 게이트는 스크립트가 아니라
  호출하는 쪽(Claude/사용자 대화)에서 지켜야 함 — Workflow 자체에는 "일시정지" 기능이 없으므로
  research와 verify를 물리적으로 다른 두 워크플로우로 분리해 둔 것.
- **더 나은 SDS 탐색**: 처음 찾은 SDS가 단일물질용이거나 성분/물리화학/독성 중 2개 이상이 비어 있으면
  제조사 사이트·EPA 라벨·다른 SDS 애그리게이터로 계속 검색하도록 지시됨.
- SC vs SL, FS vs LS, PO vs SA, TC/TK가 정식 제형 코드라는 점 등 — formulation_harness 원본 규칙
  그대로 유지.
- CDPR 라벨 사이트가 다운되면 즉시 EPA/pomerix로 전환.
- `ingredient_source`/`tox_source_url` 힌트가 실제로 무관한 제품인 경우가 매우 잦음 — 항상 재확인.

## 검증된 테스트 (2026-08-13)

- `sds-research.js`: Roundup Concentrate Plus(제형+독성 요청) → SL 추정 + LD50/GHS 등 상세 독성정보
  + 완결성 요약 정상 반환. Xylenes (Mixed) → not_formulation(reagent) + 독성정보(GHS Danger, H226 등)
  정상 반환.
- `sds-verify.js`: 위 결과를 입력으로 1차 전항목 검증 실행 확인.
- 기존 `formulation_code` 단독 워크플로우(13개 실전 미해결 항목)로 9건 신규 해결 확인(270개 중 265개,
  98.1% 커버리지) — 회귀 없음.

## 알려진 제약

- Workflow 스크립트는 파일시스템/Node API 접근 불가 → 참조 표(`cipac_codes.json`, `field_specs.json`)를
  스크립트 안에 하드코딩. 표 갱신 시 정본 JSON과 두 워크플로우 스크립트를 모두 고칠 것.
- `args`가 문자열로 도달하는 경우가 있어 두 스크립트 모두 `typeof === 'string'`이면 `JSON.parse`하는
  방어 코드 포함.
- 물리화학적특성/독성정보/성분/CAS넘버는 아직 xlsx에 전용 컬럼이 없음 — 현재는 셀 코멘트로만 보관.
  컬럼 스키마가 정해지면 `apply_results.py --column-map`으로 연결.
- 출처가 정말 존재하지 않는 제품(사내 코드명 등)은 여러 라운드를 돌려도 `unresolved`로 남을 수 있음.
- `collect_pdfs.py`는 `unstructured`/`requests` 필요(`pip install "unstructured[pdf]" requests`).
  URL이 `.pdf`로 끝나지 않으면(리다이렉트·뷰어 페이지 등) 건너뜀 — 그런 경우는 여전히 에이전트의
  일반 웹서치에 의존.
