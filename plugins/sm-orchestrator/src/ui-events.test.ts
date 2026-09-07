import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { readFileSync } from "node:fs"

import { hooks } from "./hooks"
import { smPhaseCompleteTool } from "./tools"
import {
  EXPECTED_SCHEMA_KEYS,
  emitViaSharedWriter,
  expectRecord,
  expectString,
  makeToolContext,
  readRecords,
  setEnv,
  setupEventCapture,
  tempPath,
  teardownEventCapture,
} from "./ui-events.test-support"

beforeEach(setupEventCapture)
afterEach(teardownEventCapture)

describe("baseline 12-key schema (current behavior)", () => {
  test("hook tool.execute.before persists the exact schema for safe data", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({
      SEAM_UI_EVENTS_PATH: eventsPath,
      SEAM_RUN_ID: "run-baseline",
      PHASE_ID: "phase_1_project_analysis",
      SM_AGENT_TYPE: "build",
    })

    await hooks["tool.execute.before"]({ tool: "bash", sessionID: "ses-1" })

    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const record = records[0]
    expect(Object.keys(record).sort()).toEqual([...EXPECTED_SCHEMA_KEYS])
    expect(record.schema_version).toBe("1.0")
    expect(record.run_id).toBe("run-baseline")
    expect(record.event_type).toBe("opencode_tool_started")
    expect(record.phase_id).toBe("phase_1_project_analysis")
    expect(record.subphase_id).toBeNull()
    expect(record.agent_role).toBe("build")
    expect(record.session_id).toBe("ses-1")
    expect(record.status).toBe("running")
    expect(record.message).toBe("bash")
    expect(record.artifact_path).toBeNull()
    expect(expectRecord(record.details, "details")).toEqual({ tool: "bash" })
  })

  test("phase-complete tool persists the exact schema for safe data", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({
      SEAM_UI_EVENTS_PATH: eventsPath,
      SEAM_RUN_ID: "run-baseline-2",
      PHASE_ID: "phase_5_validation",
      SM_AGENT_TYPE: "build",
    })

    const result = await smPhaseCompleteTool.execute(
      { phase_id: "phase_5_validation", output_data: {} },
      makeToolContext(),
    )

    expect(typeof result).toBe("string")
    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const record = records[0]
    expect(Object.keys(record).sort()).toEqual([...EXPECTED_SCHEMA_KEYS])
    expect(record.schema_version).toBe("1.0")
    expect(record.run_id).toBe("run-baseline-2")
    expect(record.event_type).toBe("opencode_phase_complete")
    expect(record.phase_id).toBe("phase_5_validation")
    expect(record.subphase_id).toBeNull()
    expect(record.agent_role).toBe("build")
    expect(record.session_id).toBeNull()
    expect(record.status).toBe("passed")
    expect(record.message).toBe("Phase phase_5_validation submitted structured output")
    expect(record.artifact_path).toBeNull()
    expect(record.details).toEqual({})
  })
})

describe("secret redaction through public surfaces", () => {
  test("hook event redacts token text in message and details", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({
      SEAM_UI_EVENTS_PATH: eventsPath,
      SEAM_RUN_ID: "run-red-hook",
      PHASE_ID: "phase_5_validation",
      SM_AGENT_TYPE: "build",
    })

    await expect(
      hooks["tool.execute.before"]({ tool: "evil sk-abcdefghijklmnop1234" }),
    ).rejects.toThrow()

    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    expect(JSON.stringify(records[0])).not.toContain("sk-abcdefghijklmnop1234")
  })

  test("phase-complete message redacts token text and structural phase_id together", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    const secretPhase = "phase_5 sk-abcdefghijklmnop1234"
    setEnv({
      SEAM_UI_EVENTS_PATH: eventsPath,
      SEAM_RUN_ID: "run-red-phase",
      PHASE_ID: secretPhase,
      SM_AGENT_TYPE: "build",
    })

    await smPhaseCompleteTool.execute(
      { phase_id: secretPhase, output_data: {} },
      makeToolContext(),
    )

    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const record = records[0]
    const phaseId = expectString(record.phase_id, "phase_id")
    const message = expectString(record.message, "message")
    expect(phaseId).toContain("phase_5")
    expect(phaseId).not.toContain("sk-abcdefghijklmnop1234")
    expect(message).not.toContain("sk-abcdefghijklmnop1234")
  })
})

describe("Authorization Bearer and structural-field redaction through public surfaces", () => {
  test("hook message redacts Authorization: Bearer token across casing variants", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({
      SEAM_UI_EVENTS_PATH: eventsPath,
      SEAM_RUN_ID: "run-bearer",
      PHASE_ID: "phase_5_validation",
      SM_AGENT_TYPE: "build",
    })

    await expect(
      hooks["tool.execute.before"]({
        tool: "evil Authorization: bearer abc.def-ghi AUTH: Bearer TOK-9 BEARER xyz",
        sessionID: "ses-bearer",
      }),
    ).rejects.toThrow()

    const raw = readFileSync(eventsPath, "utf8")
    expect(raw).not.toContain("abc.def-ghi")
    expect(raw).not.toContain("TOK-9")
    expect(raw).not.toContain("xyz")
  })

  test("emitter redacts secret tokens carried in every structural field", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({
      SEAM_UI_EVENTS_PATH: eventsPath,
      SEAM_RUN_ID: "run sk-abcdefghijklmnop1234",
      PHASE_ID: "phase sk-abcdefghijklmnop1234",
      SM_AGENT_TYPE: "agent sk-abcdefghijklmnop1234",
    })

    await emitViaSharedWriter({
      eventType: "evt sk-abcdefghijklmnop1234",
      sessionId: "ses sk-abcdefghijklmnop1234",
      status: "ok sk-abcdefghijklmnop1234",
      message: "msg sk-abcdefghijklmnop1234 done",
    })

    const raw = readFileSync(eventsPath, "utf8")
    expect(raw).not.toContain("sk-abcdefghijklmnop1234")
    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const record = records[0]
    expect(Object.keys(record).sort()).toEqual([...EXPECTED_SCHEMA_KEYS])
    const runId = expectString(record.run_id, "run_id")
    const eventType = expectString(record.event_type, "event_type")
    const phaseId = expectString(record.phase_id, "phase_id")
    const agentRole = expectString(record.agent_role, "agent_role")
    const sessionId = expectString(record.session_id, "session_id")
    const status = expectString(record.status, "status")
    expect(runId).toContain("run")
    expect(eventType).toContain("evt")
    expect(phaseId).toContain("phase")
    expect(agentRole).toContain("agent")
    expect(sessionId).toContain("ses")
    expect(status).toContain("ok")
  })

  test("emitter redacts Bearer token and secret patterns in artifact_path", async () => {
    const eventsPath = tempPath("ui_events.jsonl")
    setEnv({ SEAM_UI_EVENTS_PATH: eventsPath })

    await emitViaSharedWriter({
      eventType: "artifact_event",
      message: "artifact test",
      artifactPath: "log Authorization: bearer abc.def-ghi sk-abcdefghijklmnop1234 end",
    })

    const raw = readFileSync(eventsPath, "utf8")
    expect(raw).not.toContain("abc.def-ghi")
    expect(raw).not.toContain("sk-abcdefghijklmnop1234")
    const records = readRecords(eventsPath)
    expect(records).toHaveLength(1)
    const artifactPath = expectString(records[0].artifact_path, "artifact_path")
    expect(artifactPath).toContain("log")
    expect(artifactPath).toContain("<REDACTED>")
  })
})
