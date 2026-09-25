# RALPLAN: Formulation Missing Data Resolution
## Status: DRAFT (pending consensus review)

---

## RALPLAN-DR Summary

### Principles
1. **Pipeline idempotency** — every step can be re-run safely; existing outputs are preserved via cache
2. **Pre-fill enriches, LLM decides** — rule-based pre-fill is hint context, not authoritative; LLM output overrides
3. **Pair integrity as first-class constraint** — formulation_code and its 3 supporting fields are always written together or not at all
4. **Minimum-diff changes** — modify as few existing files as possible; new Phase 1.5 is additive, not replacing Phase 1 output
5. **No speculative extraction** — do not attempt EPA reg number from Formulation_Name (format is not reliably present there)

### Decision Drivers
1. **AWS Bedrock availability** — existing `extract_formulation_features.py` uses Bedrock; rewriting is out of scope
2. **Pair integrity requirement** — A1 must atomically write formulation_code + 3 supporting fields; partial writes violate the invariant
3. **Pre-fill as LLM hint** — adding `pre_formulation_code` to A1 inputs improves classification accuracy without changing the schema contract

### Viable Options for Phase 1.5

#### Option A: Standalone script `workflow/pre_fill_merge.py` (Recommended)
- New 60-line script that reads enriched_queue.csv, computes `pre_formulation_code`, writes enriched_queue_prefilled.csv
- `run_pipeline.py` calls it as Phase 1.5 before Phase 2
- **Pros:** isolated, testable, reversible, does not mutate existing enriched_queue.csv
- **Cons:** two CSV artifacts to track; Phase 2 must read the prefilled variant

#### Option B: Inline pre-fill inside `extract_formulation_features.py`
- Add pre-fill logic at the top of the Phase 2 script before batching
- **Pros:** single script, no new artifact
- **Cons:** couples pre-fill logic to LLM execution; harder to test or re-run pre-fill alone; violates single-responsibility

**Chosen:** Option A — standalone script, clean separation of concerns.

---

## Implementation Steps

### Step 1: Create `workflow/pre_fill_merge.py`
**File:** `workflow/pre_fill_merge.py` (new)

```python
"""
Phase 1.5 — Pre-fill merge
Combines sec_formulation_code (PDF-extracted) with Formulation_Name pattern
to produce pre_formulation_code hint column for Phase 2 LLM input.

Run: python workflow/pre_fill_merge.py
Output: artifacts/enriched_queue_prefilled.csv
"""
import re
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
INPUT_CSV  = BASE_DIR / "artifacts/enriched_queue.csv"
OUTPUT_CSV = BASE_DIR / "artifacts/enriched_queue_prefilled.csv"

FORM_CODES = ['WDG','RTU','ULV','EC','SC','WP','WG','FS','EW','SL',
              'SP','CS','DC','GR','TB']
_PAT = re.compile(r'\b(' + '|'.join(FORM_CODES) + r')\b')

def resolve_pre_formulation_code(row: pd.Series) -> str | None:
    # Priority 1: PDF-extracted sec_formulation_code
    val = row.get('sec_formulation_code')
    if pd.notna(val) and str(val).strip():
        return str(val).strip()
    # Priority 2: Formulation_Name pattern match
    m = _PAT.search(str(row.get('Formulation_Name', '')))
    return m.group(1) if m else None

def main():
    df = pd.read_csv(INPUT_CSV, low_memory=False)
    df['pre_formulation_code'] = df.apply(resolve_pre_formulation_code, axis=1)
    filled = df['pre_formulation_code'].notna().sum()
    print(f"pre_formulation_code: {filled}/{len(df)} rows filled ({filled/len(df)*100:.1f}%)")
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Written: {OUTPUT_CSV}")

if __name__ == '__main__':
    main()
```

**Acceptance gate:** `pre_formulation_code` non-null ≥ 250 rows.

---

### Step 2: Modify `workflow/extract_formulation_features.py` — A1 input columns

**File:** `workflow/extract_formulation_features.py`

Locate the A1 agent's input column selection (search for `formulation_type_classifier` or `A1_COLS`/`agent_1_cols`). Add `pre_formulation_code` to the column list.

Exact change pattern:
```python
# BEFORE (approximate — exact line TBD by reading the file)
A1_INPUT_COLS = ['Formulation_ID', 'Formulation_Name', 'Formulation_Ingredients',
                 'sec1_text', ...]

# AFTER
A1_INPUT_COLS = ['Formulation_ID', 'Formulation_Name', 'Formulation_Ingredients',
                 'sec1_text', 'pre_formulation_code', ...]
```

Also update the input file path from `enriched_queue.csv` → `enriched_queue_prefilled.csv`.

---

### Step 3: Add pair integrity post-processing to `extract_formulation_features.py`

After A1 results are merged into the output dataframe, add a validation + enforcement block:

```python
# Pair integrity enforcement
PAIR_FIELDS = ['physical_form', 'product_category', 'concentration_type']
mask = df['formulation_code'].notna()
violations = mask & df[PAIR_FIELDS].isnull().any(axis=1)
if violations.any():
    # Nullify formulation_code for rows where supporting fields are missing
    # (LLM returned partial output — treat as extraction failure)
    df.loc[violations, 'formulation_code'] = None
    print(f"[WARN] pair integrity: {violations.sum()} rows had formulation_code "
          f"but missing supporting fields — formulation_code cleared")
n_violations = df.loc[mask, PAIR_FIELDS].isnull().any(axis=1).sum()
assert n_violations == 0, f"Pair integrity FAILED: {n_violations} violations remain"
```

---

### Step 4: Update `workflow/run_pipeline.py` — register Phase 1.5

**File:** `workflow/run_pipeline.py`

Add Phase 1.5 between Phase 1 and Phase 2 in the orchestration sequence:

```python
# After Phase 1 completes, before Phase 2:
if current_phase <= 1.5:
    print("=== Phase 1.5: Pre-fill merge ===")
    import subprocess
    result = subprocess.run(
        ['python', 'workflow/pre_fill_merge.py'],
        cwd=BASE_DIR, check=True
    )
```

(Adapt to the existing phase-dispatch pattern in run_pipeline.py.)

---

### Step 5: Verify and run

```bash
# 1. Run Phase 1.5 standalone
python workflow/pre_fill_merge.py
# Expected: pre_formulation_code: ≥250/1675 rows filled

# 2. Check pair integrity before Phase 2
python -c "
import pandas as pd
df = pd.read_csv('artifacts/enriched_queue_prefilled.csv')
print('pre_formulation_code filled:', df['pre_formulation_code'].notna().sum())
"

# 3. Set AWS credentials and run Phase 2
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=...
python workflow/run_pipeline.py --from-phase 2

# 4. Verify pair integrity after Phase 2
python -c "
import pandas as pd
df = pd.read_csv('artifacts/formulation_characteristics_output.csv')
mask = df['formulation_code'].notna()
pair_fields = ['physical_form','product_category','concentration_type']
violations = mask & df[pair_fields].isnull().any(axis=1)
print(f'Pair integrity violations: {violations.sum()}')
print(f'formulation_code fill: {mask.sum()}/{len(df)} ({mask.mean()*100:.1f}%)')
print(f'epa_reg_number fill: {df[\"epa_reg_number\"].notna().sum()}/{len(df)}')
"
```

---

## Acceptance Criteria

- [ ] `artifacts/enriched_queue_prefilled.csv` exists with `pre_formulation_code` column
- [ ] `pre_formulation_code` non-null ≥ 250/1675 rows
- [ ] `formulation_characteristics_output.csv` has columns: `formulation_code`, `physical_form`, `product_category`, `concentration_type`, `epa_reg_number`, `regulatory_jurisdiction`, `registration_status`
- [ ] Pair integrity: `formulation_code` non-null → all 3 supporting fields non-null (0 violations in final output)
- [ ] `extraction_cache/` populated; re-run skips completed batches

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| A1 returns formulation_code but omits some supporting fields | Medium | Step 3 enforcement block: nullify formulation_code rather than emit half-pair |
| `pre_formulation_code` column missing from enriched_queue_prefilled.csv if Phase 1.5 skipped | Low | run_pipeline.py Phase 1.5 gate checks file existence before Phase 2 |
| AWS Bedrock throttling on 34 batches | Medium | Existing retry logic in extract_formulation_features.py + extraction_cache resumability |
| WDG vs WG ambiguity in name pattern | Low | Longest-match regex ordering (`WDG` before `WG` in FORM_CODES list) |
| enriched_queue_prefilled.csv drifts from enriched_queue.csv | Low | Phase 1.5 is purely additive (adds 1 column); diff check can verify |

---

## Verification Steps

1. `python workflow/pre_fill_merge.py` → confirms ≥ 250 rows filled, file written
2. `diff <(head -1 artifacts/enriched_queue.csv) <(head -1 artifacts/enriched_queue_prefilled.csv | sed 's/,pre_formulation_code//')` → headers identical except added column
3. After Phase 2: pair integrity assertion (Step 5 verification script) → 0 violations
4. Spot-check 5 rows where `pre_formulation_code` was set: confirm A1 `formulation_code` agrees or has higher-confidence alternative
5. `python workflow/run_pipeline.py --status` → Phase 1.5 and Phase 2 marked complete

---

## ADR (to be completed after Architect/Critic consensus)

**Decision:** TBD after consensus
**Drivers:** pair integrity, AWS Bedrock constraint, minimum-diff
**Alternatives considered:** inline pre-fill, post-hoc merge
**Why chosen:** TBD
**Consequences:** TBD
**Follow-ups:** PDF regex improvement (deferred), Phase 3 Excel integration

---

## Changelog
- v1: Initial draft from deep-interview spec (di-formulation-missing-2026)
