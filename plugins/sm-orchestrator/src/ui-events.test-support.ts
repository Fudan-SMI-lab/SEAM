import { mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import type { ToolContext } from "@opencode-ai/plugin"

export const EXPECTED_SCHEMA_KEYS = [
  "agent_role",
  "artifact_path",
  "details",
  "event_type",
  "message",
  "phase_id",
  "run_id",
  "schema_version",
  "session_id",
  "status",
  "subphase_id",
  "timestamp",
] as const

const ENV_KEYS = [
  "SEAM_UI_EVENTS_PATH",
  "SEAM_RUN_ID",
  "RUN_ID",
  "PHASE_ID",
  "SM_AGENT_TYPE",
  "AGENT_TYPE",
  "REPAIR_AGENT_TYPE",
] as const

let savedEnv: ReadonlyMap<string, string | undefined> = new Map()
let tempDir = ""

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function expectRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error(`${label} must be an object`)
  }
  return value
}

export function expectString(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new Error(`${label} must be a string`)
  }
  return value
}

export function readRecords(path: string): readonly Record<string, unknown>[] {
  return readFileSync(path, "utf8")
    .split("\n")
    .filter((line) => line.length > 0)
    .map((line) => expectRecord(JSON.parse(line), "ui_events line"))
}

export function setEnv(entries: Readonly<Record<string, string | undefined>>): void {
  for (const [key, value] of Object.entries(entries)) {
    if (value === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = value
    }
  }
}

export function makeToolContext(): ToolContext {
  return {
    sessionID: "ses-test",
    messageID: "msg-test",
    agent: "test-agent",
    directory: ".",
    worktree: ".",
    abort: new AbortController().signal,
    metadata: () => {},
    ask: () => {
      throw new Error("ask is not used by sm_phase_complete")
    },
  }
}

export function tempPath(...parts: readonly string[]): string {
  return join(tempDir, ...parts)
}

export function setupEventCapture(): void {
  savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]))
  tempDir = mkdtempSync(join(tmpdir(), "seam-ui-events-test-"))
}

export function teardownEventCapture(): void {
  for (const key of ENV_KEYS) {
    const value = savedEnv.get(key)
    if (value === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = value
    }
  }
  rmSync(tempDir, { recursive: true, force: true })
}

export type UiEventInputLike = {
  readonly eventType: string
  readonly phaseId?: string | null
  readonly sessionId?: string | null
  readonly status?: string
  readonly message?: unknown
  readonly details?: unknown
  readonly artifactPath?: unknown
}

type EmitUiEvent = (input: UiEventInputLike) => void

function hasEmitUiEvent(module: unknown): module is { readonly emitUiEvent: EmitUiEvent } {
  return isRecord(module) && typeof module.emitUiEvent === "function"
}

export async function emitViaSharedWriter(input: UiEventInputLike): Promise<void> {
  const module: unknown = await import("./ui-events")
  if (!hasEmitUiEvent(module)) {
    throw new Error("emitUiEvent export missing from ./ui-events")
  }
  module.emitUiEvent(input)
}
