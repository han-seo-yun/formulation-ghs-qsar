# Crop Protection Agents — GHS Toxicity Classification QSAR

A QSAR pipeline that predicts GHS toxicity classification for crop protection products.
Binary classification; two endpoints: **eye irritation** and **skin irritation**.

The model is **split into two layers** (v7, 2026-09-09).

| Layer | Unit | Labels | Features | Entry point → output |
|---|---|---|---|---|
| **Substance model** | one substance (folded on the InChIKey skeleton block; 661 substances, 398 labelled) | per-ingredient GHS categories (Phase 1 collection + independent survey) | structure-derived only — RDKit descriptors, structural alerts, MACCS, Morgan | `run_model_ingredient.py` → `04_모델산출물/v7_성분모델/` |
| **Formulation model** | one formulation (1,675) | formulation GHS (L1 canonical → L2 jurisdiction projection) | 6 aggregate moments over ingredient descriptors + per-role concentration sums + surfactant interaction terms + formulation physical properties + GHS mixture additivity (CT) | `run_model_formulation.py` → `04_모델산출물/v7_제형모델/` |

The two layers predict different things (intrinsic hazard of a substance vs. classification of a
mixture). Their n, prevalence, and CV unit all differ, so **their metrics are not directly
comparable**.

## The four headline models (single cross-jurisdiction basis)

Each layer splits by endpoint, giving four models. The headline basis is fixed to **`K_REACH`**.
The four jurisdictions differ by exactly two switches — "is eye Category 2B a classification?" and
"is skin Category 3 a classification?" — and the only jurisdictions satisfying both simultaneously
are `K_REACH` and `US_OSHA`, whose projection tables are identical. This basis therefore loses
nothing when projected onto the Korean and US jurisdictions, and its eye mapping also matches the
UN GHS source text.

| Model | Jurisdiction label set | n | Prevalence | ROC-AUC | MCC |
|---|---|---|---|---|---|
| substance × eye | UN_GHS+K_REACH+US_OSHA | 398 | 0.445 | 0.754 | 0.438 |
| substance × skin | identical across all four | 398 | 0.319 | 0.775 | 0.395 |
| formulation × eye | UN_GHS+K_REACH+US_OSHA | 1,044 | 0.683 | 0.675 | 0.246 |
| formulation × skin | EU_CLP+K_REACH+US_OSHA | 1,059 | 0.336 | 0.773 | 0.390 |

**This basis was not chosen for performance.** On eye, this basis (2B = positive, prevalence
0.683) gives AUC 0.675, whereas the `EU_CLP` basis (2B = negative, prevalence 0.370) gives 0.763.
The two bases define different prediction targets, so picking whichever yields a higher AUC is not
a valid argument. Every other combination of jurisdiction, missing-data protocol, and CT arm is
kept in the same metrics file as supplementary rows with `대표` (headline) = 0, so switching the
basis needs no re-run. The statutory basis of each jurisdiction and the full category → label
projection tables are written to `규제처리_명시.md` in each output directory.

## Missing-data handling

`nan0` (fill missing with 0) was the default through v6 and is now kept **only as a reproduction
control**. The headline protocol is `native_복원` for the formulation layer and `native` for the
substance layer.

| Protocol | Meaning |
|---|---|
| `nan0` | Fill missing cells with 0. This raises the zero fraction of the formulation matrix from 38.8% to 58.4% |
| `native` | Leave missing as missing; rely on the native NaN split in sklearn ≥ 1.4 trees |
| `native_복원` | `native` plus restoration of missing values that the build step froze into 0 (formulation layer only) |

`audit_zero_vs_missing.py` audits every feature. **Exactly two columns had missing values frozen
into 0 at build time**: `f_pct_surf_total` and `f_surf_anionic_nonionic` (281 rows each, all with
unknown composition). The idiom `row["f_pct_surf_anionic"] or 0.0` in `build_input_v5.py` converts
`None` to `0.0`, so all four constituent columns stay missing while the sum becomes 0 — and no
missing-flag column (`*_isna`) was ever generated for it. Every other zero in the matrices is a
real observation ("composition known and it contains no surfactant", "single-ingredient
formulation, so the descriptor spread is 0", "no structural alert present"). The substance matrix
contains no imputed zeros at all. The build scripts are version-pinned and not edited; the
restoration is applied as a model-side protocol.

**Skin sensitization is not used for training.** It was not deleted: everything up to v6 is frozen
under `04_모델산출물/v7_감작/` (`archive_sens.py`). The `f_ct_sens_*` columns do remain as features
of the eye and skin models, because they are mixture-additivity descriptors, not labels.

> Private repository. Contains real team member names, data extracted from SDS documents, and
> unpublished research.

## Directories

| Path | Contents |
|---|---|
| `01_파이프라인/` | Data build and measurement scripts. Build entry point is `build_input_v6.py`; the shared model module is `lib_model.py` |
| `04_모델산출물/input_dataset_v6.xlsx` | Current model input dataset (1,675 formulations × 608 columns) |
| `04_모델산출물/v7_성분모델/` | Substance-level metrics, `물질_라벨대장.csv` (substance label ledger), `라벨충돌_물질.csv` (label conflicts) |
| `04_모델산출물/v7_제형모델/` | Formulation-level metrics. A lock verifies agreement with the v6 figures to within 1e-9 |
| `04_모델산출물/v7_감작/` | Frozen sensitization record. Not trained on |
| `04_모델산출물/v7_결측감사/` | Zero-vs-missing audit: per-column profile and the list of imputed zeros |
| `04_모델산출물/v6_통합/` | Consolidated outputs from the four team members (`팀원통합_260908.xlsx`) |
| `05_원본보관_260908/` | The four as-submitted originals. **L0 immutable — never edited** |
| `05_제안/` | Proposals for model performance improvement |
| `dataset_배정_260904.xlsx` | Work assignment and review ledger. Open decisions are on the `총책임자_결정_9건` sheet |

Excluded via `.gitignore`: the v2–v5 generation datasets, the highlighted SDS audit PDF dump
(146 MB), Word report documents (`*.docx`), and the collection harness (separate repository,
[`formulation_harness`](https://github.com/han-seo-yun/formulation_harness)).

## Build

```bash
python3 01_파이프라인/build_input_v6.py
```

The script reads the originals in `05_원본보관_260908/` directly and produces
`04_모델산출물/input_dataset_v6.xlsx`. The `ROOT` path needs adjusting for the local environment.

## Train and measure

```bash
cd 01_파이프라인
python3 audit_zero_vs_missing.py    # zero-vs-missing audit — seconds; cross-checks the restore list against lib_model
python3 run_model_ingredient.py     # substance level — ~1.5 min
python3 run_model_formulation.py    # formulation level — ~4.5 min, v6 reproduction lock partway through
python3 archive_sens.py             # freeze the sensitization record (no training, originals untouched)
```

All four scripts read their inputs read-only and write only into their own output directory.

## Data rules

These rules are enforced by asserts inside the pipeline. Do not work around them.

- **Never invent a value.** Missing pH is not imputed to `0` or `7`, and is not filled in by
  estimation. Missing stays missing. Applying `nan_to_num(nan=0)` right before training also
  invents values, so the headline protocols do not use it.
- **Distinguish zero from missing.** "Concentration sum 0 because the formulation contains no
  surfactant" is an observation; "0 because the composition is unknown" is an invented value. The
  test is **whether that column's information source exists for that row**
  (`audit_zero_vs_missing.py`). Re-run this audit whenever features are added — an assert halts if
  the audit result disagrees with `lib_model.ZERO_IS_MISSING`.
- **The headline basis is the single jurisdiction `K_REACH`** (`lib_model.CANON_JUR`). Other
  jurisdictions are not deleted; they are measured alongside as supplementary rows. Changing the
  basis means changing only this constant.
- **Only as-supplied pH is accepted.** The GHS non-additivity exceptions for strong acids
  (pH ≤ 2) and strong bases (pH ≥ 11.5) are decided solely from pH measured on the product as-is.
  The pH of a diluted aqueous solution varies logarithmically with concentration and cannot
  substitute; literature values for other products or for active ingredients are not values of
  this formulation.
- **Minimum-frequency S11 readings are never used as features.** They were read from the same
  documents as the labels, so using them as features leaks the answer. They serve only as label
  correction candidates.
- **Version-pinned scripts are never edited**: `build_input_v3/v4/v5`, `smoke_baseline_v3/v4`,
  `active_rank`, `measure_v5_performance`, `measure_skinmap_and_ct_augment`,
  `measure_full_metrics_v6`, `measure_sens_arm_grid`. They are the reproduction basis for past
  measurements. The last two are the only reproduction path for the v6 measurements that included
  sensitization, so they stay even after sensitization was dropped from training.
- **Labels are managed in three layers**: L0 (original, byte-immutable) → L1 (canonical) →
  L2 (per-jurisdiction projection).
- **Leakage prevention**: the formulation model groups on `group_key` (587 groups of formulations
  sharing an active ingredient); the substance model groups on the Murcko scaffold. Both use
  StratifiedGroupKFold, 5 folds × 5 seeds. In the substance model the CV unit is the substance
  itself, so duplication across formulations cannot leak by construction. The primary metrics are
  ROC-AUC and MCC; F1 is not used as a headline metric. The threshold is fixed at 0.5 (the
  post-hoc optima of 0.298–0.400 are deliberately not used).
- **Substance-model features come from structure only.** Concentrations, formulation context
  (`formulation_type_*`), and label-derived indicators (`has_ghs_label` and friends) are excluded
  through the explicit lists in `lib_model.ING_DROP_*`. Telling a model that predicts a
  substance's intrinsic hazard which formulation that substance ended up in blurs the prediction
  target.
- **Eye category `2` from the independent survey is excluded from the EU_CLP layer.** The EU
  splits 2A (classified) from 2B (not classified), and this value carries no subdivision, so it
  cannot be adjudicated. It is not arbitrarily assigned to either side (60 substances excluded in
  the substance model).

## Artifact promotion states

| State | Meaning |
|---|---|
| `ACTIVE` | Actually used by a model in training |
| `STAGED` | Merged into the dataset but unused in training. Promotion to ACTIVE is a lead's decision |
| `STAGED_격리` | Material that could change labels. Never used as a feature |

The only `ACTIVE` source at present is the independent per-ingredient GHS survey. The formulation
model uses it for CT additivity augmentation (the A2/A3 arms); the substance model uses it as
**labels**. pH remains `STAGED` because the strong-acid/strong-base non-additivity gate (D1) is
not implemented.
