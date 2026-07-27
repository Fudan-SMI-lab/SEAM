import { tool } from "@opencode-ai/plugin"
import { appendFileSync } from "node:fs"

const PHASE_ID_ENV_KEY = "PHASE_ID"

function getExpectedPhaseId(): string | undefined {
  return process.env[PHASE_ID_ENV_KEY]?.trim() || undefined
}

function emitPhaseCompleteEvent(phaseId: string) {
  const path = process.env.SEAM_UI_EVENTS_PATH
  if (!path) {
    return
  }
  const record = {
    schema_version: "1.0",
    timestamp: new Date().toISOString(),
    run_id: process.env.SEAM_RUN_ID ?? process.env.RUN_ID ?? "",
    event_type: "opencode_phase_complete",
    phase_id: phaseId,
    subphase_id: null,
    agent_role: process.env.SM_AGENT_TYPE ?? process.env.AGENT_TYPE ?? process.env.REPAIR_AGENT_TYPE ?? null,
    session_id: null,
    status: "passed",
    message: `Phase ${phaseId} submitted structured output`,
    details: {},
    artifact_path: null,
  }
  try {
    appendFileSync(path, `${JSON.stringify(record)}\n`, "utf8")
  } catch {
    // UI telemetry is non-critical.
  }
}

export const smPhaseCompleteTool = tool({
  description: "Submit structured phase completion output with schema-validated data",
  args: {
    phase_id: tool.schema
      .string()
      .describe("Phase identifier for the active session"),
    output_data: tool.schema
      .object({})
      .describe("Structured phase output payload to hand back to the orchestrator"),
  },
  async execute(args) {
    const expectedPhaseId = getExpectedPhaseId()

    if (expectedPhaseId && args.phase_id !== expectedPhaseId) {
      throw new Error(
        `sm_phase_complete phase_id mismatch: expected ${expectedPhaseId}, received ${args.phase_id}`,
      )
    }

    emitPhaseCompleteEvent(args.phase_id)

    return JSON.stringify(
      {
        ok: true,
        phase_id: args.phase_id,
        output_data: args.output_data,
      },
      null,
      2,
    )
  },
})

export const tools = {
  sm_phase_complete: smPhaseCompleteTool,
}
