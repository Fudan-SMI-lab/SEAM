const TOOL_WHITELIST = {
  build: ["bash", "read", "write", "edit"],
  ultrabrain: ["bash", "read", "write", "edit", "grep", "lsp_diagnostics", "ast_grep"],
  default: ["bash", "read", "write", "edit", "grep", "glob", "lsp_diagnostics"],
} as const

const PHASE_ENV_KEYS = ["RUN_ID", "PHASE_ID", "ATTEMPT", "SM_ARTIFACTS_DIR"] as const

type HookInput = Record<string, unknown>

type HookOutput = {
  args?: Record<string, unknown>
  env?: Record<string, string>
  status?: "ask" | "allow" | "deny"
  system?: string[]
}

function getAgentType(): keyof typeof TOOL_WHITELIST {
  const rawAgentType =
    process.env.SM_AGENT_TYPE ?? process.env.AGENT_TYPE ?? process.env.REPAIR_AGENT_TYPE ?? "default"

  return rawAgentType in TOOL_WHITELIST
    ? (rawAgentType as keyof typeof TOOL_WHITELIST)
    : "default"
}

function getAllowedTools(): Set<string> {
  return new Set([...TOOL_WHITELIST[getAgentType()], "sm_phase_complete"])
}

function getRequestedToolName(input: HookInput): string | undefined {
  const directTool = input.tool
  if (typeof directTool === "string") {
    return directTool
  }

  const permissionTool = input.permission
  if (permissionTool && typeof permissionTool === "object") {
    const toolName = (permissionTool as Record<string, unknown>).tool
    if (typeof toolName === "string") {
      return toolName
    }
  }

  const name = input.name
  if (typeof name === "string") {
    return name
  }

  return undefined
}

function buildPhaseContract(): string {
  const phaseId = process.env.PHASE_ID ?? "unknown"
  const runId = process.env.RUN_ID ?? "unknown"
  const attempt = process.env.ATTEMPT ?? "0"
  const artifactsDir = process.env.SM_ARTIFACTS_DIR ?? ".sm-artifacts"
  const allowedTools = [...getAllowedTools()].sort().join(", ")

  return [
    "SM phase contract:",
    `- RUN_ID=${runId}`,
    `- PHASE_ID=${phaseId}`,
    `- ATTEMPT=${attempt}`,
    `- SM_ARTIFACTS_DIR=${artifactsDir}`,
    `- Allowed tools for this phase: ${allowedTools}`,
  ].join("\n")
}

function buildOutputConstraints(): string {
  return [
    "Output format constraints:",
    "- Do not signal completion with free-form prose.",
    "- Call sm_phase_complete exactly once when the phase work is done.",
    "- Pass the active PHASE_ID as phase_id and return all structured data inside output_data.",
  ].join("\n")
}

export const hooks = {
  "shell.env": async (_input: HookInput, output: HookOutput) => {
    output.env ??= {}

    for (const key of PHASE_ENV_KEYS) {
      const value = process.env[key]
      if (value) {
        output.env[key] = value
      }
    }
  },

  "tool.execute.before": async (input: HookInput) => {
    const toolName = getRequestedToolName(input)
    if (!toolName) {
      return
    }

    if (!getAllowedTools().has(toolName)) {
      throw new Error(
        `Tool ${toolName} is not allowed for agent type ${getAgentType()} in phase ${process.env.PHASE_ID ?? "unknown"}`,
      )
    }
  },

  "experimental.chat.system.transform": async (_input: HookInput, output: HookOutput) => {
    output.system = [buildPhaseContract(), buildOutputConstraints(), ...(output.system ?? [])]
  },

  "permission.ask": async (input: HookInput, output: HookOutput) => {
    const toolName = getRequestedToolName(input)
    if (toolName && getAllowedTools().has(toolName)) {
      output.status = "allow"
    }
  },
}
