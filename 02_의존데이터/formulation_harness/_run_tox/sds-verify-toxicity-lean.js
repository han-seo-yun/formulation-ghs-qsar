export const meta = {
  name: 'sds-verify-toxicity-lean-chunk4',
  description: 'Lean variant: each verify agent looks up its own item from a shared file by row number instead of the orchestrator embedding all items in args.',
  phases: [
    { title: 'Verify-1', detail: 'independent re-check of every item (100% coverage)' },
    { title: 'Verify-2', detail: 'tie-break re-check of items flagged in pass 1' },
  ],
}

let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch (e) { resolvedArgs = null }
}

const rows = (resolvedArgs && resolvedArgs.rows) || []
const dataFile = resolvedArgs && resolvedArgs.dataFile
if (!rows.length || !dataFile) {
  throw new Error("Pass args.rows (array of row numbers) and args.dataFile (path). Got: " + JSON.stringify(args).slice(0, 200))
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    row: { type: 'integer' },
    product_name: { type: 'string' },
    agrees: { type: 'boolean', description: 'true if your independent research confirms the claimed toxicity values AND the ingredient-match claim' },
    ingredients_actually_match: { type: 'boolean' },
    anomaly: { type: 'boolean' },
    corrected_field_values: { type: ['object', 'null'] },
    corrected_resolution: { type: ['string', 'null'] },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    notes: { type: 'string', description: 'CONCISE Korean note, ANOMALIES ONLY. Empty string "" if nothing unusual.' },
  },
  required: ['row', 'product_name', 'agrees', 'ingredients_actually_match', 'anomaly', 'confidence', 'notes'],
}

function lookupInstruction(row) {
  return `First, run this Bash command to fetch the ONE item you're auditing (do not read the whole file into your context, just this one extracted item):
python3 -c "import json; d=json.load(open('${dataFile}')); item=[x for x in d if x['row']==${row}][0]; print(json.dumps(item, ensure_ascii=False))"
`
}

function verify1Prompt(row) {
  return `You are independently auditing an SDS toxicity-research claim about a chemical/pesticide product (row ${row} of a research batch). Be skeptical - catch a wrong GHS signal word/H-code or a false ingredient-match claim, not rubber-stamp it.

${lookupInstruction(row)}
The item has: product_name, ingredient_names (ground truth), resolution, field_values.toxicity (claimed toxicity data), sds_summary, source_url, notes.

Do your own fresh check of the cited source (search further if needed) - do not just trust that source_url says what is claimed. Re-derive the GHS signal word / H-codes / toxicity classification from scratch, and independently judge whether the source's ingredients truly match ingredient_names. Include "row": ${row} and the exact "product_name" you looked up in your structured output. Report whether you agree, and flag any anomaly.`
}

function verify2Prompt(row, v1) {
  return `Tie-break re-check for row ${row}. An earlier independent audit flagged a possible issue.

${lookupInstruction(row)}
First audit's finding: agrees=${v1.agrees}, ingredients_actually_match=${v1.ingredients_actually_match}, anomaly=${v1.anomaly}, notes: ${v1.notes}

Search independently and settle it. Include "row": ${row} and "product_name" in your output. If the original was actually right, say so (agrees=true, anomaly=false, ingredients_actually_match=true). If it was wrong, give your best-effort corrected_field_values / corrected_resolution.`
}

phase('Verify-1')
log(`Pass 1: independently re-checking all ${rows.length} items (100% coverage), lean lookup mode`)
const pass1 = await parallel(rows.map(row => () =>
  agent(verify1Prompt(row), { label: `verify1-row-${row}`, phase: 'Verify-1', schema: VERIFY_SCHEMA })
    .then(v => ({ row, v }))
))

const pass1Clean = pass1.filter(Boolean)
const flagged = pass1Clean.filter(({ v }) => !v.agrees || v.anomaly || !v.ingredients_actually_match)
log(`Pass 1 complete. ${flagged.length} of ${pass1Clean.length} items flagged for tie-break re-verification.`)

phase('Verify-2')
const pass2 = await parallel(flagged.map(({ row, v }) => () =>
  agent(verify2Prompt(row, v), { label: `verify2-row-${row}`, phase: 'Verify-2', schema: VERIFY_SCHEMA })
    .then(v2 => ({ row, v1: v, v2 }))
))

const pass2ByRow = new Map(pass2.filter(Boolean).map(({ row, v1, v2 }) => [row, { v1, v2 }]))

const finalResults = pass1Clean.map(({ row, v }) => {
  const p2 = pass2ByRow.get(row)
  if (!p2) {
    return { row, product_name: v.product_name, verify_status: 'confirmed_pass1', verify_notes: v.notes, agrees: v.agrees, ingredients_actually_match: v.ingredients_actually_match, corrected_field_values: null, corrected_resolution: null }
  }
  const agreesFinal = p2.v2.agrees && !p2.v2.anomaly && p2.v2.ingredients_actually_match
  if (agreesFinal) {
    return { row, product_name: p2.v2.product_name || v.product_name, verify_status: 'confirmed_pass2_after_flag', verify_notes: p2.v2.notes, agrees: true, ingredients_actually_match: true, corrected_field_values: null, corrected_resolution: null }
  }
  const mismatch = p2.v2.ingredients_actually_match === false
  return {
    row,
    product_name: p2.v2.product_name || v.product_name,
    verify_status: 'corrected',
    verify_notes: p2.v2.notes,
    agrees: false,
    ingredients_actually_match: p2.v2.ingredients_actually_match,
    corrected_field_values: mismatch ? null : p2.v2.corrected_field_values,
    corrected_resolution: p2.v2.corrected_resolution || (mismatch ? 'ingredient_mismatch' : null),
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
