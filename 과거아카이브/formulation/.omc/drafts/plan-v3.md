# RALPLAN: Formulation Missing Data Resolution — v3
## Status: DRAFT v3 (incorporating Architect iteration 2 feedback)

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
3. **Pre-fill as LLM hint** — `pre_formulation_code` in A1 prompt improves classification without schema changes

### Option Summary (unchanged from v2, confirmed correct)
**Chosen: Option A (revised)** — `pre_fill_merge.py` exports callable `add_pre_formulation_code(df)`, called inline by Phase 2 (no CSV boundary dependency) while also writing standalone CSV for inspection.

---

## Implementation Steps

### Step 1: Create `workflow/pre_fill_merge.py` (new module)

**File:** `workflow/pre_fill_merge.py` (new, ~70 lines)

```python
"""
Phase 1.5 — Pre-fill merge
Exports add_pre_formulation_code(df) for use inline in Phase 2,
and main() for standalone CSV production (human inspection only).
"""
import re
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
INPUT_CSV  = BASE_DIR / "artifacts/enriched_queue.csv"
OUTPUT_CSV = BASE_DIR / "artifacts/enriched_queue_prefilled.csv"

# Longest alternatives first to avoid WG matching inside WDG
FORM_CODES = ['WDG', 'RTU', 'ULV', 'EC', 'SC', 'WP', 'WG', 'FS',
              'EW', 'SL', 'SP', 'CS', 'DC', 'GR', 'TB']
_PAT = re.compile(r'\b(' + '|'.join(FORM_CODES) + r')\b')


def resolve_pre_formulation_code(row: pd.Series) -> str | None:
    # Priority 1: PDF-extracted sec_formulation_code
    val = row.get('sec_formulation_code')
    if pd.notna(val) and str(val).strip():
        return str(val).strip()
    # Priority 2: Formulation_Name pattern match
    m = _PAT.search(str(row.get('Formulation_Name', '')))
    return m.group(1) if m else None


def add_pre_formulation_code(df: pd.DataFrame) -> pd.DataFrame:
    """Adds pre_formulation_code column in-place and returns df. Idempotent."""
    df = df.copy()
    df['pre_formulation_code'] = df.apply(resolve_pre_formulation_code, axis=1)
    return df


def main() -> None:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            f"{INPUT_CSV} not found. Run Phase 1 first:\n"
            "  python workflow/enrich_with_sections.py"
        )
    df = pd.read_csv(INPUT_CSV, low_memory=False, encoding='utf-8-sig')
    df = add_pre_formulation_code(df)
    filled = df['pre_formulation_code'].notna().sum()
    print(f"pre_formulation_code: {filled}/{len(df)} rows filled "
          f"({filled / len(df) * 100:.1f}%)")
    df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"Written: {OUTPUT_CSV}")


if __name__ == '__main__':
    main()
```

**Acceptance gate:** `pre_formulation_code` non-null ≥ 250 rows.

---

### Step 2: Modify `workflow/extract_formulation_features.py` — 3 targeted changes

**File:** `workflow/extract_formulation_features.py`

#### 2a — Add `pre_formulation_code` to `base_cols` in `load_review_queue()` (line ~98)

The actual `base_cols` list has 12 entries (lines 91-98). Add `"pre_formulation_code"` as the 13th entry at the end of the list:

```python
# Locate: base_cols = [ ... "Ingredient_Source", ]  (line ~98, last entry)
# AFTER — append pre_formulation_code as new last entry:
    "Ingredient_Source",
    "pre_formulation_code",     # Phase 1.5 hint column; fallback loop sets "" if absent
```

The `usecols` lambda at line ~108 will now admit this column. The fallback loop at lines 112-114 initialises it as `""` if absent — safe for backwards compatibility with old `enriched_queue.csv`.

#### 2b — Add `pre_formulation_code` to `AGENT_INPUT_COLS["type"]` (lines 119-124)

```python
# Locate: AGENT_INPUT_COLS: dict[str, list[str]] = { "type": [...], ... }  (line ~119)
# Add 'pre_formulation_code' to the "type" list:
    "type": [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "sec1_text",
        "pre_formulation_code",     # formulation type hint from PDF + name pattern
        # ... all existing columns unchanged
    ],
```

#### 2c — Add sibling import and inline call inside `load_review_queue()` (after line ~113)

**Import (add near top of file, after existing imports):**

```python
# workflow/ has no __init__.py — use sibling import, not package import
from pre_fill_merge import add_pre_formulation_code
```

**Inline call (inside `load_review_queue()`, after the fallback-fill loop at line ~113):**

```python
    # In-memory pre-fill (idempotent; fills pre_formulation_code regardless of CSV source)
    df = add_pre_formulation_code(df)
```

This ensures `pre_formulation_code` is always present in `work_df` even when reading the original `enriched_queue.csv` without Phase 1.5 having been run as a standalone step.

---

### Step 3: Pair integrity enforcement in `extract_formulation_features.py` (after line ~321)

**Variable in scope at this location is `output_df` (NOT `df`).**
- `df` at line 290 = input queue (columns are input features like `Formulation_Name`, `sec_*`)
- `output_df` at line 321 = merged output with `formulation_code`, `physical_form`, etc.

**Placement:** After `output_df = pd.concat([output_df, cancer_output], ignore_index=True)` at line ~321.

```python
# After line ~321 (pd.concat with cancer rows), before writing output CSV:
PAIR_FIELDS = ['physical_form', 'product_category', 'concentration_type']

# Exclude 'UNKNOWN' sentinel (CANCER_DEFAULTS + LLM-failure rows)
mask = output_df['formulation_code'].notna() & (output_df['formulation_code'] != 'UNKNOWN')
violations = mask & output_df[PAIR_FIELDS].isnull().any(axis=1)

if violations.any():
    # Nullify formulation_code for partial pairs — prefer no code over lying code
    output_df.loc[violations, 'formulation_code'] = None
    print(f"[WARN] pair integrity: {violations.sum()} rows cleared "
          f"(formulation_code present but supporting fields missing)")

# Refresh mask after correction, then assert
mask = output_df['formulation_code'].notna() & (output_df['formulation_code'] != 'UNKNOWN')
n_violations = output_df.loc[mask, PAIR_FIELDS].isnull().any(axis=1).sum()
assert n_violations == 0, f"Pair integrity FAILED: {n_violations} violations after auto-fix"
```

---

### Step 4: Register Phase 1.5 in `workflow/run_pipeline.py` — 4 concrete changes

**File:** `workflow/run_pipeline.py`

#### 4a — Add "1.5" entry to `PHASES` dict (between "1" and "2" entries, lines 32-54)

```python
"1.5": {
    "name": "Pre-fill Merge",
    "script": WORKFLOW / "pre_fill_merge.py",
    "output_check": BASE_DIR / "artifacts/enriched_queue_prefilled.csv",
    "needs_api": False,
    "description": "enriched_queue → enriched_queue_prefilled.csv (+pre_formulation_code hint)",
},
```

#### 4b — Add "1.5" to `argparse choices` (line ~106)

```python
# BEFORE
choices=["0.5", "1", "2"]
# AFTER
choices=["0.5", "1", "1.5", "2"]
```

#### 4c — Insert "1.5" into `phase_order` list (line ~138)

```python
# BEFORE
phase_order = ["0.5", "1", "2"]
# AFTER
phase_order = ["0.5", "1", "1.5", "2"]
```

#### 4d — Add "1.5" to credential pre-check at line ~126

```python
# BEFORE (line ~126) — credential warning check
if not args.no_llm and args.from_phase in ["0.5", "1", "2"]:
# AFTER
if not args.no_llm and args.from_phase in ["0.5", "1", "1.5", "2"]:
```

This ensures `--from-phase 1.5` triggers the AWS credential early-warning if Phase 2 will follow.

`print_status()` iterates `PHASES.items()` — no change needed there.

---

### Step 5: Add hint context note to `agents/formulation_type_classifier.md` (A1 prompt)

Add a brief note in the A1 agent's input field description:

```
pre_formulation_code: rule-extracted hint from product name or SDS Section 1
(EC/SC/WP/etc.). Treat as supporting evidence only — override if ingredient
text or SDS content contradicts it.
```

---

## Acceptance Criteria

- [ ] `workflow/pre_fill_merge.py` exists and is importable as a sibling module
- [ ] `python workflow/pre_fill_merge.py` writes `artifacts/enriched_queue_prefilled.csv` with `pre_formulation_code` non-null ≥ 250/1675 rows
- [ ] `extract_formulation_features.py` `base_cols` list contains `"pre_formulation_code"` (line ~98)
- [ ] `AGENT_INPUT_COLS["type"]` (line ~120) contains `"pre_formulation_code"`
- [ ] `from pre_fill_merge import add_pre_formulation_code` present in `extract_formulation_features.py` (sibling import — no `workflow.` prefix)
- [ ] After Phase 2: `formulation_characteristics_output.csv` has columns: `formulation_code`, `physical_form`, `product_category`, `concentration_type`, `epa_reg_number`, `regulatory_jurisdiction`, `registration_status`
- [ ] Pair integrity: `formulation_code` non-null & != 'UNKNOWN' → all 3 supporting fields non-null (0 violations)
- [ ] `phase_order` in `run_pipeline.py` = `["0.5", "1", "1.5", "2"]`
- [ ] `python workflow/run_pipeline.py --status` shows Phase 1.5 as a tracked phase

---

## Risks & Mitigations

| Risk | Status | Mitigation |
|------|--------|------------|
| `pre_formulation_code` silently dropped (base_cols whitelist) | **Eliminated** | Step 2a adds to base_cols; Step 2c inline call applies regardless of CSV source |
| `ModuleNotFoundError: No module named 'workflow'` | **Eliminated** | Sibling import `from pre_fill_merge import ...` (workflow/ has no `__init__.py`) |
| `KeyError: 'formulation_code'` in pair integrity block | **Eliminated** | All Step 3 references use `output_df`, not `df` |
| Stale mask causes false assertion failure | **Eliminated** | Mask refreshed after correction before asserting |
| Encoding mismatch corrupts Formulation_ID | **Eliminated** | pre_fill_merge.py uses utf-8-sig; inline path bypasses CSV entirely |
| `--from-phase 1.5` skips credential pre-check | **Eliminated** | Step 4d adds "1.5" to the credential check condition |
| A1 returns formulation_code but omits supporting fields | Medium | Step 3 enforcement clears formulation_code rather than emit partial pair |
| AWS Bedrock throttling on 34 batches | Medium | Existing retry logic + extraction_cache/ resumability |
| WDG vs WG regex ambiguity | Low | FORM_CODES longest-first ordering (WDG before WG) |
| Cache invalidation after adding pre_formulation_code hint | Low | Acceptable: existing cache entries lack hint; `--force` regenerates. Document in run_pipeline.py help text |

---

## Verification Steps

```bash
# 1. Run Phase 1.5 standalone
python workflow/pre_fill_merge.py
# Expected: "pre_formulation_code: NNN/1675 rows filled (NN.N%)" where NNN ≥ 250

# 2. Verify importable callable (sibling import — run from workflow/ dir)
cd /Users/hanseoyun/Desktop/formulation/workflow
python -c "
from pre_fill_merge import add_pre_formulation_code
import pandas as pd
from pathlib import Path
df = pd.read_csv('../artifacts/enriched_queue.csv', low_memory=False, encoding='utf-8-sig')
df2 = add_pre_formulation_code(df)
assert 'pre_formulation_code' in df2.columns
filled = df2['pre_formulation_code'].notna().sum()
assert filled >= 250, f'Only {filled} rows filled, expected >=250'
print(f'OK: {filled} rows filled')
"
cd ..

# 3. Verify changes in extract_formulation_features.py
grep -n 'pre_formulation_code' workflow/extract_formulation_features.py
# Expected: >=3 hits (base_cols, AGENT_INPUT_COLS["type"], add_pre_formulation_code call)
grep -n 'from pre_fill_merge import' workflow/extract_formulation_features.py
# Expected: 1 hit (sibling import, no 'workflow.' prefix)

# 4. Verify run_pipeline.py phase registration
python -c "
import sys; sys.path.insert(0, 'workflow')
# Patch argv to avoid argparse triggering
import sys; sys.argv = ['run_pipeline.py', '--status']
exec(open('workflow/run_pipeline.py').read().split('if __name__')[0])
assert '1.5' in PHASES
assert '1.5' in phase_order
assert phase_order == ['0.5', '1', '1.5', '2']
print('OK: Phase 1.5 registered')
"

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

**Decision:** `pre_fill_merge.py` as callable-first sibling module; sibling import (`from pre_fill_merge import`) inside `extract_formulation_features.py`; pair integrity enforced on `output_df` after `pd.concat`.

**Drivers:** pair integrity, column whitelist safety, minimum-diff (no `__init__.py` addition), no silent failures.

**Alternatives considered:**
- Package import with `__init__.py`: rejected — adds a new file, changes project structure, unnecessary given sibling import works.
- `sys.path.insert` workaround: rejected — mutates path in library file, non-idiomatic.
- Pure CSV pipeline: rejected (v1 review) — silent column-whitelist and encoding risks confirmed.
- Inline-only in Phase 2 (Option B): rejected — untestable without AWS credentials.

**Why chosen:** Sibling import is the minimal change compatible with the existing `workflow/` directory structure (no `__init__.py`). Callable-first pattern eliminates CSV boundary risk while preserving standalone inspection capability.

**Consequences:**
- Existing `extraction_cache/` entries lack `pre_formulation_code` context — use `--force` to regenerate with hint if needed.
- `enriched_queue_prefilled.csv` is inspection-only; downstream processes should not hard-depend on it.

**Follow-ups:** PDF regex improvement (deferred), Phase 3 Excel integration, cache invalidation docs.

---

## Changelog
- v1: Initial draft
- v2: Architect/Critic v1 feedback — base_cols fix, symbol correction, inline callable, pair integrity, run_pipeline.py concrete steps, encoding
- v3: Architect iteration 2 feedback:
  - Blocker fixed: `from pre_fill_merge import` (sibling, not package import — no `__init__.py`)
  - Blocker fixed: All Step 3 references use `output_df` (not `df` which is the input queue)
  - Step 4 gains 4th change: credential pre-check at line ~126 updated with "1.5"
  - Verification scripts updated to use sibling import from workflow/ directory
  - Step 2a BEFORE block replaced with plain prose (actual base_cols has 12 entries; diff-style block was misleading)
