# 산출물 지도 (INDEX)

제형 독성(눈·피부·감작) 예측 모델용 데이터 구축 결과. 2026-08-20 기준.

**읽는 순서**: 이 파일 → `LIMITATIONS.md` → `DESCRIPTOR_DESIGN.md`

---

## 1. 한눈에 보기 — 산출물 목록

파일이 많아 보이지만 **실제로 쓰는 것은 4개**.

| 쓸 것 | 정체 | 사용 시점 |
|---|---|---|
| **`out/v2/*.parquet` + `*.npz`** | 모델에 바로 넣는 행렬 | **학습·평가할 때 사용하는 본체** |
| **`input_dataset_v2.xlsx`** | 위 내용의 사람이 읽는 버전(8시트) | 눈으로 확인·공유·검토할 때 |
| **`dataset_2차배정_verified.xlsx`** | 팀 배정 시트 + 검증란 채움 | 팀에 검증 결과 돌려줄 때 |
| **`LIMITATIONS.md` / `DESCRIPTOR_DESIGN.md`** | 한계·후속작업 / 디스크립터 설계 | 논문 쓸 때, 모델 설계할 때 |

나머지는 **입력 원본** 또는 **중간 산물**.

---

## 2. 파일별 설명

### 2.1 입력 (미수정, 원본 보존)

| 파일 | 내용 |
|---|---|
| `dataset_2차배정.xlsx` | 팀 2차 수집 배정 시트. 5시트(설명·성분·제형코드·독성코드·특성). **원본 미수정** |
| `input_dataset.xlsx` | 기존 v1 모델 입력. MetaData·formulation(1676×189)·ingredient(5288×57) |
| `현 연구의 모델 개발 방향…고찰.docx` | 연구 방향 문서. Track A/B 설계 근거 |
| `formulation_harness/` | 1차 수집에 쓴 하네스 + 그 실행 산출물 (§4) |

### 2.2 최종 산출물

#### `out/v2/` — 모델 입력 본체

| 파일 | 형태 | 설명 |
|---|---|---|
| `X_formulation.parquet` | 1,675 × 518 | **제형 단위 피처**. 농도가중 모멘트 + 상호작용 항 + 실측 물성 + CT baseline + SDS §11 수치 |
| `X_ingredient.parquet` | 5,287 × 174 | **성분 단위 피처**. RDKit 디스크립터 + 구조경보 + 역할 + 계면활성제 클래스 |
| `y_formulation.parquet` | 1,675 × 35 | 타깃. `y_eye/skin/sens` + `_ord`(순서형) `_bin` `_src` `_conflict` + `resid_*`(Track B) |
| `groups.parquet` | 1,675 | **GroupKFold 키** (주성분 CAS 기준, 664 그룹). 누출 방지에 필수 |
| `fp_ing_morgan.npz` | 5,287 × 2048 | 성분 Morgan/ECFP4 지문 |
| `fp_ing_maccs.npz` | 5,287 × 167 | 성분 MACCS 지문 |
| `fp_form_pooled.npz` | morgan 4096 / maccs 334 | 제형 단위로 접은 지문 (`[max \| 농도가중mean]`) |
| `feature_manifest.csv` | 1,070 × 8 | **컬럼 사전**. `role` = feature 442 / feature_derived 345 / label_source 228 / provenance 47 / id 8. `sds_coderived` 열 = 라벨과 같은 문서에서 나온 피처 54열(ablation 대상) |
| `build_summary.json` | — | 빌드 통계 |
| `smoke_report.json` | — | 베이스라인 성능 |

바로 쓰는 코드:

```python
import pandas as pd, numpy as np
from sklearn.model_selection import StratifiedGroupKFold

X = pd.read_parquet("out/v2/X_formulation.parquet")
Y = pd.read_parquet("out/v2/y_formulation.parquet")
G = pd.read_parquet("out/v2/groups.parquet")
MAN = pd.read_csv("out/v2/feature_manifest.csv")

FEATS = [c for c in X.columns if c != "Formulation_ID"]
D = X.merge(Y, on="Formulation_ID").merge(G, on="Formulation_ID")

m = D["y_eye_ord"].notna()
cv = StratifiedGroupKFold(5, shuffle=True, random_state=0)      # ← 반드시 Group 분할
for tr, te in cv.split(D.loc[m, FEATS], D.loc[m, "y_eye_ord"], groups=D.loc[m, "group_key"]):
    ...
```

> ⚠️ `feature_manifest.csv` 에서 `role == "label_source"` 인 228열은 **절대 피처로 쓰지 말 것**
> (타깃을 유도한 원본 컬럼). `X_*.parquet` 에는 이미 제외되어 있음.
> `sds_coderived == True` 인 54열은 피처로 사용 가능하나, 라벨과 같은 SDS 문서에서 나온 값이므로
> **논문에는 이 열을 뺀 ablation 을 함께 보고** 필요 (`LIMITATIONS.md` §L2.1).

#### `input_dataset_v2.xlsx` — 사람이 읽는 버전

| 시트 | 크기 | 설명 |
|---|---|---|
| `MetaData` | 21×3 | 빌드 메타 |
| `formulation` | 1,675 × 570 | 제형 전체(원본 v1 + 2차 신규 + 집계 + SDS §11 + 타깃) |
| `ingredient` | 5,287 × 155 | 성분 전체 |
| `desc_formulation` | 1,675 × 187 | 제형 집계 디스크립터만 |
| `desc_ingredient` | 5,287 × 78 | 성분 디스크립터만 |
| `targets` | 1,675 × 35 | 타깃만 |
| `provenance` | 4,552 × 6 | **출처 추적** — 어느 값이 어디서 왔는지 |
| `feature_manifest` | 1,070 × 8 | 컬럼 사전 |

#### `dataset_2차배정_verified.xlsx` — 검증 결과

원본 5시트 + 각 시트에 `검증여부` 채움 + `검증근거`·`검증방식` 2열 추가.
독성코드의 병합셀 3,295범위 해제 및 드롭다운 재구성 포함. **원본은 미수정.**

### 2.3 문서 (md 3개, 전부)

| 파일 | 무엇 |
|---|---|
| `README.md` | ← **지금 이 파일.** 산출물 지도 + 독성 시트 작업 기록(§5) |
| `LIMITATIONS.md` | 데이터 한계 L0~L10 + 후속작업 F0~F8. **논문 Limitations 절 초안** |
| `DESCRIPTOR_DESIGN.md` | 성분/제형 2층 디스크립터 설계 + 학습 레시피 |
| `MEETING_REPORT.md` | **회의 보고용.** 수집 방법 · 팀 요청사항 · Track 설계 · 다음주 역할분담 |
| `회의_전달_메시지.md` | 위 보고서의 **채팅 붙여넣기용** 축약본 (전체 / 짧은 / 개인DM 3종) |
| `work/MASTERPLAN.md` | 작업 계획서(진행 기록). 이력 참고용 |
| `formulation_harness/README.md` | 하네스 사용법 |

### 2.4 코드

| 파일 | 역할 |
|---|---|
| `work/lib_parse.py` | 2차 시트 자유서술 `추출정보` → 구조화 컬럼. GHS CT 가산식 `ct_predict()` 포함 |
| `work/lib_desc.py` | SMILES → RDKit 디스크립터 · 지문 · 구조경보 · 역할 추정 |
| `work/lib_tox11.py` | SDS Section 11 자유서술 → `t11_*` 구조화 (F0.5). 파싱 규칙 docstring 이 정본 |
| `work/build_input_v2.py` | **메인 빌더** (11단계). 이것만 돌리면 전부 재생성 |
| `work/verify_sheets.py` | 검증란 규칙 엔진 → `dataset_2차배정_verified.xlsx` |
| `work/smoke_baseline.py` | 누출·그룹분할 점검용 베이스라인 |

재생성:
```bash
python3 work/verify_sheets.py      # 검증 시트
python3 work/build_input_v2.py     # 모델 입력 (멱등)
python3 work/smoke_baseline.py     # 점검
```

### 2.5 중간 산물 (삭제 금지, 열람 불필요)

| 경로 | 내용 |
|---|---|
| `work/results/p1_*.json` | 이번 패스 에이전트 회수 결과 13배치. **빌더 입력** |
| `work/batches/p1_*.json` | 에이전트에 넣은 작업 지시 13배치 |
| `work/batches/_P2_backlog.json`, `_P4_backlog.json` | **미처리 백로그** (P2 460행 / P4 298행) |
| `work/verify/verify_report.json` | 검증 규칙별 집계 |

### 2.6 정리 완료 항목

`b.html`(Brave 스로틀 페이지) · `s.html`(Anubis 봇체크) · `m.html`(nginx 302) · `y.html`(0바이트) —
모두 수집 에이전트의 미완료 fetch 잔여물로 데이터 가치 0. **삭제 완료.**
`__pycache__` 도 삭제.

---

## 3. 현재 상태 요약

| 항목 | 값 |
|---|---|
| 제형 | 1,675 |
| 성분 행 | 5,287 (구조 확보 3,574 = 67.6%) |
| 라벨 | 눈 1,105 · 피부 992 · 감작 595 |
| 그중 음성(NC) | 눈 379 · 피부 614 · 감작 209 |
| CT baseline 산출 제형 | 눈 719 · 피부 719 · 감작 721 |
| Track B 잔차 타깃 | 눈 456 · 피부 382 · 감작 184 |
| 타깃 보유 / Track A 학습가능 / Track B 학습가능 | 1,525 / 1,345 / 652 |
| 그룹 수 | 664 (최대 46행) |
| 누출 점검 | X ∩ label_source = **0** ✅ |

베이스라인(RF, 튜닝 전) — **자세한 해석은 `LIMITATIONS.md` §L2**

| 과제 | n | F1 | balAcc | 더미 | 판정 |
|---|---|---|---|---|---|
| 눈 이진 | 1,105 | 0.798 | 0.624 | 0.793 | ✅ 더미 초과(마진 0.005) |
| 눈 순서형 | 1,105 | 0.499 | 0.496 | 0.128 | ✅ |
| 피부 이진 | 992 | 0.679 | 0.752 | 0.000 | ✅ |
| 피부 순서형 | 992 | 0.507 | 0.494 | 0.191 | ✅ |
| 감작 이진 | 595 | 0.808 | 0.670 | 0.787 | ✅ 더미 초과(마진 0.021) |
| 감작 순서형 | 595 | 0.677 | 0.670 | 0.393 | ✅ |
| Track B 눈 / 피부 | 456 / 382 | 0.628 / 0.618 | — | ~0.33 | ✅ |
| Track B 감작 | 184 | 0.417 | — | ~0.33 | ⚠️ 보고 금지(3클래스 인공물) |

> **F1 만 보면 해석이 반대로 뒤집힘.** F0.5 이전 눈 이진 F1 은 0.832, 현재는 0.798.
> 단, 같은 구간에 더미가 0.838→0.793 으로 더 크게 하락했고 balAcc 은 0.593→0.624 로
> 상승. 음성 라벨 유입으로 과제 난이도가 정상화된 결과. **더미 대비와 balAcc 으로만 판단할 것.**

---

## 4. 하네스 사용 범위

**독성코드 시트만 하네스 사용.** 성분·제형코드·특성은 방법론 미문서화 ad-hoc 수집.

```
formulation_harness/
├─ README.md                    파이프라인 설명
├─ extract_targets.py           대상 행 추출
├─ collect_pdfs.py              PDF 수집
├─ apply_results.py             결과 → xlsx
├─ build_ml_collection_sheets.py
├─ field_specs.json             SDS 섹션↔필드 정의
├─ cipac_codes.json             CIPAC 제형코드 96종
├─ _run/                        1차 실행 (제형/성분 — 결과 미반영)
└─ _run_tox/                    ★ 독성코드 실행 (유일하게 시트에 반영됨)
   ├─ sds-research-toxicity.js       수집 워크플로 (섹션 정의 = 진실의 원천)
   ├─ sds-verify-toxicity*.js        검증 워크플로 4종 (이번엔 미실행)
   ├─ apply_toxicity_results.py      결과 → 독성코드 시트
   ├─ final_merged_results.json  ★★ 1,076제품 전체 수집 결과 (§5.5 — 미활용 데이터 다수)
   └─ chunk_*/vchunk_*/rchunk_*  배치 분할 중간파일 (재현용, 무시 가능)
```

---

## 5. 독성코드 시트 — 작업 방법 전체 기록

### 5.1 작업 방식 (2단계 파이프라인)

```
① 대상 추출        1,675행 → 제품명 중복 제거 → 1,076 고유제품
                   (row_mapping.json 이 제품명 → 행번호 목록을 보관)
        ↓
② 병렬 SDS 조사    sds-research-toxicity.js
                   에이전트 배치(8제품/배치) 병렬 → SDS 검색·판독
                   ★ 성분일치 게이팅: SDS Section 3 성분표가 시트의
                     ingredient_names 와 일치할 때만 값을 기록
        ↓
③ 4값 판정         found / no_code_in_sds / ingredient_mismatch / unresolved
        ↓
④ 시트 기입        apply_toxicity_results.py
                   resolution=found AND ingredients_match=True AND
                   (신호어 또는 H코드 존재) → 추출정보 기입. 아니면 공란
```

**설계 의도** — 힌트로 준 `tox_source_url` 중 상당수가 무관한 물질의 SDS였음.
따라서 "힌트 URL 을 믿지 말고 Section 3 로 검증하라"를 게이팅 규칙으로 강제하고,
불일치 시 값을 **비워 두도록** 처리. 값을 채우는 것보다 틀린 값을 넣지 않는 쪽을 택한 설계.

에이전트 판정 결과 (1,076 제품):

| resolution | 건수 | 의미 |
|---|---|---|
| `found` | 749 | SDS 찾고 성분 일치 |
| `unresolved` | 222 | 쓸 만한 SDS 를 못 찾음 |
| `ingredient_mismatch` | 60 | SDS 는 있으나 성분이 다름 → 기록 거부 |
| `no_code_in_sds` | 45 | 성분 일치하는 SDS 인데 Section 11 에 분류가 없음 |

신뢰도 자기평가: high 391 / medium 408 / **low 277**
(`ingredients_match=True` 는 773건 — `found` 749보다 많은 것은 `no_code_in_sds` 중 성분은
확인된 건이 포함되기 때문)

### 5.2 SDS 추출 대상 섹션

`sds-research-toxicity.js` 의 `FIELD_SPECS` 가 정의한 15개 파라미터, **모두 Section 11
(독성학적 정보)** + 라벨 요소:

| 파라미터 | 출처 섹션 |
|---|---|
`ghs_signal_word` (신호어) | Section 2 (라벨 요소) |
`ghs_hazard_statements` (H코드) | Section 2 |
`ghs_pictograms` | Section 2 |
`acute_oral_toxicity_ld50` | **Section 11** |
`acute_dermal_toxicity_ld50` | **Section 11** |
`acute_inhalation_toxicity_lc50` | **Section 11** |
`eye_damage_irritation` (눈) | **Section 11** |
`skin_corrosion_irritation` (피부) | **Section 11** |
`respiratory_skin_sensitization` (감작) | **Section 11** |
`germ_cell_mutagenicity` | **Section 11** |
`carcinogenicity` | **Section 11** |
`reproductive_toxicity` | **Section 11** |
`stot_single_exposure` | **Section 11** |
`stot_repeated_exposure` | **Section 11** |
`aspiration_hazard` | **Section 11** |

부수적으로 **Section 3**(조성)은 게이팅 검증용으로 반드시 판독.
**Section 9**(물리화학)와 **Section 1/라벨**(제형코드)은 이 워크플로가 아니라
성분·특성·제형코드 시트 담당이며, 그 3시트는 하네스 미사용(§4).
→ **결과적으로 Section 9(pH 포함)는 체계적으로 수집된 적이 없음.** `LIMITATIONS.md` §L6.

### 5.3 채움률 (원본 `dataset_2차배정.xlsx` 독성코드, 1,675행)

| 컬럼 | 채움 | % | 성격 |
|---|---|---|---|
| `Formulation_ID` / `product_name` | 1,675 | 100% | 키 |
| `ingredient_names` | 1,482 | 88.5% | |
| `tox_signal_word` | 440 | 26.3% | v1 기존 |
| `tox_h_statements` | 338 | 20.2% | v1 기존 |
| `cat_eye_ghs` | 789 | 47.1% | v1 기존 라벨 |
| `cat_skin_ghs` | 701 | 41.9% | v1 기존 라벨 |
| `cat_sens_ghs` | 184 | 11.0% | v1 기존 라벨 |
| `sds_grade_status_*` (3열) | 1,675 | 100% | 상태 플래그 |
| `tox_source_url` | 235 | 14.0% | |
| `doc_source` / `doc_rel` | 564 / 541 | 33.7 / 32.3% | |
| **`신규소스링크`** | **541** | **32.3%** | 하네스 산출 |
| **`추출정보`** | **1,076** | **64.2%** | 하네스 산출 |
| **`메모`** | **1,334** | **79.6%** | 하네스 산출 |
| `검증여부` | **0** | **0%** | ← 이번에 채움 |

`추출정보` 1,076행의 내용 구성:

| 신호어 | H코드 있음 | H코드 없음 |
|---|---|---|
| Danger | 약 350행 | 59 |
| Warning | 약 340행 | 107 |
| **Caution** (EPA FIFRA) | — | **84** |
| 없음 | 약 25행 | 58 |

→ H코드가 붙은 행은 약 715행, **신호어만 있고 H코드가 없는 행이 308행**(구형 MSDS·EPA 라벨).
Caution 84행은 GHS 신호어가 아니라 EPA 4단 체계(`LIMITATIONS.md` §L1.3).

### 5.4 메모 작성 규칙

**"이상 상황만, 짧게 한국어로"** — 하네스 프롬프트의 명시 규칙.

```
notes: CONCISE Korean note, ANOMALIES ONLY.
       정상이면 빈 문자열 "". 완결성 요약("성분정보O, 독성정보O")은 금지 —
       그건 sds_summary(내부 QA)에 넣고 시트에는 쓰지 않는다.
```

실제 1,334개 메모의 키워드 분포(중복 집계):

| 유형 | 건수 | % |
|---|---|---|
| 확인/일치 언급 | 827 | 62.0% |
| **SDS 없음·못 찾음** | 544 | 40.8% |
| **성분 불일치** | 169 | 12.7% |
| 접근차단 (403 / 로그인 / JS렌더링 / CAPTCHA) | 148 | 11.1% |
| 도구 제약 (웹서치 불가 / WebFetch 한계 / PDF) | 128 | 9.6% |
| GHS 미표기 · 구형 MSDS · EPA 라벨 | 123 | 9.2% |
| 단일물질 SDS 로 대체 | 111 | 8.3% |
| 분류 대상 아님 (음성) | 68 | 5.1% |
| 404 / 링크 깨짐 | 17 | 1.3% |

실제 메모 예 — **미회수 사유 / 대체 근거**를 남기는 형식:

```
제조사(PBI-Gordon) 사이트 접근 차단(HTTP 403), 대체 SDS 검색 실패
힌트 SDS는 n-Pentane 단일물질 SDS로, 제품의 6개 성분 혼합조성과 불일치. 완제품 SDS 못 찾음
동일 EPA 등록번호(11556-125)의 배포상표명 라벨에서 동일 성분/함량 확인; 정식 SDS는 미확인
제품 자체 SDS 대신 두 원료(Metribuzin, Flufenacet) 개별 GHS/독성 데이터를 결합함
제조사 개별 SDS 대신 PubChem 집계 ECHA C&L 통보자료(122건) 사용. LD50 미제공, 분류코드만
```

**메모가 데이터로서 유용한 이유**: `추출정보`가 비어 있는 행에서 메모가 공란 사유를
설명함. 이번 검증 규칙 엔진과 백로그 버킷팅(P1~P4)은 전부 **메모 텍스트 파싱**을
근거로 동작.

### 5.5 ★ 추가 수집 가능 범위 — 웹 접근 없이 가능

#### (A) 수집 완료 후 시트 미반영 데이터 — ✅ **2026-08-20 반영 완료 (F0.5)**

`apply_toxicity_results.py:build_extract_text()` 는 **15개 파라미터 중 2개만** 시트에 기록:

```python
signal = tox.get("ghs_signal_word")
h_stmt = tox.get("ghs_hazard_statements")
return " / ".join(parts)          # ← 나머지 13개는 버려짐
```

단, `final_merged_results.json` (1.2MB) 에는 **전부 보존됨**:

| 파라미터 | 수집된 제품 수 | 시트 반영? |
|---|---|---|
| `ghs_signal_word` | 731 | ✅ |
| `ghs_hazard_statements` | 690 | ✅ |
| `acute_oral_toxicity_ld50` | 592 | ❌ **미반영** |
| `eye_damage_irritation` | 591 | ❌ **미반영** |
| `skin_corrosion_irritation` | 559 | ❌ **미반영** |
| `respiratory_skin_sensitization` | 541 | ❌ **미반영** |
| `acute_dermal_toxicity_ld50` | 499 | ❌ |
| `acute_inhalation_toxicity_lc50` | 477 | ❌ |
| `ghs_pictograms` | 472 | ❌ |
| `carcinogenicity` | 467 | ❌ |
| `reproductive_toxicity` | 436 | ❌ |
| `stot_repeated_exposure` | 412 | ❌ |
| `germ_cell_mutagenicity` | 389 | ❌ |
| `aspiration_hazard` | 380 | ❌ |
| `stot_single_exposure` | 379 | ❌ |

**실제 반영 결과** (`work/lib_tox11.py` 신규 + `build_input_v2.py` §3.5. 웹 접근 0, 에이전트 0):

| 항목 | 이전 | 이후 | 증감 |
|---|---|---|---|
| 눈 라벨 | 955 | **1,105** | +150 |
| 피부 라벨 | 855 | **992** | +137 |
| 감작 라벨 | 411 | **595** | +184 |
| 눈 음성(NC) | 266 | **379** | +113 |
| 피부 음성(NC) | 493 | **614** | +121 |
| 감작 음성(NC) | 50 | **209** | +159 |
| Track B 잔차 타깃 | 412/339/117 | **456/382/184** | +44/+43/+67 |
| 경구 LD50 (신규 수치 피처) | 0 | **617행** | |
| 경피 LD50 | 0 | **531행** | |
| 흡입 LC50 | 0 | **392행** | |
| 발암/변이원/생식/STOT-SE/STOT-RE/흡인 순서형 | 0 | 149/101/101/117/103/99 | |

**가장 큰 이득은 음성 라벨.** 감작성 양성률 87.8% → **64.9%** 로 하락하면서
`LIMITATIONS.md` §L1.4 가 지적한 "음성 표본 구조적 부족"이 실질적으로 해소되고,
눈·감작 이진 과제가 처음으로 더미를 초과(§3).

**부수적 대가** (§L11 에 기록):
- 라벨 충돌이 눈 69→**124** · 피부 67→**88** · 감작 1→**10** 으로 증가.
- 현재 라벨의 13.6%(눈) / 13.8%(피부) / **30.9%(감작)** 가 정규식으로 파싱한 자유서술.
- 새 수치 피처의 성능 기여는 사실상 0 (ablation Δ = +0.001~+0.040, §L2.1).
  **F0.5 의 이득은 피처가 아니라 라벨에서 발생.**

파싱 규칙(결측 vs 음성 구분, 정성서술 승격 금지, EPA↛GHS, 엔드포인트 교차오염 차단,
단위 없는 값 폐기)은 `work/lib_tox11.py` 상단 docstring 이 정본.

#### (B) 미실행 검증 단계

`_run_tox/` 에 검증 워크플로 4개(`sds-verify-toxicity*.js`) 존재하나
**이번 패스에서 미실행** (`apply_toxicity_results.py` 주석: "검증여부: 손대지 않음").
신뢰도 `low` 277건 · `medium` 408건을 이 워크플로로 재확인 시 라벨 품질 향상 가능.

#### (C) 미처리 백로그

| 버킷 | 행수 | 상태 |
|---|---|---|
| P1 | 130 | ✅ 이번 패스 완료 (13배치) |
| P2 | 460 | ❌ `work/batches/_P2_backlog.json` 준비됨 |
| P3 | 9 | ❌ |
| P4 | 298 | ❌ `work/batches/_P4_backlog.json` 준비됨 |

배치 파일이 이미 준비되어 에이전트에 그대로 투입 가능.
단, `LIMITATIONS.md` §L7.5 — 일부는 **SDS 가 원리적으로 존재하지 않음**(단종·규제금지·
개발코드·NTP 조제 혼합물). 전량 회수를 목표로 설정 금지.

#### (D) 미착수 상태의 다른 축

| 대상 | 왜 필요 | 어디서 |
|---|---|---|
| **SDS Section 9 (pH)** | CT 가산식 예외 게이트. 현재 68% 결측 | SDS Section 9 만 표적 수집 |
| 감작성 **실측**값 | 현재 실측 0건 (전부 H317 유도) | LLNA / DPRA / h-CLAT 외부 DB |
| 성분별 active 플래그 | 그룹분할 키의 근본 문제 | EPA 등록번호 / 농약등록번호 |
| 성분 pKa | 이온화×pH 상호작용 항 | PubChem / ChEMBL |

우선순위와 근거는 `LIMITATIONS.md` §F0~F8 참조.
