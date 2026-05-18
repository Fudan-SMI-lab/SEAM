import { tool } from "@opencode-ai/plugin"

const PHASE_ID_ENV_KEY = "PHASE_ID"

function getExpectedPhaseId(): string | undefined {
  return process.env[PHASE_ID_ENV_KEY]?.trim() || undefined
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
