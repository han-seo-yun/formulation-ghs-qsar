export const meta = {
  name: 'sds-research-toxicity-custom',
  description: 'Toxicity-only SDS research for dataset_배정.xlsx 독성코드 sheet, with strict ingredient-match gating and concise notes.',
  whenToUse: "Given args.products (array of {row, product_name, ingredient_names, ...hints}) and args.fields=['toxicity'], researches each product's SDS Section 11 toxicity data, but ONLY records it if the SDS's Section 3 ingredients actually match the product's ingredient_names. Notes are kept to anomalies only.",
  phases: [
    { title: 'Research', detail: 'batched parallel SDS research per product for toxicity, with ingredient-match gating' },
  ],
}

// ---- Reference: standard SDS field checklists (used for every product regardless of role, for the completeness summary) ----
const FIELD_SPECS = {
  ingredients: { label_ko: '성분', sds_section: 'Section 3', fields: ['ingredient_name', 'cas_number', 'percentage', 'role_in_formulation'] },
  cas_number: { label_ko: 'CAS넘버', sds_section: 'Section 3' },
  physicochemical: {
    label_ko: '물리화학적특성', sds_section: 'Section 9',
    fields: ['physical_state', 'color', 'odor', 'pH', 'melting_freezing_point', 'initial_boiling_point', 'flash_point', 'evaporation_rate', 'flammability', 'vapor_pressure', 'vapor_density', 'relative_density_specific_gravity', 'water_solubility', 'partition_coefficient_log_kow', 'auto_ignition_temperature', 'decomposition_temperature', 'viscosity'],
  },
  toxicity: {
    label_ko: '독성정보', sds_section: 'Section 11',
    fields: ['acute_oral_toxicity_ld50', 'acute_dermal_toxicity_ld50', 'acute_inhalation_toxicity_lc50', 'skin_corrosion_irritation', 'eye_damage_irritation', 'respiratory_skin_sensitization', 'germ_cell_mutagenicity', 'carcinogenicity', 'reproductive_toxicity', 'stot_single_exposure', 'stot_repeated_exposure', 'aspiration_hazard', 'ghs_signal_word', 'ghs_hazard_statements', 'ghs_pictograms'],
  },
  formulation_code: { label_ko: '제형코드', sds_section: 'Section 1 / label' },
}

let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch (e) { resolvedArgs = null }
}

const BATCH_SIZE = (resolvedArgs && resolvedArgs.batchSize) || 8
const products = (resolvedArgs && resolvedArgs.products) || []
const fields = (resolvedArgs && resolvedArgs.fields) || ['toxicity']
if (!products.length) {
  throw new Error("Pass args.products: an array of {row, product_name, ingredient_names, ...} objects. Got: " + JSON.stringify(args).slice(0, 200))
}

const RESULT_SCHEMA = {
  type: 'object',
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          row: { type: 'integer' },
          product_name: { type: 'string' },
          resolution: { type: 'string', enum: ['found', 'no_code_in_sds', 'ingredient_mismatch', 'unresolved'] },
          field_values: { type: 'object', description: 'ONLY present if resolution=found AND ingredients were confirmed to match. Keys: toxicity -> object keyed by the standard parameter names (ghs_signal_word, ghs_hazard_statements, acute_oral_toxicity_ld50, etc). Omit entirely (or leave empty object) if not confirmed.' },
          sds_summary: {
            type: 'object',
            description: 'Completeness checklist for the SDS/source ultimately checked (for internal QA only, not written to the memo column).',
            properties: {
              is_finished_product: { type: 'boolean' },
              is_single_substance: { type: 'boolean' },
              has_ingredient_info: { type: 'boolean' },
              has_toxicity_info: { type: 'boolean' },
              ingredients_match: { type: 'boolean', description: "true only if you positively verified the SDS Section 3 ingredient list matches this product's ingredient_names" },
            },
          },
          source_url: { type: ['string', 'null'], description: 'Set ONLY if you found a BETTER/more complete SDS than the given hint (tox_source_url/doc_source). Otherwise null.' },
          better_source_found: { type: 'boolean' },
          source_mismatch_found: { type: 'boolean', description: 'true if the given hint URL is for a different/unrelated substance than ingredient_names' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
          notes: { type: 'string', description: 'CONCISE Korean note, ANOMALIES ONLY (e.g. "성분 불일치", "로그인 필요", "Section 11 없음", "대체 검색어로 발견"). Leave empty string "" if nothing unusual - do NOT write a generic completeness summary here.' },
        },
        required: ['row', 'product_name', 'resolution', 'confidence', 'notes', 'sds_summary'],
      },
    },
  },
  required: ['results'],
}

function researchPrompt(batch) {
  const spec = FIELD_SPECS.toxicity

  return `You are an SDS research agent collecting toxicity data (SDS Section 11) for pesticide/biocide/chemical products, for a Korean toxicology database review sheet (독성코드). Work is parallelized across many agents - research the batch of products below thoroughly and return structured data. This is a RESEARCH-ONLY pass; a separate verification step happens later.

## Field to collect: toxicity (독성정보) - SDS Section 11
Extract these parameters where available: ${spec.fields.join(', ')}
Prioritize: GHS 신호어(signal word), H코드(hazard statements), and acute toxicity classification/category - these are the headline values for this sheet's 추출정보 column.

## CRITICAL GATING RULE - only record toxicity data if ingredients actually match
Each product has an \`ingredient_names\` field (the actual active ingredients of THIS product) and often a hint URL (\`tox_source_url\`/\`doc_source\`). Many hint URLs in this dataset are WRONG - they point to SDSs for unrelated substances.
1. Find the SDS for the product (start from the hint URL if present, but verify it).
2. Check the SDS's Section 3 (composition) ingredient list against \`ingredient_names\`.
3. ONLY if the ingredients genuinely match (same active substance(s), not just a similar name) may you populate \`field_values.toxicity\` and set resolution="found" with sds_summary.ingredients_match=true.
4. If the hint SDS is for a different/unrelated substance: set source_mismatch_found=true, try to find the correct SDS for THIS product's actual ingredient_names via a fresh search (manufacturer site, EPA label, PubChem/other SDS aggregators for the correct CAS/name). If you find the correct one and it matches, proceed as in step 3 (mention "대체 검색어로 발견" style note only if you switched search strategy). If you cannot find any matching SDS after a real attempt, set resolution="ingredient_mismatch", leave field_values empty, and put a short note like "성분 불일치, 대체 SDS 못 찾음".
5. If you find the correct, matching SDS but its Section 11 genuinely has no toxicity classification/code (e.g. explicitly "not classified" or the section is blank), set resolution="no_code_in_sds", leave field_values empty (do not invent a code), and note only if something is unusual (e.g. "Section 11 정보없음"; if literally nothing unusual beyond the absence itself, notes can be "").
6. Use resolution="unresolved" ONLY if you could not find ANY usable SDS at all for this product after a real search attempt.

## Source URL field - only for a genuinely BETTER source
Set \`source_url\` ONLY if you searched further and found a more complete/more appropriate SDS than what the hint already pointed to (set better_source_found=true too). If the existing hint SDS is already fine, or you found nothing better, leave source_url=null - do NOT restate the existing hint URL, and do NOT fill it with a source that doesn't clearly beat the original.

## Notes - anomalies ONLY, concise
The \`notes\` field feeds a "메모" column that must stay terse. Write a short Korean phrase ONLY for genuine anomalies: ingredient mismatch, login-walled SDS, missing Section 11, needed an alternate search term, discontinued product, etc. If nothing unusual happened, notes = "" (empty string). Do NOT write routine completeness summaries like "성분정보O, 독성정보O" into notes - that belongs in sds_summary only, which is for internal QA and is not written to the sheet.

## Products to research (JSON)
${JSON.stringify(batch, null, 2)}

Return your findings via the required structured output.`
}

phase('Research')
const batches = []
for (let i = 0; i < products.length; i += BATCH_SIZE) batches.push(products.slice(i, i + BATCH_SIZE))
log(`Researching ${products.length} products x [toxicity] in ${batches.length} batches of up to ${BATCH_SIZE}`)

const batchResults = await parallel(batches.map((batch, i) => () =>
  agent(researchPrompt(batch), { label: `research-batch-${i}`, phase: 'Research', schema: RESULT_SCHEMA })
))

const flat = batchResults.filter(Boolean).flatMap(r => r.results || [])
log(`Research complete: ${flat.length} products processed. Verification is a SEPARATE step - run it only after a human reviews these results.`)

return { results: flat, fields_requested: fields }
