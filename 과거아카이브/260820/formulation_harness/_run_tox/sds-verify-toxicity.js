export const meta = {
  name: 'sds-verify-toxicity-final',
  description: 'Lean independent re-check of toxicity research claims (found items only) for dataset_배정.xlsx 독성코드 sheet. Args carry only row numbers + a source file path - each verify agent looks up its own item by row, keeping the orchestrator payload tiny.',
  whenToUse: 'Run after sds-research-toxicity.js on the found items to independently verify GHS signal word/H-codes and the ingredient-match claim.',
  phases: [
    { title: 'Verify-1', detail: 'independent re-check of every row (100% coverage)' },
    { title: 'Verify-2', detail: 'tie-break re-check of rows flagged in pass 1' },
  ],
}

let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch (e) { resolvedArgs = null }
}

const rows = (resolvedArgs && resolvedArgs.rows) || []
const sourceFile = (resolvedArgs && resolvedArgs.sourceFile) || '/Users/hanseoyun/Desktop/제형/formulation_harness/_run_tox/to_verify.json'
if (!rows.length) {
  throw new Error("Pass args.rows: array of row numbers to verify, and optionally args.sourceFile. Got: " + JSON.stringify(args).slice(0, 200))
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    row: { type: 'integer' },
    product_name: { type: 'string' },
    agrees: { type: 'boolean', description: 'true if your independent research confirms the claimed toxicity values AND the ingredient-match claim (allow minor wording differences, only false for a substantive disagreement)' },
    ingredients_actually_match: { type: 'boolean', description: 'your own independent judgment: does the cited source really describe this product\'s actual ingredients, not a different substance?' },
    anomaly: { type: 'boolean', description: 'true if anything is worth flagging even if not fully sure it is wrong (wrong signal word, H-codes not matching the source, source looks unrelated, etc.)' },
    corrected_field_values: { type: ['object', 'null'], description: 'If agrees=false, your best-effort corrected toxicity field_values in the same shape as the original (ghs_signal_word, ghs_hazard_statements, etc); else null' },
    corrected_resolution: { type: ['string', 'null'], description: 'One of found/no_code_in_sds/ingredient_mismatch/unresolved if you disagree with the original resolution; else null' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    notes: { type: 'string', description: 'CONCISE Korean note, ANOMALIES ONLY. Empty string "" if nothing unusual.' },
  },
  required: ['row', 'product_name', 'agrees', 'ingredients_actually_match', 'anomaly', 'confidence', 'notes'],
}

function lookupInstruction(row) {
  return `First, look up the item for row ${row} yourself by running this Bash command (do not skip this step, do not guess the data, do not read the whole file into your context - just this one extracted item):
python3 -c "import json; d=json.load(open('${sourceFile}')); print(json.dumps([x for x in d if x['row']==${row}][0], ensure_ascii=False))"
This prints ONE JSON object with: product_name, ingredient_names (ground truth ingredients for this product), resolution, field_values.toxicity (claimed GHS signal word/H-codes/toxicity data), sds_summary, source_url, notes.`
}

function verify1Prompt(row) {
  return `You are independently auditing an SDS toxicity-research claim about a chemical/pesticide product (row ${row}). Be skeptical - your job is to catch a wrong GHS signal word/H-code or a false ingredient-match claim, not rubber-stamp it.

${lookupInstruction(row)}

Then do your own fresh check of the cited source (and search further if needed) - do not just trust that the source_url actually says what is claimed. Re-derive the GHS signal word / H-codes / toxicity classification from scratch, and independently judge whether the source's ingredient list truly matches the product's ingredient_names. Include "row": ${row} and the exact "product_name" you looked up in your structured output. Report whether you agree, and flag any anomaly.`
}

function verify2Prompt(row, v1) {
  return `Tie-break re-check for row ${row}. An earlier independent audit flagged a possible issue with this product's toxicity data or ingredient-match claim.

${lookupInstruction(row)}

First audit's finding: agrees=${v1.agrees}, ingredients_actually_match=${v1.ingredients_actually_match}, anomaly=${v1.anomaly}, notes: ${v1.notes}

Search independently and settle it. Include "row": ${row} and "product_name" in your output. If the original was actually right, say so (agrees=true, anomaly=false, ingredients_actually_match=true) - the first audit may have been overly cautious. If it was wrong, give your best-effort corrected_field_values / corrected_resolution.`
}

phase('Verify-1')
log(`Pass 1: independently re-checking all ${rows.length} rows (100% coverage)`)
const pass1 = await parallel(rows.map(row => () =>
  agent(verify1Prompt(row), { label: `verify1-row-${row}`, phase: 'Verify-1', schema: VERIFY_SCHEMA })
))

const pass1Clean = pass1.filter(Boolean)
const flagged = pass1Clean.filter(v => !v.agrees || v.anomaly || !v.ingredients_actually_match)
log(`Pass 1 complete. ${flagged.length} of ${pass1Clean.length} rows flagged for tie-break re-verification.`)

phase('Verify-2')
const pass2 = await parallel(flagged.map(v => () =>
  agent(verify2Prompt(v.row, v), { label: `verify2-row-${v.row}`, phase: 'Verify-2', schema: VERIFY_SCHEMA })
    .then(v2 => ({ row: v.row, v1: v, v2 }))
))

const pass2ByRow = new Map(pass2.filter(Boolean).map(({ row, v1, v2 }) => [row, { v1, v2 }]))

const finalResults = pass1Clean.map(v => {
  const p2 = pass2ByRow.get(v.row)
  if (!p2) {
    return { row: v.row, product_name: v.product_name, verify_status: 'confirmed_pass1', verify_notes: v.notes, corrected_field_values: null, corrected_resolution: null, ingredients_actually_match: v.ingredients_actually_match }
  }
  const agreesFinal = p2.v2.agrees && !p2.v2.anomaly && p2.v2.ingredients_actually_match
  if (agreesFinal) {
    return { row: v.row, product_name: p2.v2.product_name || v.product_name, verify_status: 'confirmed_pass2_after_flag', verify_notes: p2.v2.notes, corrected_field_values: null, corrected_resolution: null, ingredients_actually_match: true }
  }
  const mismatch = p2.v2.ingredients_actually_match === false
  return {
    row: v.row,
    product_name: p2.v2.product_name || v.product_name,
    verify_status: 'corrected',
    verify_notes: p2.v2.notes,
    corrected_field_values: mismatch ? null : p2.v2.corrected_field_values,
    corrected_resolution: p2.v2.corrected_resolution || (mismatch ? 'ingredient_mismatch' : null),
    ingredients_actually_match: p2.v2.ingredients_actually_match,
  }
})

const anomalyReport = pass2.filter(Boolean).map(({ row, v1, v2 }) => ({
  row,
  product_name: v2.product_name || v1.product_name,
  pass1_notes: v1.notes,
  pass2_verdict: (v2.agrees && !v2.anomaly && v2.ingredients_actually_match) ? 'confirmed_original_was_right' : 'corrected',
  pass2_notes: v2.notes,
}))

log(`Done. ${finalResults.filter(r => r.verify_status === 'corrected').length} corrected, ${finalResults.filter(r => r.verify_status === 'confirmed_pass2_after_flag').length} confirmed-after-flag, ${finalResults.filter(r => r.verify_status === 'confirmed_pass1').length} confirmed on pass 1.`)

return { final_results: finalResults, anomaly_report: anomalyReport }
