import type { Plugin } from "@opencode-ai/plugin"

import { hooks } from "./hooks"
import { tools } from "./tools"

export const SmOrchestratorPlugin: Plugin = async () => {
  return {
    ...hooks,
    tool: tools,
  }
}

export default SmOrchestratorPlugin
