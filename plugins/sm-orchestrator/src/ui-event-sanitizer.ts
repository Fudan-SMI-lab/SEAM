// Text-level redaction and safe-cut primitives.
// Direct port of src/core/secret_redaction.py: concrete secret patterns run
// first, then the named-value collapse runs last so a free-text
// "Authorization: Bearer X" construction cannot leak the token.

const SENSITIVE_KEY_PARTS: ReadonlySet<string> = new Set([
  "api-key", "apikey", "auth", "authentication", "authorization", "credential",
  "credentials", "password", "passwd", "secret", "secrets", "token", "tokens",
])
const SENSITIVE_COMPOUND_PARTS: ReadonlySet<string> = new Set([
  "accesstoken", "apikey", "clientsecret",
])
const STRUCTURAL_SUFFIXES: ReadonlySet<string> = new Set([
  "complete", "count", "enabled", "id", "length", "requested", "status", "type",
])

const NAME_SEPARATOR = /[^A-Za-z0-9]+/
const CAMEL_BOUNDARY = /(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/
const BEARER_VALUE = /(Bearer)\s+[A-Za-z0-9._~+/=-]+/gi
const API_KEY_VALUE = /\bsk-[A-Za-z0-9_-]{16,}/g
const GITHUB_TOKEN_VALUE = /\bgh[pousr]_[A-Za-z0-9_]{20,}/g
const QUOTED_CLI_VALUE = /((?:--|\/)[A-Za-z0-9_-]+)(\s*(?:=|:)\s*|\s+)(["'])(.*?)\3/gi
const PLAIN_CLI_VALUE = /((?:--|\/)[A-Za-z0-9_-]+)(\s*(?:=|:)\s*|\s+)([^\s,;]+)/gi
const QUOTED_NAMED_VALUE = /(["']?[A-Za-z_][A-Za-z0-9_-]*["']?)(\s*[:=]\s*)(["'])(.*?)\3/g
const PLAIN_NAMED_VALUE = /\b([A-Za-z_][A-Za-z0-9_-]*)(\s*[:=]\s*)([^\s'"`,;]+)/g
const SECRET_PREFIX_GUARD = /(?:sk-[A-Za-z0-9_-]{1,15}|gh[pousr]_[A-Za-z0-9_]{1,19})$/

export const REDACTED = "<REDACTED>"
export const TEXT_LIMIT = 500
const REDACT_MARGIN = 64
const BOUNDED_WALK = 24

export type JsonScalar = string | number | boolean | null
export type JsonValue = JsonScalar | readonly JsonValue[] | { readonly [key: string]: JsonValue }

export function isSensitiveName(name: string): boolean {
  const stripped = name.replace(/^["']+|["']+$/g, "")
  const segments = stripped
    .replace(/^[-/]+/, "")
    .split(NAME_SEPARATOR)
    .filter((segment) => segment.length > 0)
  const raw = segments.map((segment) => segment.toLowerCase())
  const expanded = segments.flatMap((segment) =>
    segment
      .split(CAMEL_BOUNDARY)
      .filter((part) => part.length > 0)
      .map((part) => part.toLowerCase()),
  )
  if (raw.length === 0) {
    return false
  }
  const suffix = expanded.length > 0 ? expanded[expanded.length - 1] : raw[raw.length - 1]
  if (STRUCTURAL_SUFFIXES.has(suffix)) {
    return false
  }
  const candidates = new Set([...raw, ...expanded])
  for (const part of SENSITIVE_KEY_PARTS) {
    if (candidates.has(part)) {
      return true
    }
  }
  for (const part of SENSITIVE_COMPOUND_PARTS) {
    if (candidates.has(part)) {
      return true
    }
  }
  for (let index = 0; index + 1 < expanded.length; index += 1) {
    if (expanded[index] === "api" && expanded[index + 1] === "key") {
      return true
    }
  }
  return false
}

export function redactSensitiveText(text: string): string {
  const afterBearer = text.replace(BEARER_VALUE, (_m, scheme: string) => `${scheme} <REDACTED>`)
  const afterApiKey = afterBearer.replace(API_KEY_VALUE, "<REDACTED_API_KEY>")
  const afterGithub = afterApiKey.replace(GITHUB_TOKEN_VALUE, "<REDACTED_GITHUB_TOKEN>")
  const redactQuoted = (match: string, option: string, separator: string, quote: string): string =>
    isSensitiveName(option) ? `${option}${separator}${quote}${REDACTED}${quote}` : match
  const redactPlain = (match: string, option: string, separator: string): string =>
    isSensitiveName(option) ? `${option}${separator}${REDACTED}` : match
  const afterQuotedCli = afterGithub.replace(QUOTED_CLI_VALUE, redactQuoted)
  const afterPlainCli = afterQuotedCli.replace(PLAIN_CLI_VALUE, redactPlain)
  const afterQuotedNamed = afterPlainCli.replace(QUOTED_NAMED_VALUE, redactQuoted)
  return afterQuotedNamed.replace(
    PLAIN_NAMED_VALUE,
    (match, name: string, separator: string, value: string) =>
      isSensitiveName(name)
        ? `${name}${separator}${REDACTED}`
        : `${name}${separator}${redactSensitiveText(value)}`,
  )
}

function danglingQuoteCut(text: string): number {
  let cut = text.length
  for (const quote of ['"', "'"]) {
    let count = 0
    for (const ch of text) {
      if (ch === quote) {
        count += 1
      }
    }
    if (count % 2 === 1) {
      const idx = text.lastIndexOf(quote)
      if (idx < cut) {
        cut = idx
      }
    }
  }
  return cut
}

export function inputSafeWindow(value: string, limit: number): string {
  let cut = Math.min(value.length, limit + REDACT_MARGIN)
  for (let step = 0; step < BOUNDED_WALK; step += 1) {
    const window = value.slice(0, cut)
    const quoteCut = danglingQuoteCut(window)
    if (quoteCut < cut) {
      cut = quoteCut
      continue
    }
    const guard = SECRET_PREFIX_GUARD.exec(window)
    if (guard) {
      cut = guard.index
      continue
    }
    return window
  }
  return value.slice(0, cut)
}

export function boundedText(text: string, limit: number = TEXT_LIMIT): string {
  return text.length <= limit ? text : text.slice(0, limit)
}

export function redactHead(value: string, limit: number = TEXT_LIMIT): string {
  if (value.length <= limit) {
    return redactSensitiveText(value)
  }
  return boundedText(redactSensitiveText(inputSafeWindow(value, limit)), limit)
}

export function summarizeText(value: unknown, limit: number): string {
  let raw = String(value ?? "").replace(/\s+/g, " ").trim()
  raw = redactSensitiveText(raw.length > limit ? inputSafeWindow(raw, limit) : raw)
  if (raw.length <= limit) {
    return raw
  }
  return `${raw.slice(0, limit).trimEnd()}...`
}

export function normalizeOptionalText(value: string | null | undefined): string | null {
  return value === null || value === undefined ? null : redactHead(value)
}

export function toBoundedString(value: unknown): string {
  let rendered: string
  try {
    rendered = String(value)
  } catch {
    rendered = "[Unserializable]"
  }
  return boundedText(redactSensitiveText(rendered))
}
