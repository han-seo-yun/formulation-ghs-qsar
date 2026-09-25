# RALPLAN: Formulation Missing Data Resolution
## Status: pending approval — design updated post-consensus
## Consensus: Architect ✅ Critic ✅ (3 iterations) + user design change

---

## RALPLAN-DR Summary

### Principles
1. **Pipeline idempotency** — every step can be re-run safely; existing outputs preserved via cache
2. **Pre-fill enriches, LLM decides** — rule-based pre-fill is hint context only; LLM output is authoritative
3. **Pair integrity as first-class constraint** — formulation_code and its 3 supporting fields are written atomically or not at all
4. **Minimum-diff changes** — touch as few existing files as possible; sibling imports over package restructuring
5. **No silent failures** — all changes verified against actual source before plan is final

### Decision Drivers
1. **AWS Bedrock availability** — existing `extract_formulation_features.py` kept as-is except targeted additions
2. **Pair integrity requirement** — A1 must produce formulation_code + 3 supporting fields together; partial writes are prohibited
3. **소스 분리 힌트** — `sec_formulation_code`(PDF)와 `name_formulation_code`(이름 패턴)를 분리 전달해 A1이 불일치 시 직접 판단

### 설계 결정 (post-consensus 사용자 확정)

`sec_formulation_code`는 이미 `AGENT_INPUT_COLS["type"]`(line 123)와 `sec_cols`(line 100)에 존재 — A1이 이미 PDF 값을 받고 있음.
따라서 `name_formulation_code`(병합 단일값) 대신 **`name_formulation_code`(이름 패턴만)를 신규 컬럼으로 추가**.

A1 수신 구조:
- `sec_formulation_code` — PDF 파싱값 (기존, 변경 없음)
- `name_formulation_code` — Formulation_Name 패턴값 (신규)
- `sec1_text` — 원문 (기존)

불일치 시 (예: PDF="EC", Name="WP") A1이 sec1_text까지 종합해 최종 결정. 병합 우선순위 로직 불필요.

---

## Implementation Steps

### Step 1: Create `workflow/pre_fill_merge.py` (new module)

`sec_formulation_code`(PDF)는 이미 sec_cols로 로드됨 → 이 스크립트는 **`name_formulation_code`만 추출** (병합 로직 없음).

```python
"""
Phase 1.5 — Name-pattern formulation code extraction
Adds name_formulation_code column extracted from Formulation_Name.
sec_formulation_code (PDF) is already in sec_cols — passed separately to A1.
A1 receives both and decides which to trust.
"""
from __future__ import annotations
import re
import pandas as pd
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
INPUT_CSV  = BASE_DIR / "artifacts/enriched_queue.csv"
OUTPUT_CSV = BASE_DIR / "artifacts/enriched_queue_prefilled.csv"

# Longest alternatives first to avoid WG matching inside WDG
FORM_CODES = ['WDG', 'RTU', 'ULV', 'EC', 'SC', 'WP', 'WG', 'FS',
              'EW', 'SL', 'SP', 'CS', 'DC', 'GR', 'TB']
_PAT = re.compile(r'\b(' + '|'.join(FORM_CODES) + r')\b')


def extract_name_formulation_code(row: pd.Series) -> Optional[str]:
    """Extract formulation code from Formulation_Name only (no PDF merge)."""
    m = _PAT.search(str(row.get('Formulation_Name', '')))
    return m.group(1) if m else None


def add_name_formulation_code(df: pd.DataFrame) -> pd.DataFrame:
    """Adds name_formulation_code column and returns df. Idempotent."""
    df = df.copy()
    df['name_formulation_code'] = df.apply(extract_name_formulation_code, axis=1)
    return df


def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            f"{INPUT_CSV} not found. Run Phase 1 first:\n"
            "  python workflow/enrich_with_sections.py"
        )
    df = pd.read_csv(INPUT_CSV, low_memory=False, encoding='utf-8-sig')
    df = add_name_formulation_code(df)
    filled = df['name_formulation_code'].notna().sum()
    # Conflict analysis (informational)
    both = df['sec_formulation_code'].notna() & df['name_formulation_code'].notna()
    conflict = both & (df['sec_formulation_code'] != df['name_formulation_code'])
    agree   = both & (df['sec_formulation_code'] == df['name_formulation_code'])
    print(f"name_formulation_code: {filled}/{len(df)} rows filled")
    print(f"  PDF only : {(df['sec_formulation_code'].notna() & df['name_formulation_code'].isna()).sum()}")
    print(f"  Name only: {(df['sec_formulation_code'].isna() & df['name_formulation_code'].notna()).sum()}")
    print(f"  Both agree   : {agree.sum()}")
    print(f"  Both conflict: {conflict.sum()}  ← A1이 sec1_text로 판단")
    df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"Written: {OUTPUT_CSV}")


if __name__ == '__main__':
    main()
```

---

### Step 2: Modify `workflow/extract_formulation_features.py` — 3 targeted changes

**전제:** `sec_formulation_code`는 이미 `sec_cols`(line 100)와 `AGENT_INPUT_COLS["type"]`(line 123)에 있음 — 변경 불필요.
**추가할 것:** `name_formulation_code`만.

#### 2a — Add `name_formulation_code` to `base_cols` in `load_review_queue()` (line ~98)

`base_cols` 마지막 엔트리 `"Ingredient_Source"` 뒤에 추가:

```python
    "Ingredient_Source",
    "name_formulation_code",    # Phase 1.5: name-pattern hint; fallback loop sets "" if absent
```

`usecols` 람다(line ~108)가 이제 이 컬럼을 통과시킴. 폴백 루프(lines 112-114)가 구 CSV에서는 `""`으로 초기화 — 하위 호환 유지.

#### 2b — Add `name_formulation_code` to `AGENT_INPUT_COLS["type"]` (lines ~120-124)

```python
    "type": [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "sec_formulation_code",  # 이미 있음 — PDF 파싱값
        "name_formulation_code",    # 신규 — Formulation_Name 패턴값 (불일치 시 A1 판단)
        "sec_product_name", "sec1_text",
        # ... 기존 컬럼 변경 없음
    ],
```

#### 2c — Add sibling import and inline call in `load_review_queue()` (after line ~113)

**파일 상단 (기존 import 뒤):**
```python
# workflow/ has no __init__.py — sibling import, not package import
from pre_fill_merge import add_name_formulation_code
```

**`load_review_queue()` 내부, 폴백 루프(line ~113) 뒤:**
```python
    df = add_name_formulation_code(df)   # in-memory, idempotent
```

`load_review_queue()`가 `df.fillna("")`(line ~115)로 리턴하므로 `None` → `""` — A1 프롬프트에서 빈 문자열로 전달됨 (benign).

---

### Step 3: Pair integrity enforcement in `extract_formulation_features.py`

**Placement:** After `output_df = pd.concat([output_df, cancer_output], ignore_index=True)` at line ~321. Use `output_df` (the merged output), NOT `df` (which is the input queue at line 290 and has no `formulation_code` column).

```python
# After line ~321, before writing output CSV:
PAIR_FIELDS = ['physical_form', 'product_category', 'concentration_type']

# Exclude 'UNKNOWN' sentinel (CANCER_DEFAULTS + LLM-failure rows)
mask = output_df['formulation_code'].notna() & (output_df['formulation_code'] != 'UNKNOWN')
violations = mask & output_df[PAIR_FIELDS].isnull().any(axis=1)

if violations.any():
    output_df.loc[violations, 'formulation_code'] = None
    print(f"[WARN] pair integrity: {violations.sum()} rows cleared "
          f"(formulation_code present but supporting fields missing)")

# Refresh mask after correction, then assert
mask = output_df['formulation_code'].notna() & (output_df['formulation_code'] != 'UNKNOWN')
n_violations = output_df.loc[mask, PAIR_FIELDS].isnull().any(axis=1).sum()
assert n_violations == 0, f"Pair integrity FAILED: {n_violations} violations after auto-fix"
```

---

### Step 4: Register Phase 1.5 in `workflow/run_pipeline.py` — 4 changes

#### 4a — Add "1.5" to `PHASES` dict (between "1" and "2", lines 32-54)
```python
"1.5": {
    "name": "Pre-fill Merge",
    "script": WORKFLOW / "pre_fill_merge.py",
    "output_check": BASE_DIR / "artifacts/enriched_queue_prefilled.csv",
    "needs_api": False,
    "description": "enriched_queue → enriched_queue_prefilled.csv (+name_formulation_code hint)",
},
```

#### 4b — Add "1.5" to `argparse choices` (line ~106)
```python
choices=["0.5", "1", "1.5", "2"]
```

#### 4c — Insert "1.5" into `phase_order` (line ~138)
```python
phase_order = ["0.5", "1", "1.5", "2"]
```

#### 4d — Add "1.5" to credential pre-check (line ~126)
```python
if not args.no_llm and args.from_phase in ["0.5", "1", "1.5", "2"]:
```

`print_status()` iterates `PHASES.items()` — auto-updates, no change needed.

---

### Step 5: Add hint field note to `agents/formulation_type_classifier.md`

Add a new row to the `## 입력 필드` table at line ~28 (before the closing `---`):

```markdown
| name_formulation_code | 제형코드 힌트 | PDF/제품명에서 추출한 규칙 기반 힌트(EC/SC/WP 등). 성분/SDS 내용과 상충 시 LLM 판단 우선. |
```

---

## Acceptance Criteria

- [ ] `workflow/pre_fill_merge.py` exists and is importable
- [ ] `python workflow/pre_fill_merge.py` → `name_formulation_code` non-null **≥ 134**/1675 rows (이름 패턴만); `artifacts/enriched_queue_prefilled.csv` written. `sec_formulation_code`(PDF, 131건)는 별도 컬럼으로 이미 존재.
- [ ] `extract_formulation_features.py` `base_cols` (line ~98) contains `"name_formulation_code"`
- [ ] `AGENT_INPUT_COLS["type"]` (line ~120) contains `"name_formulation_code"`
- [ ] `from pre_fill_merge import add_name_formulation_code` in `extract_formulation_features.py` (no `workflow.` prefix)
- [ ] `run_pipeline.py` `phase_order` = `["0.5", "1", "1.5", "2"]` (grep-verified)
- [ ] After Phase 2: `formulation_characteristics_output.csv` has columns: `formulation_code`, `physical_form`, `product_category`, `concentration_type`, `epa_reg_number`, `regulatory_jurisdiction`, `registration_status`
- [ ] Pair integrity: `formulation_code` non-null & != 'UNKNOWN' → all 3 supporting fields non-null (**0 violations**)

---

## Risks & Mitigations

| Risk | Status | Mitigation |
|------|--------|------------|
| `name_formulation_code` silently dropped (base_cols whitelist) | Eliminated | Step 2a adds to base_cols; Step 2c inline call applies regardless of CSV source |
| `ModuleNotFoundError: No module named 'workflow'` | Eliminated | Sibling import `from pre_fill_merge import` (workflow/ has no `__init__.py`) |
| `KeyError: 'formulation_code'` in pair integrity block | Eliminated | All Step 3 references use `output_df`, not `df` (input queue) |
| Stale mask false assertion | Eliminated | Mask refreshed after correction before asserting |
| Encoding mismatch corrupts Formulation_ID | Eliminated | utf-8-sig used; inline path bypasses CSV entirely |
| `--from-phase 1.5` skips credential pre-check | Eliminated | Step 4d adds "1.5" to check condition |
| A1 returns partial pair (code + missing supporting field) | Medium | Step 3 clears formulation_code rather than emit partial pair |
| AWS Bedrock throttling | Medium | Existing retry logic + extraction_cache/ resumability |
| WDG/WG regex ambiguity | Low | FORM_CODES longest-first ordering |
| Existing cache lacks name_formulation_code hint | Low | Acceptable; `--force` regenerates. Follow-up: document in help text |
| LLM returns `""` for pair fields (not null) | Low | `.isnull()` would miss; in practice LLM emits JSON null, not empty string |

---

## Verification Steps

```bash
# 1. Run Phase 1.5 standalone
python workflow/pre_fill_merge.py
# Expected: "name_formulation_code: NNN/1675 rows filled" where NNN >= 134

# 2. Verify importable callable (run from workflow/ dir for sibling import)
cd /Users/hanseoyun/Desktop/formulation/workflow
python -c "
from pre_fill_merge import add_name_formulation_code
import pandas as pd
df = pd.read_csv('../artifacts/enriched_queue.csv', low_memory=False, encoding='utf-8-sig')
df2 = add_name_formulation_code(df)
assert 'name_formulation_code' in df2.columns
filled = df2['name_formulation_code'].notna().sum()
assert filled >= 134, f'Only {filled} name-pattern rows, expected >=134'
print(f'OK: {filled} rows filled')
"
cd ..

# 3. Verify changes in extract_formulation_features.py
grep -n 'name_formulation_code' workflow/extract_formulation_features.py
# Expected: >=3 hits (base_cols, AGENT_INPUT_COLS["type"], inline call)
grep -n 'from pre_fill_merge import' workflow/extract_formulation_features.py
# Expected: 1 hit (no 'workflow.' prefix)

# 4. Verify run_pipeline.py phase registration (PHASES via exec; phase_order via grep)
python -c "
import sys; sys.path.insert(0, 'workflow')
exec(open('workflow/run_pipeline.py').read().split('if __name__')[0])
assert '1.5' in PHASES, f'Phase 1.5 missing from PHASES: {list(PHASES)}'
print(f'OK: PHASES keys = {list(PHASES)}')
"
grep -n 'phase_order' workflow/run_pipeline.py
# Expected: one line containing ["0.5", "1", "1.5", "2"]

# 5. Set AWS credentials and run integrated Phase 1.5 -> Phase 2
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=...
python workflow/run_pipeline.py --from-phase 1.5

# 6. Verify pair integrity and fill rates after Phase 2
python -c "
import pandas as pd
df = pd.read_csv('artifacts/formulation_characteristics_output.csv')
pair_fields = ['physical_form', 'product_category', 'concentration_type']
mask = df['formulation_code'].notna() & (df['formulation_code'] != 'UNKNOWN')
violations = mask & df[pair_fields].isnull().any(axis=1)
assert violations.sum() == 0, f'Pair integrity FAILED: {violations.sum()} violations'
print(f'Pair integrity: OK (0 violations)')
print(f'formulation_code fill: {mask.sum()}/{len(df)} ({mask.mean()*100:.1f}%)')
print(f'epa_reg_number fill: {df[\"epa_reg_number\"].notna().sum()}/{len(df)}')
"
```

---

## ADR

**Decision:** `pre_fill_merge.py` as callable-first sibling module; sibling import in `extract_formulation_features.py`; pair integrity enforced on `output_df` after `pd.concat`.

**Drivers:** pair integrity, column whitelist safety, minimum-diff (no `__init__.py`), no silent failures.

**Alternatives considered:**
- Package import with `__init__.py`: rejected — adds file, changes project structure, unnecessary
- Inline-only in Phase 2: rejected — untestable without AWS credentials
- Pure CSV pipeline: rejected — column whitelist + encoding risks confirmed against source

**Why chosen:** Callable-first pattern eliminates CSV boundary risk. Sibling import is the minimum-diff option compatible with `workflow/` having no `__init__.py`.

**Consequences:**
- Existing `extraction_cache/` entries lack `name_formulation_code` context — use `--force` to regenerate with hint
- `enriched_queue_prefilled.csv` is inspection-only; downstream processes must not hard-depend on it
- `None` hint values become `""` via `fillna("")` in `load_review_queue()` — benign for LLM prompt

**Follow-ups:**
- PDF regex improvement (Phase 0.5 re-run): deferred; revisit after Phase 2 fill rates observed
- Phase 3 Excel integration: separate task after Phase 2 completion
- Cache invalidation note in `run_pipeline.py --help` text
- `sec_reach_id` missing from `sec_cols` (pre-existing bug in `extract_formulation_features.py:99-105`): follow-up fix

---

## Files Changed

| File | Change |
|------|--------|
| `workflow/pre_fill_merge.py` | **New** — Phase 1.5 module |
| `workflow/extract_formulation_features.py` | 3 additions: base_cols, AGENT_INPUT_COLS["type"], import + inline call |
| `workflow/run_pipeline.py` | 4 additions: PHASES entry, argparse choices, phase_order, credential check |
| `agents/formulation_type_classifier.md` | 1 table row added |

---

## Consensus History

| Iteration | Architect | Critic | Key Issue |
|-----------|-----------|--------|-----------|
| v1 | — | REJECT | base_cols whitelist gap (critical), wrong symbols, stale mask |
| v2 | REVISE | REJECT | `from workflow.` import fails (no __init__.py); `df` vs `output_df` |
| v3 | APPROVED | APPROVED | All blockers resolved |
