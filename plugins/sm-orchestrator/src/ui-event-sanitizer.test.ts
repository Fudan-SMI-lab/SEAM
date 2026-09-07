import { describe, expect, test } from "bun:test"

import {
  normalizeOptionalText,
  redactHead,
  redactSensitiveText,
  summarizeText,
} from "./ui-event-sanitizer"

describe("redactSensitiveText - Bearer scheme casing preserved, case-insensitive", () => {
  test("lowercase bearer in Authorization header is fully redacted", () => {
    expect(redactSensitiveText("Authorization: bearer abc.def-ghi")).toBe(
      "Authorization: <REDACTED> <REDACTED>",
    )
  })

  test("mixed-case Bearer preserves scheme casing while removing the token", () => {
    expect(redactSensitiveText("hdr: Bearer xyz-TOK9")).toBe("hdr: Bearer <REDACTED>")
    expect(redactSensitiveText("hdr: BEARER TOK-XYZ-999")).toBe("hdr: BEARER <REDACTED>")
    expect(redactSensitiveText("hdr: bearer tok_mixed_1")).toBe("hdr: bearer <REDACTED>")
  })

  test("raw Bearer token without a name:value wrapper still drops the token", () => {
    expect(redactSensitiveText("leak: Bearer abc.def-ghi tail")).toBe("leak: Bearer <REDACTED> tail")
  })
})

describe("redactSensitiveText - concrete patterns run before named-value collapse", () => {
  test("sk- and gh- patterns substitute before the named-value rule consumes Bearer", () => {
    expect(redactSensitiveText("k=sk-abcdefghijklmnop1234")).toBe("k=<REDACTED_API_KEY>")
    expect(redactSensitiveText("t=ghp_aaaaaaaaaaaaaaaaaaaa")).toBe("t=<REDACTED_GITHUB_TOKEN>")
  })
})

describe("redactHead - shrink-shift safe cut", () => {
  test("drops a boundary partial sk- prefix that the bounded walk detects", () => {
    const secret = "sk-abcdefghijklmnop"
    const blob = `TOKEN=${secret.repeat(11)}    sk-xy`
    const out = redactHead(blob, 60)
    expect(out).not.toContain("sk-xy")
    expect(out).not.toContain(secret)
    expect(out.length).toBeLessThanOrEqual(60)
  })

  test("preserves non-secret content under the safe cut", () => {
    expect(redactHead("phase_5 completed normally", 60)).toBe("phase_5 completed normally")
  })
})

describe("summarizeText - whitespace collapse plus redaction plus suffix", () => {
  test("collapses newlines and redacts secrets inside a long message", () => {
    const secret = "sk-abcdefghijklmnop1234"
    const message = `clone\n--token "ghp_aaaaaaaaaaaaaaaaaaaa" ${secret} done`
    const out = summarizeText(message, 200)
    expect(out).not.toContain("\n")
    expect(out).not.toContain(secret)
    expect(out).not.toContain("ghp_aaaaaaaaaaaaaaaaaaaa")
    expect(out).toContain("done")
  })

  test("never exceeds the limit; appends ellipsis when truncated", () => {
    const out = summarizeText(`${"a".repeat(700)}tail`, 100)
    expect(out.length).toBeLessThanOrEqual(103)
    expect(out.endsWith("...")).toBe(true)
  })
})

describe("normalizeOptionalText - null-safe structural redaction", () => {
  test("null and undefined collapse to null; strings get redacted and bounded", () => {
    expect(normalizeOptionalText(null)).toBeNull()
    expect(normalizeOptionalText(undefined)).toBeNull()
    const out = normalizeOptionalText("phase_5 sk-abcdefghijklmnop1234")
    if (out === null) {
      throw new Error("expected a redacted string, got null")
    }
    expect(out).toContain("phase_5")
    expect(out).not.toContain("sk-abcdefghijklmnop1234")
  })
})
