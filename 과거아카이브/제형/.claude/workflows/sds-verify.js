export const meta = {
  name: 'sds-verify',
  description: 'Mandatory two-pass verification for sds-research.js results. Pass 1 independently re-checks EVERY item (100%, no confidence-based skipping). Anomalies found in pass 1 are reported, then re-verified a second time (tie-break) before being finalized.',
  whenToUse: 'Run this ONLY after a human has reviewed sds-research.js output and explicitly approved moving to verification. Takes args.results = the research workflow\'s results array. Do not run automatically as part of research.',
  phases: [
    { title: 'Verify-1', detail: 'independent re-check of every single item (mandatory, 100% coverage)' },
    { title: 'Verify-2', detail: 'targeted tie-break re-verification of items flagged as anomalous in pass 1' },
  ],
}

const CIPAC_CURRENT = [
  {code:'AE', en:'Aerosol dispenser', ko:'에어로솔제', def:'A container-held formulation which is dispersed generally by a propellant as fine droplets or particles upon the actuation of a valve.'},
  {code:'AL', en:'Any other liquid', ko:'기타액제(미분류)', def:'A liquid not yet designated by a specific code, to be applied undiluted.'},
  {code:'AP', en:'Any other powder', ko:'기타분말제(미분류)', def:'A powder not yet designated by a specific code, to be applied undiluted.'},
  {code:'BR', en:'Briquette', ko:'블록제', def:'Solid block designed for controlled release of active ingredient into water.'},
  {code:'CB', en:'Bait concentrate', ko:'미끼농축제', def:'A solid or liquid intended for dilution before use as a bait.'},
  {code:'CP', en:'Contact powder', ko:'접촉분제', def:'Rodenticidal or insecticidal formulation in powder form for direct application. Formerly known as tracking powder (TP).'},
  {code:'CS', en:'Capsule suspension', ko:'캡슐현탁제', def:'A stable suspension of capsules in a fluid, normally intended for dilution with water before use.'},
  {code:'DC', en:'Dispersible concentrate', ko:'분산성농축제', def:'A liquid homogeneous formulation to be applied as a solid dispersion after dilution in water. (Some formulations are intermediate between DC and EC.)'},
  {code:'DP', en:'Dustable powder', ko:'분제', def:'A free-flowing powder suitable for dusting.'},
  {code:'DS', en:'Powder for dry seed treatment', ko:'종자처리분제', def:'A powder for application in the dry state directly to the seed.'},
  {code:'DT', en:'Tablet for direct application', ko:'직접적용정제', def:'Tablets applied individually and directly in the field/water bodies, without preparing a spraying solution or dispersion.'},
  {code:'EC', en:'Emulsifiable concentrate', ko:'유제', def:'A liquid, homogeneous formulation to be applied as an emulsion after dilution in water.'},
  {code:'EG', en:'Emulsifiable granule', ko:'유화성입제', def:'A granular formulation, which may contain water-insoluble formulants, applied as an oil-in-water emulsion after disintegration in water.'},
  {code:'EO', en:'Emulsion, water in oil', ko:'유중수형유제', def:'A fluid, heterogeneous formulation: a solution of pesticide in water dispersed as fine globules in a continuous organic liquid phase.'},
  {code:'EP', en:'Emulsifiable powder', ko:'유화성분제', def:'A powder formulation, which may contain water-insoluble formulants, applied as an oil-in-water emulsion after dispersion in water.'},
  {code:'ES', en:'Emulsion for seed treatment', ko:'종자처리유탁제', def:'A stable emulsion for application to the seed either directly or after dilution.'},
  {code:'EW', en:'Emulsion, oil in water', ko:'유탁제', def:'A fluid, heterogeneous formulation: a solution of pesticide in an organic liquid dispersed as fine globules in a continuous water phase.'},
  {code:'FS', en:'Flowable concentrate for seed treatment', ko:'종자처리액상수화제', def:'A stable SUSPENSION for application to the seed, either directly or after dilution. (Undissolved solids - contrast with LS.)'},
  {code:'FU', en:'Smoke generator', ko:'훈연제', def:'A combustible formulation, generally solid, which upon ignition releases the active ingredient(s) as smoke.'},
  {code:'GA', en:'Gas', ko:'가스제', def:'A gas packed in pressure bottle or pressure tank.'},
  {code:'GD', en:'Gel for direct application', ko:'직접적용겔제', def:'A gel-like preparation to be applied undiluted.'},
  {code:'GE', en:'Gas generating product', ko:'가스발생제', def:'A formulation which generates a gas by chemical reaction.'},
  {code:'GL', en:'Emulsifiable gel', ko:'유화성겔제', def:'A gelatinized formulation to be applied as an emulsion in water.'},
  {code:'GR', en:'Granule', ko:'입제', def:'A free-flowing solid formulation of a defined granule size range, ready for use.'},
  {code:'GS', en:'Grease', ko:'그리스제', def:'Very viscous formulation based on oil or fat.'},
  {code:'GW', en:'Water soluble gel', ko:'수용성겔제', def:'A gelatinized formulation to be applied as an aqueous solution.'},
  {code:'HN', en:'Hot fogging concentrate', ko:'열훈연농축제', def:'A formulation suitable for application by hot fogging equipment, either directly or after dilution.'},
  {code:'KK', en:'Combi-pack solid/liquid', ko:'고체/액체 콤비팩', def:'A solid and a liquid formulation, separately packed in one outer pack, for simultaneous tank-mix application. (Twin-pack special code.)'},
  {code:'KL', en:'Combi-pack liquid/liquid', ko:'액체/액체 콤비팩', def:'Two liquid formulations, separately packed in one outer pack, for simultaneous tank-mix application. (Twin-pack special code.)'},
  {code:'KN', en:'Cold fogging concentrate', ko:'저온훈연농축제', def:'A formulation suitable for application by cold fogging equipment, either directly or after dilution.'},
  {code:'LB', en:'Long-lasting storage bag', ko:'장기저장백', def:'A slow/controlled-release treated storage bag providing physical and chemical barriers to pests.'},
  {code:'LN', en:'Long-lasting insecticidal net', ko:'장기효력방충망', def:'A slow/controlled-release netting formulation providing physical/chemical barriers to insects (bulk netting or ready-to-use, e.g. mosquito nets).'},
  {code:'LS', en:'Solution for seed treatment', ko:'종자처리액제', def:'A clear to opalescent liquid applied to the seed, directly or as a solution after dilution. TRUE SOLUTION - contrast with FS (suspension).'},
  {code:'MC', en:'Mosquito coil', ko:'모기향', def:'A coil which smoulders without flame, releasing the active ingredient as vapour/smoke.'},
  {code:'ME', en:'Micro-emulsion', ko:'미탁제', def:'A clear to opalescent, oil-and-water-containing liquid, applied directly or after dilution in water.'},
  {code:'MR', en:'Matrix Release', ko:'매트릭스방출제', def:'A slow/controlled-release polymer-matrix formulation providing long-lasting effects, applied directly.'},
  {code:'OD', en:'Oil dispersion', ko:'유현탁제', def:'A stable suspension of active ingredient(s) in a water-immiscible fluid, which may contain other dissolved active(s), for dilution with water before use.'},
  {code:'OF', en:'Oil miscible flowable concentrate (oil miscible suspension)', ko:'유혼합성현탁제', def:'A stable suspension of active ingredient(s) in a fluid intended for dilution in an organic liquid before use.'},
  {code:'OL', en:'Oil miscible liquid', ko:'유혼합성액제', def:'A liquid, homogeneous formulation applied as a homogeneous liquid after dilution in an organic liquid.'},
  {code:'OP', en:'Oil dispersible powder', ko:'유분산성분제', def:'A powder formulation to be applied as a suspension after dispersion in an organic liquid.'},
  {code:'PA', en:'Paste', ko:'페이스트제', def:'Water-based, film-forming composition.'},
  {code:'PR', en:'Plant rodlet', ko:'식물삽입봉', def:'A small rodlet (a few cm long, a few mm diameter) containing an active ingredient.'},
  {code:'RB', en:'Bait (ready for use)', ko:'미끼제', def:'A formulation designed to attract and be eaten by the target pests. (Covers solid, liquid, or gel bait sub-forms - no separate CIPAC code for \'gel bait\'.)'},
  {code:'SC', en:'Suspension concentrate (= flowable concentrate)', ko:'액상수화제', def:'A stable suspension of active ingredient(s) with water as the fluid, for dilution with water before use. NOTE: \'flowable concentrate\' (non-seed-treatment) = SC, not to be confused with FS.'},
  {code:'SD', en:'Suspension concentrate for direct application', ko:'직접적용현탁제', def:'A stable suspension for direct application (e.g. to rice paddies), may contain other dissolved active(s).'},
  {code:'SE', en:'Suspo-emulsion', ko:'현탁화유제', def:'A fluid, heterogeneous formulation: stable dispersion of solid particles AND water-immiscible fine globules in a continuous water phase.'},
  {code:'SG', en:'Water soluble granule', ko:'수용성입제', def:'Granules applied as a true solution after dissolution in water; may contain insoluble inert ingredients.'},
  {code:'SL', en:'Soluble concentrate', ko:'액제', def:'A clear to opalescent liquid applied as a solution of the active ingredient after dilution in water. TRUE SOLUTION intended for dilution (contrast AL = undiluted; TD = trigger-spray RTU).'},
  {code:'SO', en:'Spreading oil', ko:'전개유제', def:'Formulation designed to form a surface layer on application to water.'},
  {code:'SP', en:'Water soluble powder', ko:'수용제', def:'A powder formulation applied as a true solution after dissolution in water; may contain insoluble inert ingredients.'},
  {code:'ST', en:'Water soluble tablet', ko:'수용성정제', def:'Tablets forming a solution of the active ingredient after disintegration in water; may contain water-insoluble formulants.'},
  {code:'SU', en:'Ultra-low volume (ULV) suspension', ko:'초미량현탁제', def:'A suspension ready for use through ULV equipment.'},
  {code:'TB', en:'Tablet', ko:'정제', def:'Pre-formed solids of uniform shape/dimensions, usually circular with flat or convex faces.'},
  {code:'TC', en:'Technical material', ko:'원제', def:'A material resulting from a manufacturing process comprising the active ingredient plus associated impurities; may contain small amounts of necessary additives. (No diluent.)'},
  {code:'TD', en:'Trigger Dispenser', ko:'트리거분무제', def:'A container-held, propellant-free liquid formulation, ready to use, dispersed as droplets by actuation of a trigger.'},
  {code:'TK', en:'Technical concentrate', ko:'원제농축물', def:'A material resulting from a manufacturing process comprising the active ingredient plus associated impurities; may contain small amounts of necessary additives AND appropriate diluents.'},
  {code:'UL', en:'Ultra-low volume (ULV) liquid', ko:'초미량액제', def:'A homogeneous liquid ready for use through ULV equipment.'},
  {code:'VP', en:'Vapour releasing product', ko:'증기발산제', def:'A formulation containing volatile active ingredient(s) whose vapours are released into the air, evaporation rate controlled by formulation/dispenser.'},
  {code:'WG', en:'Water dispersible granules', ko:'입상수화제', def:'Granules applied after disintegration and dispersion in water.'},
  {code:'WP', en:'Wettable powder', ko:'수화제', def:'A powder formulation applied as a suspension after dispersion in water.'},
  {code:'WS', en:'Water dispersible powder for slurry seed treatment', ko:'종자처리수화제', def:'A powder dispersed at high concentration in water before application as a slurry to the seed.'},
  {code:'WT', en:'Water dispersible tablet', ko:'수분산성정제', def:'Tablets forming a dispersion of the active ingredient after disintegration in water.'},
  {code:'XX', en:'Others', ko:'기타(임시분류)', def:'Temporary categorization of all other formulations not listed above.'},
  {code:'ZC', en:'A mixed formulation of CS and SC', ko:'CS+SC 혼합제', def:'A stable suspension of capsules and active ingredient(s) in fluid, for dilution with water before use.'},
  {code:'ZE', en:'A mixed formulation of CS and SE', ko:'CS+SE 혼합제', def:'Stable dispersion of capsules, solid particles, and fine globules in a continuous water phase, for dilution with water before use.'},
  {code:'ZW', en:'A mixed formulation of CS and EW', ko:'CS+EW 혼합제', def:'Stable dispersion of capsules and fine globules in a continuous water phase, for dilution with water before use.'},
]
const CIPAC_DISCONTINUED = [
  {code:'AB', en:'Grain bait', ko:'곡물미끼제', def:'Special form of bait.'},
  {code:'BB', en:'Block bait', ko:'블록미끼제', def:'Special form of bait.'},
  {code:'CF', en:'Capsule suspension for seed treatment', ko:'종자처리캡슐현탁제', def:'A stable suspension of capsules in a fluid, applied to seed directly or after dilution.'},
  {code:'CG', en:'Encapsulated granule', ko:'캡슐입제', def:'A granule with a protective/release-controlling coating.'},
  {code:'CL', en:'Contact liquid or gel', ko:'접촉액상/겔제', def:'Rodenticidal/insecticidal liquid or gel for direct application (or after dilution for gels).'},
  {code:'ED', en:'Electrochargeable liquid', ko:'정전분무액제', def:'Special liquid formulation for electrostatic (electrodynamic) spraying.'},
  {code:'FD', en:'Smoke tin', ko:'훈연통', def:'Special form of smoke generator.'},
  {code:'FG', en:'Fine granule', ko:'세립제', def:'A granule in the particle size range 300-2500 μm.'},
  {code:'FK', en:'Smoke candle', ko:'훈연캔들', def:'Special form of smoke generator.'},
  {code:'FP', en:'Smoke cartridge', ko:'훈연카트리지', def:'Special form of smoke generator.'},
  {code:'FR', en:'Smoke rodlet', ko:'훈연봉', def:'Special form of smoke generator.'},
  {code:'FT', en:'Smoke tablet', ko:'훈연정제', def:'Special form of smoke generator.'},
  {code:'FW', en:'Smoke pellet', ko:'훈연펠릿', def:'Special form of smoke generator.'},
  {code:'GB', en:'Granular bait', ko:'입상미끼제', def:'Special form of bait.'},
  {code:'GF', en:'Gel for Seed Treatment', ko:'종자처리겔제', def:'A homogeneous gelatinous formulation applied directly to the seed.'},
  {code:'GG', en:'Macrogranule', ko:'대형입제', def:'A granule in the particle size range 2000-6000 μm.'},
  {code:'GP', en:'Flo-dust', ko:'플로더스트', def:'Very fine dustable powder for pneumatic application in greenhouses.'},
  {code:'KP', en:'Combi-pack solid/solid', ko:'고체/고체 콤비팩', def:'Two solid formulations, separately packed in one outer pack, for simultaneous tank-mix application.'},
  {code:'LA', en:'Lacquer', ko:'락커제', def:'Solvent-based, film-forming composition.'},
  {code:'LV', en:'Liquid vaporizer', ko:'액체증발기제', def:'A liquid formulation in a cartridge/bottle fitted to a heater unit; passes up a heated wick and evaporates.'},
  {code:'MG', en:'Microgranule', ko:'미립제', def:'A granule in the particle size range 100-600 μm.'},
  {code:'MV', en:'Vaporizing mats', ko:'가열증발매트제', def:'A pulp/inert mat impregnated with active ingredient, used in a heater unit for slow volatilisation.'},
  {code:'PB', en:'Plate bait', ko:'판형미끼제', def:'Special form of bait.'},
  {code:'PC', en:'Gel or paste concentrate', ko:'겔/페이스트농축제', def:'A solid formulation applied as a gel or paste after dilution with water.'},
  {code:'PO', en:'Pour-on', ko:'포어온제', def:'Solution for pouring on the skin of animals in a high volume (normally more than 100 ml per animal).'},
  {code:'PS', en:'Seed coated with a pesticide', ko:'농약처리종자', def:'Application form. NOT considered a formulation type.'},
  {code:'SA', en:'Spot-on', ko:'스팟온제', def:'Solution for spot application on the skin of animals in a low volume (normally less than 100 ml per animal).'},
  {code:'SB', en:'Scrap bait', ko:'조각미끼제', def:'Special form of bait.'},
  {code:'SS', en:'Water soluble powder for seed treatment', ko:'종자처리수용제', def:'A powder to be dissolved in water before application to the seed.'},
  {code:'TP', en:'Tracking powder', ko:'추적분제', def:'Discontinued term. Refer to CP.'},
]

function codeTable(list) {
  return list.map(c => `- ${c.code}: ${c.en} — ${c.def}`).join('\n')
}

let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch (e) { resolvedArgs = null }
}

const items = (resolvedArgs && resolvedArgs.results) || []
if (!items.length) {
  throw new Error("Pass args.results: the results array returned by sds-research.js. Got: " + JSON.stringify(args).slice(0, 200))
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    agrees: { type: 'boolean', description: 'true if your independent research confirms the claimed result (allow minor wording differences - only false for a substantive disagreement)' },
    anomaly: { type: 'boolean', description: 'true if you found something worth flagging even if you are not 100% sure it is wrong (e.g. source looks mismatched, code seems inconsistent with the SC-vs-SL/FS-vs-LS/PO-vs-SA distinctions, sds_summary seems overstated)' },
    corrected_field_values: { type: ['object', 'null'], description: 'If agrees=false, your own best-effort corrected field_values in the same shape as the original; else null' },
    corrected_resolution: { type: ['string', 'null'] },
    corrected_not_formulation_reason: { type: ['string', 'null'] },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    notes: { type: 'string' },
  },
  required: ['agrees', 'anomaly', 'confidence', 'notes'],
}

function verify1Prompt(item) {
  const formulationContext = (item.field_values && item.field_values.formulation_code) ? `
## CIPAC formulation code reference (for checking formulation_code claims)
${codeTable(CIPAC_CURRENT)}
${codeTable(CIPAC_DISCONTINUED)}
Key distinctions: SC (suspension, dilute before use) vs SL (true solution); FS (seed-treatment suspension) vs LS (seed-treatment solution); PO (pour-on, >100mL) vs SA (spot-on, <100mL); TC (no diluent) vs TK (has diluent) - both are legitimate formulation codes, not "not_formulation".
` : ''

  return `You are independently auditing an SDS-research claim about a pesticide/biocide/chemical product. Be skeptical - your job is to catch a wrong classification or an overstated/understated SDS summary, not rubber-stamp it. Do your own fresh web search (SDS/EPA label/manufacturer site) for this product - do not simply trust the cited source_url without checking it actually says what is claimed.
${formulationContext}
## Product being audited
Name: ${item.product_name}
Claimed resolution: ${item.resolution}
Claimed field values: ${JSON.stringify(item.field_values || {})}
Claimed not_formulation_reason: ${item.not_formulation_reason || 'null'}
Claimed SDS completeness summary: ${JSON.stringify(item.sds_summary || {})}
Cited source: ${item.source_url || 'none'}
Original researcher's notes: ${item.notes}

Re-derive the field value(s) and the SDS completeness summary from scratch. Report whether you agree, and flag any anomaly (even a minor one worth a second look) - including if the cited source does not actually contain what is claimed, if a suspension-vs-solution or pour-on-vs-spot-on type distinction looks wrong, or if the completeness summary (성분/독성/제형/물리화학적 정보 유무) looks inflated or understated relative to what the source actually contains.`
}

function verify2Prompt(item, v1) {
  return `This is a tie-break re-check. An earlier independent audit flagged a possible issue with this product's classification/data. Do a third, careful look and give a final decision.

Product: ${item.product_name}
Original claim: ${JSON.stringify({ resolution: item.resolution, field_values: item.field_values, not_formulation_reason: item.not_formulation_reason, source_url: item.source_url })}
First audit's finding: agrees=${v1.agrees}, anomaly=${v1.anomaly}, notes: ${v1.notes}

Search independently and settle it. If the original was actually right, say so (agrees=true, anomaly=false) - the first audit may have been overly cautious. If it was wrong, give your best-effort corrected_field_values / corrected_resolution.`
}

phase('Verify-1')
log(`Pass 1: independently re-checking all ${items.length} items (mandatory, 100% coverage)`)
const pass1 = await parallel(items.map(item => () =>
  agent(verify1Prompt(item), { label: `verify1-row-${item.row}`, phase: 'Verify-1', schema: VERIFY_SCHEMA })
    .then(v => ({ item, v }))
))

const pass1Clean = pass1.filter(Boolean)
const flagged = pass1Clean.filter(({ v }) => !v.agrees || v.anomaly)
log(`Pass 1 complete. ${flagged.length} of ${pass1Clean.length} items flagged for tie-break re-verification.`)

phase('Verify-2')
const pass2 = await parallel(flagged.map(({ item, v }) => () =>
  agent(verify2Prompt(item, v), { label: `verify2-row-${item.row}`, phase: 'Verify-2', schema: VERIFY_SCHEMA })
    .then(v2 => ({ item, v1: v, v2 }))
))

const pass2ByRow = new Map(pass2.filter(Boolean).map(({ item, v1, v2 }) => [item.row, { v1, v2 }]))

const finalResults = items.map(item => {
  const p2 = pass2ByRow.get(item.row)
  if (!p2) return { ...item, verify_status: 'confirmed_pass1' }
  if (p2.v2.agrees && !p2.v2.anomaly) {
    return { ...item, verify_status: 'confirmed_pass2_after_flag' }
  }
  return {
    ...item,
    field_values: p2.v2.corrected_field_values || item.field_values,
    resolution: p2.v2.corrected_resolution || item.resolution,
    not_formulation_reason: p2.v2.corrected_not_formulation_reason !== undefined ? p2.v2.corrected_not_formulation_reason : item.not_formulation_reason,
    verify_status: 'corrected',
    verify_notes: p2.v2.notes,
  }
})

const anomalyReport = pass2.filter(Boolean).map(({ item, v1, v2 }) => ({
  row: item.row,
  product_name: item.product_name,
  pass1_notes: v1.notes,
  pass2_verdict: v2.agrees && !v2.anomaly ? 'confirmed_original_was_right' : 'corrected',
  pass2_notes: v2.notes,
}))

log(`Done. ${finalResults.filter(r => r.verify_status === 'corrected').length} corrected, ${finalResults.filter(r => r.verify_status === 'confirmed_pass2_after_flag').length} confirmed-after-flag, ${finalResults.filter(r => r.verify_status === 'confirmed_pass1').length} confirmed on pass 1.`)

return { final_results: finalResults, anomaly_report: anomalyReport }
