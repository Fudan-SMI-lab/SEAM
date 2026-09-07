// Record shaping: bound structure, recursive redaction, and 64 KiB encoding.
// Direct port of src/core/ui_event_sanitizer.py normalize_details pipeline
// (bound -> redact -> truncate) plus the encoded-record byte cap.

import {
  REDACTED,
  TEXT_LIMIT,
  type JsonValue,
  boundedText,
  inputSafeWindow,
  isSensitiveName,
  redactHead,
  redactSensitiveText,
  toBoundedString,
} from "./ui-event-sanitizer"

const COLLECTION_LIMIT = 50
const DEPTH_LIMIT = 6
export const MAX_RECORD_BYTES = 65536

export type UiEventRecord = {
  readonly schema_version: string
  readonly timestamp: string
  readonly run_id: string
  readonly event_type: string
  readonly phase_id: string | null
  readonly subphase_id: string | null
  readonly agent_role: string | null
  readonly session_id: string | null
  readonly status: string
  readonly message: string
  readonly details: JsonValue
  readonly artifact_path: string | null
}

type BoundContext = {
  readonly depth: number
  readonly seen: Set<object>
}

function isPlainObject(value: object): boolean {
  const prototype: unknown = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function boundStruct(value: unknown, context: BoundContext): JsonValue {
  if (typeof value === "string") {
    return value.length > TEXT_LIMIT ? inputSafeWindow(value, TEXT_LIMIT) : value
  }
  if (value === null) {
    return null
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : String(value)
  }
  if (typeof value === "boolean") {
    return value
  }
  if (Array.isArray(value)) {
    if (context.seen.has(value)) {
      return "[Circular]"
    }
    if (context.depth > DEPTH_LIMIT) {
      return "[Array]"
    }
    context.seen.add(value)
    const items = value
      .slice(0, COLLECTION_LIMIT)
      .map((item) =>
        boundStruct(item, { depth: context.depth + 1, seen: context.seen }),
      )
    context.seen.delete(value)
    return items
  }
  if (typeof value === "object" && isPlainObject(value)) {
    if (context.seen.has(value)) {
      return "[Circular]"
    }
    if (context.depth > DEPTH_LIMIT) {
      return "[Object]"
    }
    context.seen.add(value)
    const result: Record<string, JsonValue> = {}
    for (const [childKey, childValue] of Object.entries(value).slice(0, COLLECTION_LIMIT)) {
      result[redactHead(childKey)] = boundStruct(childValue, {
        depth: context.depth + 1,
        seen: context.seen,
      })
    }
    context.seen.delete(value)
    return result
  }
  return toBoundedString(value)
}

function redactJsonValue(value: JsonValue, key: string, sensitiveParent: boolean): JsonValue {
  const sensitive = sensitiveParent || (key !== "" && isSensitiveName(key))
  if (typeof value === "string") {
    return sensitive ? REDACTED : redactSensitiveText(value)
  }
  if (value === null || typeof value !== "object") {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactJsonValue(item, "", sensitive))
  }
  const result: Record<string, JsonValue> = {}
  for (const [childKey, childValue] of Object.entries(value)) {
    result[childKey] = redactJsonValue(childValue, childKey, sensitive)
  }
  return result
}

function truncateStrings(value: JsonValue): JsonValue {
  if (typeof value === "string") {
    return boundedText(value)
  }
  if (value === null || typeof value !== "object") {
    return value
  }
  if (Array.isArray(value)) {
    return value.map(truncateStrings)
  }
  const result: Record<string, JsonValue> = {}
  for (const [key, child] of Object.entries(value)) {
    result[key] = truncateStrings(child)
  }
  return result
}

export function normalizeDetails(details: unknown): JsonValue {
  const bounded = boundStruct(details, { depth: 1, seen: new Set() })
  const redacted = redactJsonValue(bounded, "", false)
  return truncateStrings(redacted)
}

const textEncoder = new TextEncoder()

export function encodeRecordLine(record: UiEventRecord): string | null {
  try {
    const line = `${JSON.stringify(record)}\n`
    if (textEncoder.encode(line).length < MAX_RECORD_BYTES) {
      return line
    }
    const skeleton: UiEventRecord = { ...record, details: { truncated: true, preview: "" } }
    const overhead = textEncoder.encode(`${JSON.stringify(skeleton)}\n`).length
    const budget = Math.max(0, Math.floor((MAX_RECORD_BYTES - overhead) / 4))
    const preview = [...JSON.stringify(record.details)].slice(0, budget).join("")
    const bounded: UiEventRecord = { ...record, details: { truncated: true, preview } }
    const boundedLine = `${JSON.stringify(bounded)}\n`
    if (textEncoder.encode(boundedLine).length < MAX_RECORD_BYTES) {
      return boundedLine
    }
    return null
  } catch {
    return null
  }
}
