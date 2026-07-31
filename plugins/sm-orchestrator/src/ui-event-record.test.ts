import { describe, expect, test } from "bun:test"

import {
  MAX_RECORD_BYTES,
  type UiEventRecord,
  encodeRecordLine,
  normalizeDetails,
} from "./ui-event-record"
import { expectRecord } from "./ui-events.test-support"

describe("normalizeDetails - secret-bearing KEY names", () => {
  test("redacts a sk- secret at a word boundary in a details key name", () => {
    const out = expectRecord(
      normalizeDetails({ "log sk-abcdefghijklmnop1234 end": "safe" }),
      "details",
    )
    const serialized = JSON.stringify(out)
    expect(serialized).not.toContain("sk-abcdefghijklmnop1234")
    expect(serialized).toContain("<REDACTED_API_KEY>")
    const keys = Object.keys(out)
    expect(keys).toHaveLength(1)
    expect(keys[0]).toBe("log <REDACTED_API_KEY> end")
    // Python parity: the marker <REDACTED_API_KEY> re-trips the sensitive-name rule on the redacted key.
    expect(out[keys[0]]).toBe("<REDACTED>")
  })

  test("bounds an over-long key name to the text limit", () => {
    const longKey = "x".repeat(700)
    const out = expectRecord(normalizeDetails({ [longKey]: 1 }), "details")
    const keys = Object.keys(out)
    expect(keys).toHaveLength(1)
    expect(keys[0].length).toBeLessThanOrEqual(500)
  })

  test("keeps exactly 12 safe entries intact for benign input", () => {
    const safe: Record<string, number> = {}
    for (let i = 0; i < 12; i += 1) {
      safe[`k${i}`] = i
    }
    const out = expectRecord(normalizeDetails(safe), "details")
    expect(Object.keys(out)).toHaveLength(12)
  })
})

describe("encodeRecordLine - strict 64 KiB boundary", () => {
  function buildRecord(detailsSize: number): UiEventRecord {
    return {
      schema_version: "1.0",
      timestamp: "2024-01-01T00:00:00.000Z",
      run_id: "run",
      event_type: "evt",
      phase_id: null,
      subphase_id: null,
      agent_role: null,
      session_id: null,
      status: "running",
      message: "m",
      details: { padding: "p".repeat(detailsSize) },
      artifact_path: null,
    }
  }

  function encodedByteLength(record: UiEventRecord): number {
    return new TextEncoder().encode(`${JSON.stringify(record)}\n`).length
  }

  function tuneSize(target: number): number {
    let lo = 0
    let hi = 70000
    while (lo + 1 < hi) {
      const mid = (lo + hi) >> 1
      if (encodedByteLength(buildRecord(mid)) < target) {
        lo = mid
      } else {
        hi = mid
      }
    }
    return hi
  }

  test("MAX_RECORD_BYTES is exactly 65536", () => {
    expect(MAX_RECORD_BYTES).toBe(65536)
  })

  test("record at exactly 65536 bytes is truncated, never persisted at the cap", () => {
    const atBoundary = buildRecord(tuneSize(65536))
    expect(encodedByteLength(atBoundary)).toBe(65536)
    const line = encodeRecordLine(atBoundary)
    if (line === null) {
      throw new Error("expected a truncated line, got null")
    }
    const lineBytes = new TextEncoder().encode(line).length
    expect(lineBytes).toBeLessThan(65536)
    const details = expectRecord(expectRecord(JSON.parse(line), "line").details, "details")
    expect(details.truncated).toBe(true)
  })

  test("record sized just under 65535 bytes persists without truncation", () => {
    const underBoundary = buildRecord(tuneSize(65535) - 1)
    expect(encodedByteLength(underBoundary)).toBeLessThan(65535)
    const line = encodeRecordLine(underBoundary)
    if (line === null) {
      throw new Error("expected a non-truncated line, got null")
    }
    const details = expectRecord(expectRecord(JSON.parse(line), "line").details, "details")
    expect(details.truncated).toBe(undefined)
  })
})
