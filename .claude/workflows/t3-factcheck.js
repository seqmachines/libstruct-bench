export const meta = {
  name: 't3-factcheck',
  description: 'Fact-check a protocol T3 graph against its packet primary sources using a few stage auditors plus one adversarial reviewer',
  whenToUse: 'Before the final T3 approval gate in /audit-protocol. Pass args {protocol_id, t3_path, renditions_dir} — t3_path/renditions_dir optional, the planner discovers them.',
  phases: [
    { title: 'Plan', detail: 'read the T3 candidate and the rendition inventory, group nodes into chemistry stages' },
    { title: 'Audit', detail: 'one agent per stage, judging only against primary sources' },
    { title: 'Challenge', detail: 'one skeptic reviews every verified claim at once' },
  ],
}

// ---------------------------------------------------------------------------
// Shape chosen deliberately: a handful of stage auditors + ONE reviewer, never
// one agent per node. Per-node agents each re-read the same rendition files, so
// most of the cost is redundant reading; and the dominant failure mode (filling
// standard adapter sequences from memory) shows up as a pattern across auditors
// that a single reviewer can see and isolated refuters cannot.
// ---------------------------------------------------------------------------

const PID = (args && args.protocol_id) || null
if (!PID) throw new Error('t3-factcheck requires args.protocol_id')
const ROOT = '/Users/seqmachines/playground/protocols-test/ground_truth_audit'
const T3_HINT = (args && args.t3_path) || `${ROOT}/runs/${PID}/legacy-conversion-001/conversion.json`
const REND = (args && args.renditions_dir) || `${ROOT}/renditions/${PID}`
const MAX_STAGES = (args && args.max_stages) || 5

const RULES = `RULES FOR JUDGING:
- Judge ONLY against the primary sources listed in the plan. Legacy HTML, the curated JSON, the TSV projection and the conversion candidate are NOT evidence.
- NEVER supply a sequence or mechanism from your own knowledge. If the packet is silent the status is "missing", even when you are certain it is true in the real world. Many audit packets name reagents without printing a single base; expect "missing" to be common.
- Separate "the STEP is documented" from "the SEQUENCE is documented". They frequently differ.
- Journal PDF text extraction is often TWO-COLUMN and interleaves columns, so a failed grep does NOT prove absence. Try word fragments, and read the page images before concluding "missing".
- Give exact locators (file, line or page, short verbatim quote) for anything called verified or derivable.
- verified = the primary states it; derivable = the primary implies it unambiguously; missing = the primary is silent; conflict = the primary contradicts it; ambiguous = two readings survive.
- If a node's own properties admit an assumption or uncertainty ("the legacy curation assumes...", "uncertain whether..."), say so explicitly — that is curator inference, not primary support.`

const PLAN_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['primary_sources', 'stages', 'total_nodes'],
  properties: {
    primary_sources: { type: 'array', minItems: 1, items: { type: 'string' }, description: 'absolute paths to each primary rendition native-text.txt (and note where page images live)' },
    total_nodes: { type: 'integer' },
    stages: {
      type: 'array', minItems: 1,
      items: {
        type: 'object', additionalProperties: false,
        required: ['stage_id', 'label', 'node_brief'],
        properties: {
          stage_id: { type: 'string', description: 'short slug, e.g. cdna / frag / lig / lib' },
          label: { type: 'string' },
          node_brief: { type: 'string', description: 'for every node in this stage: its exact node id, its claimed content (architecture, sequences, properties, oligos, operation detail) and the key questions a primary source must answer' },
        },
      },
    },
  },
}

const NODE_VERDICTS = {
  type: 'object', additionalProperties: false, required: ['nodes'],
  properties: {
    nodes: {
      type: 'array', minItems: 1,
      items: {
        type: 'object', additionalProperties: false,
        required: ['node_id', 'status', 'summary', 'locators', 'unsupported_claims'],
        properties: {
          node_id: { type: 'string' },
          status: { type: 'string', enum: ['verified', 'derivable', 'missing', 'conflict', 'ambiguous'] },
          summary: { type: 'string' },
          locators: { type: 'array', items: { type: 'string' } },
          unsupported_claims: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const CHALLENGE = {
  type: 'object', additionalProperties: false, required: ['challenges'],
  properties: {
    challenges: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['node_id', 'refuted', 'corrected_status', 'reason'],
        properties: {
          node_id: { type: 'string' },
          refuted: { type: 'boolean' },
          corrected_status: { type: 'string', enum: ['verified', 'derivable', 'missing', 'conflict', 'ambiguous'] },
          reason: { type: 'string' },
        },
      },
    },
  },
}

phase('Plan')
const plan = await agent(
  `Prepare a T3 fact-check plan for protocol "${PID}".\n\n` +
  `1. Read the T3 candidate at ${T3_HINT}. If that path does not exist, look for the newest of: ` +
  `${ROOT}/runs/${PID}/legacy-conversion-*/conversion.json, ${ROOT}/runs/${PID}/comparison-*/audit.json (the T3 root issue's proposed_patch value), ` +
  `or ${ROOT}/../ground_truth/${PID}/groundtruth_library_generation_workflow.json.\n` +
  `2. List the primary-source renditions under ${REND} — one native-text.txt per primary source, plus page-*.png image paths.\n` +
  `3. Group EVERY T3 state and transition into at most ${MAX_STAGES} coherent chemistry stages that follow the graph order ` +
  `(e.g. cDNA generation; fragmentation; ligation; library and indexing). Every node must appear in exactly one stage.\n\n` +
  `For each stage write a node_brief that an auditor can work from WITHOUT reopening the T3: for every node give its exact node id, ` +
  `its claimed strand architecture and any literal sequences, its properties, its oligos and operation detail, and the key questions ` +
  `a primary source would have to answer to confirm it. Flag any property text that admits an assumption or uncertainty.\n` +
  `Do NOT judge anything yet and do NOT open the primary sources.`,
  { label: `plan:${PID}`, phase: 'Plan', schema: PLAN_SCHEMA }
)
if (!plan) throw new Error('planning agent returned nothing')
const SRC = `PRIMARY SOURCES (the ONLY admissible evidence):\n${plan.primary_sources.map(s => '  - ' + s).join('\n')}`
log(`${PID}: ${plan.total_nodes} T3 nodes in ${plan.stages.length} stages, ${plan.primary_sources.length} primary sources`)

phase('Audit')
const audits = await parallel(plan.stages.map(st => () =>
  agent(
    `You are auditing the curated T3 molecular workflow for protocol "${PID}".\n\n` +
    `Audit exactly these nodes — ${st.label}:\n${st.node_brief}\n\n${SRC}\n\n${RULES}\n\n` +
    `Return one entry per node, using the node ids exactly as given.`,
    { label: `audit:${st.stage_id}`, phase: 'Audit', schema: NODE_VERDICTS }
  )))

const all = audits.filter(Boolean).flatMap(a => a.nodes)
const covered = all.length
if (covered < plan.total_nodes) log(`WARNING: ${covered}/${plan.total_nodes} nodes returned — ${plan.total_nodes - covered} missing from auditor output`)

const claimed = all.filter(n => n.status === 'verified' || n.status === 'derivable')
let challenges = []
if (claimed.length) {
  phase('Challenge')
  const res = await agent(
    `You are a skeptical audit reviewer for protocol "${PID}". Stage auditors judged its T3 nodes against the primary sources. ` +
    `Below are ONLY the nodes claimed verified or derivable. REFUTE each one.\n\n${JSON.stringify(claimed, null, 1)}\n\n${SRC}\n\n${RULES}\n\n` +
    `Open the cited locators and confirm they say what is claimed. Hunt for three failure modes, and for PATTERNS of them across auditors:\n` +
    `  1. Confusing "the step is documented" with "the sequence is documented".\n` +
    `  2. Citing legacy HTML or the curated JSON instead of a primary source.\n` +
    `  3. Filling in standard Illumina / SMART / vendor knowledge from memory and calling it verified.\n` +
    `Return one entry per node given. refuted=true plus a corrected_status when the primary does not support the claim. Default to refuted=true when uncertain.`,
    { label: 'challenge:all', phase: 'Challenge', schema: CHALLENGE }
  )
  challenges = res ? res.challenges : []
}

const byId = {}
challenges.forEach(c => { byId[c.node_id] = c })
const rows = all.map(n => {
  const c = byId[n.node_id]
  return {
    node: n.node_id,
    claimed: n.status,
    final: c && c.refuted ? c.corrected_status : n.status,
    overturned: c ? c.refuted : null,
    summary: n.summary,
    locators: n.locators,
    unsupported: n.unsupported_claims,
    challenge: c ? c.reason : null,
  }
})
const tally = {}
rows.forEach(r => { tally[r.final] = (tally[r.final] || 0) + 1 })
log(`${PID} fact-check: ${JSON.stringify(tally)}${challenges.filter(c => c.refuted).length ? ` (${challenges.filter(c => c.refuted).length} claims overturned)` : ''}`)
return { protocol_id: PID, nodes_expected: plan.total_nodes, nodes_reported: covered, tally, rows }
