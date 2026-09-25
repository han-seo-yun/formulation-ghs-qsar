export const meta = {
  name: 'formulation-research',
  description: 'Research pesticide/biocide formulation types (CIPAC/CropLife codes) from SDS/labels and classify not-a-formulation cases',
  whenToUse: "Given args.products = an array of {row, product_name, ingredient_names, ...hints} objects (see formulation_harness/extract_targets.py's unique_products.json), researches each product's SDS/EPA label/manufacturer page to assign a CIPAC formulation code, or a not_formulation_reason if it is not an end-use pesticide formulation at all.",
  phases: [
    { title: 'Research', detail: 'batched parallel SDS/label lookups per product' },
    { title: 'Verify', detail: 'independent re-check of low-confidence results' },
  ],
}

// Source of truth: CIPAC Handbook Appendix D (current, valid for new registrations) +
// CropLife International Technical Monograph N2 8th ed. 2022 (adds 30 discontinued/legacy
// codes, still valid on pre-existing labels only). Keep in sync with ../formulation_harness/cipac_codes.json.
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

let resolvedArgs = args
if (typeof resolvedArgs === 'string') {
  try { resolvedArgs = JSON.parse(resolvedArgs) } catch (e) { resolvedArgs = null }
}

const BATCH_SIZE = (resolvedArgs && resolvedArgs.batchSize) || 8
const products = (resolvedArgs && resolvedArgs.products) || []
if (!products.length) {
  throw new Error("Pass args.products: an array of {row, product_name, ingredient_names, ...} objects (see extract_targets.py's unique_products.json). Got: " + JSON.stringify(args).slice(0, 200))
}

function codeTable(list) {
  return list.map(c => `- ${c.code}: ${c.en} \u2014 ${c.def}`).join('\n')
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
          resolution: { type: 'string', enum: ['formulation', 'not_formulation', 'unresolved'] },
          formulation_code: { type: ['string', 'null'] },
          formulation_type_en: { type: ['string', 'null'] },
          not_formulation_reason: { type: ['string', 'null'] },
          source_url: { type: ['string', 'null'] },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
          source_mismatch_found: { type: 'boolean' },
          notes: { type: 'string' },
        },
        required: ['row', 'product_name', 'resolution', 'confidence', 'notes'],
      },
    },
  },
  required: ['results'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    revised: { type: 'boolean' },
    still_formulation_code: { type: ['string', 'null'] },
    still_not_formulation_reason: { type: ['string', 'null'] },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    notes: { type: 'string' },
  },
  required: ['revised', 'confidence', 'notes'],
}

function researchPrompt(batch) {
  return `You are researching the exact CIPAC/CropLife physical formulation type of pesticide/biocide products for a Korean pesticide-toxicology database, OR determining that a product is not an end-use pesticide formulation at all.

## Official reference: CIPAC current formulation codes (66, valid for NEW product registration)
${codeTable(CIPAC_CURRENT)}

## Official reference: CropLife discontinued/legacy codes (30 - valid ONLY on pre-existing labels, do not assign to genuinely new/unregistered products, but DO use them if a product's OWN existing label/SDS already uses one, e.g. veterinary Pour-on/Spot-on, or historical bait sub-types)
${codeTable(CIPAC_DISCONTINUED)}

## Key distinctions researchers have gotten wrong before - check carefully
- SC ("Suspension concentrate", = "flowable concentrate") vs SL ("Soluble concentrate"): SC is diluted-before-use with UNDISSOLVED solids (opaque/suspension, "shake well/agitate" on the label); SL is a TRUE solution (clear/opalescent, dissolves fully). Do not call a suspension "SL" just because it is marketed as "liquid concentrate".
- FS ("Flowable concentrate for seed treatment", suspension) vs LS ("Solution for seed treatment", true solution): same suspension-vs-solution distinction, applied to seed-treatment liquids.
- SL (diluted before use) vs AL ("Any other liquid...to be applied undiluted") vs TD ("Trigger Dispenser", a ready-to-use trigger-spray bottle, propellant-free) vs AE (Aerosol dispenser, propellant-driven spray can): for consumer "Ready-to-Use" liquids, check HOW it is dispensed - trigger spray bottle = TD, pressurized can with propellant = AE, otherwise a small-volume RTU liquid with no clear applicator = AL.
- PO ("Pour-on") vs SA ("Spot-on"): both are veterinary externally-applied liquids; PO is HIGH volume (>100 mL/animal, poured along the back), SA is LOW volume (<100 mL/animal, a small spot). If the product name literally says "Pour-On" or "Spot-On", trust that unless contradicted by the label.
- TC ("Technical material") / TK ("Technical concentrate"): manufacturing-use/technical-grade active-ingredient products (often literally named "... Technical", "... MUP", "... TK", "... Crystalline") ARE a legitimate CIPAC formulation category - do NOT classify these as not_formulation. TC = no diluent (e.g. crystalline/technical solid); TK = includes diluents (e.g. a technical concentrate solution/paste).
- Gel bait / granular bait / other bait sub-types have no distinct CIPAC code beyond RB ("Bait, ready for use") in the current 66 - use RB and describe the physical sub-form in your notes.
- WP-SB / WG-SB etc.: if the product is packaged in a sealed water-soluble bag, append "-SB" to the base code (e.g. WP-SB), per the CIPAC record-keeping convention.

## When it is NOT a formulation at all
If the product is a raw chemical substance, essential oil/fragrance raw material, industrial reagent, analytical/HPLC standard, reference/calibration solution, or a non-agrochemical industrial product where no CIPAC code meaningfully applies (e.g. a marine antifouling PAINT/coating - these are coating products, not pesticide dosage forms, even though they contain biocides) - set resolution to "not_formulation" and pick the single best not_formulation_reason from: single_substance, analytical_standard, reagent, reference_solution, other.

## Verification method
For each product, search the web (product name + "SDS", "label", or "EPA registration"). Prefer, in this order: (1) the manufacturer's own SDS/label PDF, (2) www3.epa.gov/pesticides/chem_search/ppls/ (EPA-accepted label, most authoritative for US products), (3) pomerix.com or similar aggregators that mirror EPA's official "Formulation Type" registration field, (4) the given ingredient_source/tox_source_url/formulation_src hints - but VERIFY these are actually about the right product first, since many hints in this dataset turn out to be for an unrelated/mismatched product (wrong SDS, wrong brand, wrong active ingredients). If the CDPR California label site (apps.cdpr.ca.gov/cgi-bin/label/pir.pl) fails/errors, do not retry it - go straight to EPA/pomerix.

Read SDS Section 9 (Physical State/Appearance) and Section 3 (ingredients - do the listed actives' water solubility support a true solution vs. a suspension?) when no label explicitly states the CIPAC term.

Set source_mismatch_found=true and explain in notes if you discover the given hint URL is for a different/unrelated product.

Use resolution="unresolved" ONLY if you genuinely searched hard and found nothing usable - do not guess a code or reason.

## Products to research (JSON)
${JSON.stringify(batch, null, 2)}

Return your findings via the required structured output. For each product, populate formulation_code with the exact 2-letter (or 2-letter+"-SB") CIPAC code from the reference tables above (or null if resolution is not_formulation/unresolved), and formulation_type_en with the exact English term from the reference table for that code (or the literal English term found in the source document if truly no code applies but it is still resolution="formulation" - this should be rare given how comprehensive the 96-code table is).`
}

function verifyPrompt(item) {
  return `Independently double-check this formulation classification for a pesticide/biocide product. Be skeptical - your job is to catch a wrong classification, not rubber-stamp it.

Product: ${item.product_name}
Claimed classification: resolution=${item.resolution}, formulation_code=${item.formulation_code || 'null'} (${item.formulation_type_en || ''}), not_formulation_reason=${item.not_formulation_reason || 'null'}
Claimed evidence: ${item.notes}
Source cited: ${item.source_url || 'none'}

Re-derive the answer from scratch using the same product name and a fresh web search (SDS/EPA label). Pay special attention to the SC-vs-SL / FS-vs-LS (suspension vs true solution) distinction, and PO-vs-SA (pour-on vs spot-on volume) if applicable. Report whether the original classification should be revised, and if so, what it should be instead.`
}

phase('Research')
const batches = []
for (let i = 0; i < products.length; i += BATCH_SIZE) batches.push(products.slice(i, i + BATCH_SIZE))
log(`Researching ${products.length} products in ${batches.length} batches of up to ${BATCH_SIZE}`)

const batchResults = await parallel(batches.map((batch, i) => () =>
  agent(researchPrompt(batch), { label: `research-batch-${i}`, phase: 'Research', schema: RESULT_SCHEMA })
))

const flat = batchResults.filter(Boolean).flatMap(r => r.results || [])
log(`Research complete: ${flat.length} products classified, ${flat.filter(r => r.confidence === 'low').length} flagged low-confidence for verification`)

phase('Verify')
const lowConfidence = flat.filter(r => r.confidence === 'low')
const verifiedLow = await parallel(lowConfidence.map(item => () =>
  agent(verifyPrompt(item), { label: `verify-row-${item.row}`, phase: 'Verify', schema: VERIFY_SCHEMA })
    .then(v => {
      if (!v) return item
      if (!v.revised) return { ...item, confidence: v.confidence, notes: item.notes + ' | VERIFIED, no change.' }
      return {
        ...item,
        resolution: v.still_formulation_code ? 'formulation' : (v.still_not_formulation_reason ? 'not_formulation' : item.resolution),
        formulation_code: v.still_formulation_code !== undefined ? v.still_formulation_code : item.formulation_code,
        not_formulation_reason: v.still_not_formulation_reason !== undefined ? v.still_not_formulation_reason : item.not_formulation_reason,
        confidence: v.confidence,
        notes: item.notes + ' | VERIFY REVISION: ' + v.notes,
      }
    })
))

const verifiedByRow = new Map(verifiedLow.filter(Boolean).map(v => [v.row, v]))
const finalResults = flat.map(r => verifiedByRow.get(r.row) || r)

log(`Done. ${finalResults.filter(r => r.resolution === 'formulation').length} formulations, ${finalResults.filter(r => r.resolution === 'not_formulation').length} not-formulation, ${finalResults.filter(r => r.resolution === 'unresolved').length} unresolved`)

return { results: finalResults }
