import { existsSync, readFileSync } from "node:fs"
import { afterEach, beforeEach, describe, expect, test } from "bun:test"

import {
  EXPECTED_SCHEMA_KEYS,
  expectRecord,
  expectString,
  emitViaSharedWriter,
  readRecords,
  setEnv,
  setupEventCapture,
  teardownEventCapture,
  tempPath,
} from "./ui-events.test-support"

beforeEach(setupEventCapture)
afterEach(teardownEventCapture)

describe("shared ui-events writer contract", () => {
  test("redacts nested sensitive keys, Bearer tokens, and named secrets in details", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })

    await emitViaSharedWriter({
      eventType: "test_event",
      message: "nested redaction",
      details: {
        headers: { Authorization: "Bearer abc.def-ghi" },
        credentials: [{ password: "pw-sentinel-9" }],
        note: "OPENAI_API_KEY=sk-abcdefghijklmnop1234",
        safe: "visible",
      },
    })

    const raw = readFileSync(eventsPath, "utf8")
    expect(raw).not.toContain("abc.def-ghi")
    expect(raw).not.toContain("pw-sentinel-9")
    expect(raw).not.toContain("sk-abcdefghijklmnop1234")
    expect(raw).toContain("<REDACTED>")
    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    expect(Object.keys(records[0]).sort()).toEqual([...EXPECTED_SCHEMA_KEYS])
    const details = expectRecord(records[0].details, "details")
    expect(details.safe).toBe("visible")
    expect(details.note).toBe("OPENAI_API_KEY=<REDACTED>")
  })

  test("redacts GitHub PATs and quoted/split CLI forms in message and collapses whitespace", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })

    await emitViaSharedWriter({
      eventType: "test_event",
      message:
        'clone\n--token "ghp_aaaaaaaaaaaaaaaaaaaa" --Api-Key=\'quoted sentinel\' --password splitsecret7 done',
    })

    const raw = readFileSync(eventsPath, "utf8")
    expect(raw).not.toContain("ghp_aaaaaaaaaaaaaaaaaaaa")
    expect(raw).not.toContain("quoted sentinel")
    expect(raw).not.toContain("splitsecret7")
    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const message = expectString(records[0].message, "message")
    expect(message).not.toContain("\n")
    expect(message).toContain("--password <REDACTED>")
    expect(message).toContain("done")
  })

  test("bounds text leaves to 500 chars, collections to 50 entries, and depth to 6", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })
    let deep: Record<string, unknown> = { leaf: "bottom" }
    for (let index = 0; index < 8; index += 1) {
      deep = { nested: deep }
    }

    await emitViaSharedWriter({
      eventType: "test_event",
      message: "bounds",
      details: {
        longText: "y".repeat(600),
        items: Array.from({ length: 60 }, (_, index) => index),
        deep,
      },
    })

    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const details = expectRecord(records[0].details, "details")
    expect(expectString(details.longText, "longText").length).toBeLessThanOrEqual(500)
    const items = details.items
    if (!Array.isArray(items)) {
      throw new Error("items must stay an array")
    }
    expect(items).toHaveLength(50)
    let node: unknown = details.deep
    for (let depth = 2; depth <= 6; depth += 1) {
      node = expectRecord(node, `depth ${depth}`).nested
    }
    expectString(node, "depth 7 placeholder")
  })

  test("caps the encoded record below 64 KiB with a truncated preview", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })
    const leaf = "z".repeat(500)
    const fan: Record<string, unknown> = {}
    for (let index = 0; index < 50; index += 1) {
      fan[`k${index}`] = leaf
    }
    const wide: Record<string, unknown> = {}
    for (let index = 0; index < 50; index += 1) {
      wide[`w${index}`] = fan
    }

    await emitViaSharedWriter({
      eventType: "test_event",
      message: "oversize",
      details: { payload: wide },
    })

    const firstLine = readFileSync(eventsPath, "utf8").split("\n")[0]
    expect(new TextEncoder().encode(firstLine).length).toBeLessThan(65536)
    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const record = records[0]
    expect(Object.keys(record).sort()).toEqual([...EXPECTED_SCHEMA_KEYS])
    const details = expectRecord(record.details, "details")
    expect(details.truncated).toBe(true)
    expect(expectString(details.preview, "preview").length).toBeGreaterThan(0)
  })

  test("replaces cyclic and non-JSON values with bounded strings and never throws", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })
    const cyclic: Record<string, unknown> = { name: "cyc" }
    cyclic.self = cyclic

    await emitViaSharedWriter({
      eventType: "test_event",
      message: "cyclic",
      details: { cyclic, fn: () => 1, missing: undefined, big: BigInt(10) },
    })

    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const details = expectRecord(records[0].details, "details")
    expect(expectRecord(details.cyclic, "cyclic").self).toBe("[Circular]")
    expectString(details.fn, "fn")
    expect(details.missing).toBe("undefined")
    expect(details.big).toBe("10")
  })

  test("stays silent on an unwritable path and on a missing path env", async () => {
    const missingParentPath = tempPath("missing", "sub", "ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: missingParentPath })

    await emitViaSharedWriter({ eventType: "test_event", message: "unwritable" })
    expect(existsSync(missingParentPath)).toBe(false)

    setEnv({ SEAM_UI_EVENTS_PATH: undefined })
    await emitViaSharedWriter({ eventType: "test_event", message: "no path" })
  })

  test("stays silent when a details Proxy throws during inspection", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })
    const details = new Proxy({}, {
      getPrototypeOf: () => {
        throw new Error("proxy trap")
      },
    })

    await emitViaSharedWriter({ eventType: "test_event", details })

    expect(existsSync(eventsPath)).toBe(false)
  })

  test("stays silent when a details getter throws during enumeration", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })
    const details = {
      get value(): string {
        throw new Error("getter trap")
      },
    }

    await emitViaSharedWriter({ eventType: "test_event", details })

    expect(existsSync(eventsPath)).toBe(false)
  })

  test("stays silent when message string conversion throws", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })
    const message = {
      toString: () => {
        throw new Error("message conversion failed")
      },
    }

    await emitViaSharedWriter({ eventType: "test_event", message })

    expect(existsSync(eventsPath)).toBe(false)
  })
})
