import { appendFileSync } from "node:fs"

import {
  type UiEventRecord,
  encodeRecordLine,
  normalizeDetails,
} from "./ui-event-record"
import { normalizeOptionalText, redactHead, summarizeText } from "./ui-event-sanitizer"

const SCHEMA_VERSION = "1.0"
const TEXT_LIMIT = 500

export type UiEventInput = {
  readonly eventType: string
  readonly phaseId?: string | null
  readonly sessionId?: string | null
  readonly status?: string
  readonly message?: unknown
  readonly details?: unknown
  readonly artifactPath?: unknown
}

export function emitUiEvent(input: UiEventInput): void {
  const path = process.env.SEAM_UI_EVENTS_PATH
  if (!path) {
    return
  }
  try {
    const record: UiEventRecord = {
      schema_version: SCHEMA_VERSION,
      timestamp: new Date().toISOString(),
      run_id: redactHead(process.env.SEAM_RUN_ID ?? process.env.RUN_ID ?? ""),
      event_type: redactHead(input.eventType),
      phase_id:
        input.phaseId === undefined
          ? normalizeOptionalText(process.env.PHASE_ID ?? null)
          : normalizeOptionalText(input.phaseId),
      subphase_id: null,
      agent_role: normalizeOptionalText(
        process.env.SM_AGENT_TYPE ?? process.env.AGENT_TYPE ?? process.env.REPAIR_AGENT_TYPE ?? null,
      ),
      session_id: normalizeOptionalText(input.sessionId ?? null),
      status: redactHead(input.status ?? "running"),
      message: summarizeText(input.message ?? input.eventType, TEXT_LIMIT),
      details: normalizeDetails(input.details ?? {}),
      artifact_path:
        typeof input.artifactPath === "string" ? redactHead(input.artifactPath) : null,
    }
    const line = encodeRecordLine(record)
    if (line === null) {
      return
    }
    appendFileSync(path, line, "utf8")
  } catch {
    // UI telemetry is non-critical.
  }
}
