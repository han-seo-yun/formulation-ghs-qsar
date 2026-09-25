export const meta = {
  name: 'sds-verify-toxicity-c1',
  description: 'Independent re-check of toxicity research claims (found items only) for dataset_배정.xlsx 독성코드 sheet chunk 1, ingredient-match focused.',
  whenToUse: 'Run after sds-research-toxicity.js on the found items to independently verify GHS signal word/H-codes and the ingredient-match claim.',
  phases: [
    { title: 'Verify-1', detail: 'independent re-check of every item (100% coverage)' },
    { title: 'Verify-2', detail: 'tie-break re-check of items flagged in pass 1' },
  ],
}

let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch (e) { resolvedArgs = null }
}

const items = (resolvedArgs && resolvedArgs.results) || []
if (!items.length) {
  throw new Error("Pass args.results: array of found-toxicity items. Got: " + JSON.stringify(args).slice(0, 200))
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    agrees: { type: 'boolean', description: 'true if your independent research confirms the claimed toxicity values AND the ingredient-match claim (allow minor wording differences, only false for a substantive disagreement)' },
    ingredients_actually_match: { type: 'boolean', description: "your own independent judgment: does the cited source really describe this product's actual ingredients (ingredient_names), not a different substance?" },
    anomaly: { type: 'boolean', description: 'true if anything is worth flagging even if not fully sure it is wrong (wrong signal word, H-codes not matching the source, source looks unrelated, etc.)' },
    corrected_field_values: { type: ['object', 'null'], description: 'If agrees=false, your best-effort corrected toxicity field_values in the same shape as the original; else null' },
    corrected_resolution: { type: ['string', 'null'], description: 'One of found/no_code_in_sds/ingredient_mismatch/unresolved if you disagree with the original resolution; else null' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    notes: { type: 'string', description: 'CONCISE Korean note, ANOMALIES ONLY. Empty string "" if nothing unusual.' },
  },
  required: ['agrees', 'ingredients_actually_match', 'anomaly', 'confidence', 'notes'],
}

function verify1Prompt(item) {
  return `You are independently auditing an SDS toxicity-research claim about a chemical/pesticide product. Be skeptical - your job is to catch a wrong GHS signal word/H-code or a false ingredient-match claim, not rubber-stamp it. Do your own fresh check of the cited source (and search further if needed) - do not just trust that the source_url actually says what is claimed.

## Product being audited
Name: ${item.product_name}
Ingredient names (ground truth for this product): ${item.ingredient_names || 'not given'}
Claimed resolution: ${item.resolution}
Claimed toxicity field values: ${JSON.stringify((item.field_values || {}).toxicity || {})}
Claimed sds_summary: ${JSON.stringify(item.sds_summary || {})}
Cited source: ${item.source_url || 'the original hint URL (see product data)'}
Original researcher's notes: ${item.notes || '(none)'}

Re-derive the GHS signal word / H-codes / toxicity classification from scratch, and independently judge whether the source's ingredient list truly matches "${item.ingredient_names}". Report whether you agree, and flag any anomaly.`
}

function verify2Prompt(item, v1) {
  return `Tie-break re-check. An earlier independent audit flagged a possible issue with this product's toxicity data or ingredient-match claim. Do a third, careful look and give a final decision.

Product: ${item.product_name}
Ingredient names (ground truth): ${item.ingredient_names || 'not given'}
Original claim: ${JSON.stringify({ resolution: item.resolution, field_values: (item.field_values||{}).toxicity, source_url: item.source_url })}
First audit's finding: agrees=${v1.agrees}, ingredients_actually_match=${v1.ingredients_actually_match}, anomaly=${v1.anomaly}, notes: ${v1.notes}

Search independently and settle it. If the original was actually right, say so (agrees=true, anomaly=false, ingredients_actually_match=true) - the first audit may have been overly cautious. If it was wrong, give your best-effort corrected_field_values / corrected_resolution.`
}

phase('Verify-1')
log(`Pass 1: independently re-checking all ${items.length} items (100% coverage)`)
const pass1 = await parallel(items.map(item => () =>
  agent(verify1Prompt(item), { label: `verify1-row-${item.row}`, phase: 'Verify-1', schema: VERIFY_SCHEMA })
    .then(v => ({ item, v }))
))

const pass1Clean = pass1.filter(Boolean)
const flagged = pass1Clean.filter(({ v }) => !v.agrees || v.anomaly || !v.ingredients_actually_match)
log(`Pass 1 complete. ${flagged.length} of ${pass1Clean.length} items flagged for tie-break re-verification.`)

phase('Verify-2')
const pass2 = await parallel(flagged.map(({ item, v }) => () =>
  agent(verify2Prompt(item, v), { label: `verify2-row-${item.row}`, phase: 'Verify-2', schema: VERIFY_SCHEMA })
    .then(v2 => ({ item, v1: v, v2 }))
))

const pass2ByRow = new Map(pass2.filter(Boolean).map(({ item, v1, v2 }) => [item.row, { v1, v2 }]))

const finalResults = pass1Clean.map(({ item, v }) => {
  const p2 = pass2ByRow.get(item.row)
  if (!p2) return { ...item, verify_status: 'confirmed_pass1', verify_notes: v.notes }
  const agreesFinal = p2.v2.agrees && !p2.v2.anomaly && p2.v2.ingredients_actually_match
  if (agreesFinal) {
    return { ...item, verify_status: 'confirmed_pass2_after_flag', verify_notes: p2.v2.notes }
  }
  const newFieldValues = p2.v2.corrected_field_values
    ? { ...item.field_values, toxicity: p2.v2.corrected_field_values }
    : item.field_values
  return {
    ...item,
    field_values: p2.v2.ingredients_actually_match === false ? {} : newFieldValues,
    resolution: p2.v2.corrected_resolution || (p2.v2.ingredients_actually_match === false ? 'ingredient_mismatch' : item.resolution),
    verify_status: 'corrected',
    verify_notes: p2.v2.notes,
  }
})

const anomalyReport = pass2.filter(Boolean).map(({ item, v1, v2 }) => ({
  row: item.row,
  product_name: item.product_name,
  pass1_notes: v1.notes,
  pass2_verdict: (v2.agrees && !v2.anomaly && v2.ingredients_actually_match) ? 'confirmed_original_was_right' : 'corrected',
  pass2_notes: v2.notes,
}))

log(`Done. ${finalResults.filter(r => r.verify_status === 'corrected').length} corrected, ${finalResults.filter(r => r.verify_status === 'confirmed_pass2_after_flag').length} confirmed-after-flag, ${finalResults.filter(r => r.verify_status === 'confirmed_pass1').length} confirmed on pass 1.`)

return { final_results: finalResults, anomaly_report: anomalyReport }
