# RALPLAN: Formulation Missing Data Resolution — v2
## Status: DRAFT v2 (incorporating Architect + Critic feedback from v1)

---

## RALPLAN-DR Summary

### Principles
1. **Pipeline idempotency** — every step can be re-run safely; existing outputs preserved via cache
2. **Pre-fill enriches, LLM decides** — rule-based pre-fill is hint context only; LLM output is authoritative
3. **Pair integrity as first-class constraint** — formulation_code and its 3 supporting fields are written atomically or not at all
4. **Minimum-diff changes** — touch as few existing files as possible
5. **No silent failures** — column whitelist, encoding, and assertion logic must be proven correct before merge

### Decision Drivers
1. **AWS Bedrock availability** — existing `extract_formulation_features.py` is kept as-is except for targeted additions
2. **Pair integrity requirement** — A1 must produce formulation_code + 3 supporting fields together; partial writes are prohibited
3. **Pre-fill as LLM hint** — `pre_formulation_code` in A1 prompt context improves classification without schema changes

### Viable Options for Phase 1.5

#### Option A: Standalone module with inline callable (Recommended — revised from v1)
- `workflow/pre_fill_merge.py` exports both `add_pre_formulation_code(df)` function AND a `main()` that writes a standalone CSV
- Phase 2 calls `add_pre_formulation_code(df)` in-memory inside `load_review_queue()` — eliminates CSV serialization boundary risk
- Standalone CSV `enriched_queue_prefilled.csv` is still written (for human inspection), but Phase 2 does not depend on it
- **Pros:** isolated + testable standalone; no hard CSV dependency; encoding risk eliminated; `base_cols` whitelist gap automatically resolved; inspectable intermediate artifact preserved
- **Cons:** import coupling between Phase 2 and pre_fill_merge module (low risk, same package)

#### Option B: Inline pre-fill only inside `extract_formulation_features.py`
- All logic inside Phase 2 script, no separate module
- **Pros:** zero new files; zero CSV boundary
- **Cons:** pre-fill logic untestable without AWS credentials; violates single-responsibility; not inspectable without running Phase 2

#### Option C: Pure CSV pipeline (Option A v1)
- Phase 2 reads `enriched_queue_prefilled.csv` as hard dependency
- **Cons (confirmed by Architect/Critic):** column whitelist silently drops `pre_formulation_code` if `base_cols` not updated; encoding mismatch corrupts `Formulation_ID`; Phase 2 blocks if Phase 1.5 CSV missing — all three risks confirmed against source
- **Rejected**

**Chosen:** Option A (revised) — standalone callable + in-memory use in Phase 2.

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

```python
# BEFORE (extract_formulation_features.py lines 91-98, base_cols list)
base_cols = [
    "Formulation_ID", "Formulation_Name",
    "Formulation_Ingredients", "Formulation_Ingredients_CAS",
    "Formulation_Ingredients_Pct", "Ingredient_Count",
    "Ingredient_Source",
]

# AFTER — add pre_formulation_code as last entry
base_cols = [
    "Formulation_ID", "Formulation_Name",
    "Formulation_Ingredients", "Formulation_Ingredients_CAS",
    "Formulation_Ingredients_Pct", "Ingredient_Count",
    "Ingredient_Source",
    "pre_formulation_code",          # Phase 1.5 hint column (filled by fallback loop if absent)
]
```

The `usecols` lambda at line 108 will now admit `pre_formulation_code`. The fallback loop at lines 112-114 initialises it as `""` if the old `enriched_queue.csv` is used — safe for backwards compatibility.

#### 2b — Add `pre_formulation_code` to `AGENT_INPUT_COLS["type"]` (lines 119-124)

```python
# BEFORE (extract_formulation_features.py lines 119-128, AGENT_INPUT_COLS dict, "type" key)
AGENT_INPUT_COLS: dict[str, list[str]] = {
    "type": [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "sec1_text",
        # ... existing columns
    ],
    ...
}

# AFTER — add pre_formulation_code to "type" entry
    "type": [
        "Formulation_ID", "Formulation_Name",
        "Formulation_Ingredients", "sec1_text",
        "pre_formulation_code",     # formulation type hint from PDF + name pattern
        # ... existing columns unchanged
    ],
```

#### 2c — Call `add_pre_formulation_code` inline inside `load_review_queue()` (after line 113)

```python
# In load_review_queue(), after the fallback-fill loop (lines 112-114),
# add in-memory pre-fill (works regardless of which CSV variant is read):
from workflow.pre_fill_merge import add_pre_formulation_code   # add at top of file
# ... inside load_review_queue(), after line 113:
df = add_pre_formulation_code(df)   # idempotent — safe to call even if column already set
```

This ensures `pre_formulation_code` is always present in `work_df` regardless of whether `enriched_queue_prefilled.csv` or `enriched_queue.csv` is the input — eliminates all CSV boundary dependency.

---

### Step 3: Pair integrity enforcement in `extract_formulation_features.py` (after line 321)

**Placement:** After `pd.concat` with cancer rows at line 321 (covers full output including Cancer defaults).

```python
# AFTER pd.concat (line 321), before writing output CSV:
PAIR_FIELDS = ['physical_form', 'product_category', 'concentration_type']

# Exclude 'UNKNOWN' sentinel (CANCER_DEFAULTS + LLM-failure rows)
mask = df['formulation_code'].notna() & (df['formulation_code'] != 'UNKNOWN')
violations = mask & df[PAIR_FIELDS].isnull().any(axis=1)

if violations.any():
    # Nullify formulation_code for partial pairs — prefer no code over lying code
    df.loc[violations, 'formulation_code'] = None
    print(f"[WARN] pair integrity: {violations.sum()} rows cleared "
          f"(formulation_code present but supporting fields missing)")

# Refresh mask after correction, then assert
mask = df['formulation_code'].notna() & (df['formulation_code'] != 'UNKNOWN')
n_violations = df.loc[mask, PAIR_FIELDS].isnull().any(axis=1).sum()
assert n_violations == 0, f"Pair integrity FAILED: {n_violations} violations after auto-fix"
```

Key fixes vs v1:
- Excludes `'UNKNOWN'` from the mask (Cancer/LLM-failure sentinel)
- Placed AFTER `pd.concat` to validate full output
- **Refreshes `mask` after correction** before the assertion (v1 stale-mask bug fixed)

---

### Step 4: Register Phase 1.5 in `workflow/run_pipeline.py` — 3 concrete changes

**File:** `workflow/run_pipeline.py`

#### 4a — Add "1.5" entry to `PHASES` dict (lines 32-54)

```python
# Insert between the "1" entry and the "2" entry in the PHASES dict:
"1.5": {
    "name": "Pre-fill Merge",
    "script": WORKFLOW / "pre_fill_merge.py",
    "output_check": BASE_DIR / "artifacts/enriched_queue_prefilled.csv",
    "needs_api": False,
    "description": "enriched_queue → enriched_queue_prefilled.csv (+pre_formulation_code hint)",
},
```

#### 4b — Add "1.5" to `argparse choices` (line 107)

```python
# BEFORE
choices=["0.5", "1", "2"]
# AFTER
choices=["0.5", "1", "1.5", "2"]
```

#### 4c — Insert "1.5" into `phase_order` list (line 138)

```python
# BEFORE
phase_order = ["0.5", "1", "2"]
# AFTER
phase_order = ["0.5", "1", "1.5", "2"]
```

`print_status()` iterates `PHASES.items()` so it auto-updates — no change needed there.

---

### Step 5: Update `agents/formulation_type_classifier.md` (A1 prompt context)

Add a note in the A1 agent's system prompt that `pre_formulation_code` is a hint field:

```
# Hint field
pre_formulation_code: may contain a rule-extracted formulation type code (EC/SC/WP/etc.)
from the product name or SDS Section 1. Treat as supporting evidence, not ground truth.
Override if the full ingredient/section text contradicts it.
```

This prevents the LLM from blindly copying the hint when it has better evidence.

---

## Acceptance Criteria

- [ ] `workflow/pre_fill_merge.py` exists and is importable as a module
- [ ] `python workflow/pre_fill_merge.py` writes `artifacts/enriched_queue_prefilled.csv` with `pre_formulation_code` non-null ≥ 250/1675 rows
- [ ] `python -c "from workflow.pre_fill_merge import add_pre_formulation_code"` succeeds
- [ ] `extract_formulation_features.py` `base_cols` (line ~98) contains `"pre_formulation_code"`
- [ ] `AGENT_INPUT_COLS["type"]` (line ~120) contains `"pre_formulation_code"`
- [ ] After Phase 2: `formulation_characteristics_output.csv` has columns: `formulation_code`, `physical_form`, `product_category`, `concentration_type`, `epa_reg_number`, `regulatory_jurisdiction`, `registration_status`
- [ ] Pair integrity: `formulation_code` non-null & != 'UNKNOWN' → all 3 supporting fields non-null (assertion passes, 0 violations)
- [ ] `phase_order` in `run_pipeline.py` = `["0.5", "1", "1.5", "2"]`
- [ ] `python workflow/run_pipeline.py --status` shows Phase 1.5 as a tracked phase

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `pre_formulation_code` dropped before A1 prompt (base_cols whitelist gap) | **Eliminated** | Step 2a adds to base_cols; Step 2c applies inline regardless of CSV source |
| Encoding mismatch corrupts Formulation_ID | **Eliminated** | pre_fill_merge.py uses utf-8-sig for both read and write; inline path bypasses CSV entirely |
| A1 returns formulation_code but omits some supporting fields | Medium | Step 3 enforcement block clears formulation_code rather than emit partial pair |
| Stale mask causes false assertion failure | **Eliminated** | Step 3 refreshes mask after correction before asserting |
| `enriched_queue_prefilled.csv` missing and Phase 2 reads old CSV | **Eliminated** | Phase 2 uses inline add_pre_formulation_code() regardless of which CSV file is present |
| AWS Bedrock throttling on 34 batches | Medium | Existing retry logic + extraction_cache/ resumability |
| WDG vs WG regex ambiguity | Low | FORM_CODES longest-first ordering (WDG before WG) |
| Corrupted enriched_queue_prefilled.csv + Phase 2 cache inconsistency | Low | Phase 2 does not hard-depend on the file; cache keyed by Formulation_ID + agent, not file mtime |
| Cancer rows with `formulation_code="UNKNOWN"` failing pair check | **Eliminated** | Mask excludes 'UNKNOWN' sentinel; Cancer defaults set all pair fields to non-null strings |

---

## Verification Steps

```bash
# 1. Run Phase 1.5 standalone
python workflow/pre_fill_merge.py
# Expected output: "pre_formulation_code: NNN/1675 rows filled (NN.N%)" where NNN ≥ 250
# Expected file: artifacts/enriched_queue_prefilled.csv

# 2. Verify importable callable
python -c "
from workflow.pre_fill_merge import add_pre_formulation_code
import pandas as pd
df = pd.read_csv('artifacts/enriched_queue.csv', low_memory=False, encoding='utf-8-sig')
df2 = add_pre_formulation_code(df)
assert 'pre_formulation_code' in df2.columns
filled = df2['pre_formulation_code'].notna().sum()
assert filled >= 250, f'Only {filled} rows filled, expected ≥250'
print(f'OK: {filled} rows filled')
"

# 3. Verify base_cols and AGENT_INPUT_COLS changes
grep -n 'pre_formulation_code' workflow/extract_formulation_features.py
# Expected: ≥3 lines (base_cols, AGENT_INPUT_COLS["type"], add_pre_formulation_code call)

# 4. Verify run_pipeline.py phase registration
python -c "
import sys; sys.path.insert(0, '.')
from workflow.run_pipeline import PHASES, phase_order
assert '1.5' in PHASES, 'Phase 1.5 not in PHASES dict'
assert '1.5' in phase_order, 'Phase 1.5 not in phase_order'
idx = phase_order.index('1.5')
assert phase_order[idx-1] == '1' and phase_order[idx+1] == '2', 'Phase order wrong'
print('OK: Phase 1.5 registered correctly')
"

# 5. Set AWS credentials and run integrated Phase 1.5 → Phase 2
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=...
python workflow/run_pipeline.py --from-phase 1.5

# 6. Verify pair integrity and output after Phase 2
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

# 7. Spot-check hint usage (5 rows where pre_formulation_code was set)
python -c "
import pandas as pd
q = pd.read_csv('artifacts/enriched_queue.csv', low_memory=False, encoding='utf-8-sig')
o = pd.read_csv('artifacts/formulation_characteristics_output.csv')
from workflow.pre_fill_merge import add_pre_formulation_code
q = add_pre_formulation_code(q)
merged = q[q['pre_formulation_code'].notna()].merge(
    o[['Formulation_ID','formulation_code']],
    on='Formulation_ID', how='inner')
agree = (merged['pre_formulation_code'] == merged['formulation_code']).mean()
print(f'Hint agreement rate: {agree*100:.1f}% (informational, not a gate)')
merged.head(5)[['Formulation_Name','pre_formulation_code','formulation_code']].to_string()
"
```

---

## ADR

**Decision:** Implement Phase 1.5 as a callable-first module (`pre_fill_merge.py`) applied inline inside `load_review_queue()`, with a standalone CSV-producing `main()` for inspection.

**Drivers:**
1. Pair integrity — formulation_code must always be accompanied by 3 supporting fields
2. Column whitelist — `base_cols` in `load_review_queue()` is the upstream gate; pre-fill must be added there first
3. Minimum-diff — no Phase 2 architectural change; only targeted additions

**Alternatives considered:**
- Pure CSV pipeline (Option C / original Option A): Rejected — column whitelist and encoding risks confirmed by Critic/Architect against source. Silent data loss with no error is unacceptable.
- Inline only in Phase 2 (Option B): Rejected — untestable without AWS credentials; pre-fill logic should be independently verifiable.

**Why chosen:** Option A (revised) — callable function eliminates the CSV serialization boundary as a hard dependency while preserving the inspectable CSV artifact and the standalone-test benefit. Adds one import to `extract_formulation_features.py` (low coupling risk; same package).

**Consequences:**
- `extraction_cache/` entries from previous Phase 2 runs do NOT contain `pre_formulation_code` context. If Phase 2 was partially run before, cached results lack the hint. Decision: treat as acceptable — cached results are the best available output; re-running with `--force` would regenerate with the hint.
- `enriched_queue_prefilled.csv` is a non-authoritative inspection artifact. Downstream processes must NOT depend on it; they should use `enriched_queue.csv` + the callable.

**Follow-ups:**
- PDF regex improvement (Phase 0.5 re-run): deferred; revisit after observing Phase 2 fill rates
- Phase 3 Excel integration: separate task after Phase 2 completion
- Cache invalidation policy: document `--force` as the mechanism when hint-aware re-extraction is needed

---

## Changelog
- v1: Initial draft from deep-interview spec
- v2: Architect + Critic findings incorporated:
  - Critical: Added `pre_formulation_code` to `base_cols` (Step 2a) — silent drop fix
  - High: Corrected symbol names to `AGENT_INPUT_COLS["type"]` at line 120 (Step 2b)
  - High: Added inline callable Step 2c (eliminates CSV boundary dependency)
  - High: Replaced Step 4 pseudo-code with 3 concrete run_pipeline.py changes
  - Medium: Added encoding='utf-8-sig' to pre_fill_merge.py
  - Medium: Fixed pair integrity mask — excludes 'UNKNOWN', refreshes after correction, placed after pd.concat
  - Added integrated Phase 1.5 → Phase 2 verification step (Step 5 in verification)
  - Added A1 prompt hint documentation (Step 5)
  - Completed ADR section
  - Updated risk table — eliminated 5 risks from v1
