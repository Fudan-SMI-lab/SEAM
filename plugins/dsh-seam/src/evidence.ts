import type { Data } from './types.js'

function mapping(value: unknown): value is Data { return !!value && typeof value === 'object' && !Array.isArray(value) }
function tail(value: string, limit: number): string { return value.length <= limit ? value : `[truncated to last ${limit} chars]\n${value.slice(-limit)}` }
function firstText(...values: unknown[]): string { return values.filter(value => value != null).map(String).find(value => value.trim()) ?? '' }
function sanitize(value: unknown, depth: number): unknown {
  if (depth < 0) return '...'
  if (mapping(value)) return Object.fromEntries(Object.entries(value).filter(([key]) => !['stdout', 'stderr', 'script_stdout', 'script_stderr', 'raw_response'].includes(key)).map(([key, child]) => [key, sanitize(child, depth - 1)]))
  if (Array.isArray(value)) return value.slice(0, 5).map(child => sanitize(child, depth - 1))
  return typeof value === 'string' ? tail(value, 500) : value
}

export function failureEvidence(data: Data): string {
  const nested = mapping(data.run_entry_script) ? data.run_entry_script : {}
  const command = firstText(data.script_command, nested.command)
  const exit = data.script_exit_code ?? nested.exit_code ?? data.exit_code
  const duration = data.script_duration ?? nested.duration ?? data.duration
  const stderr = firstText(data.script_stderr, nested.script_stderr, nested.stderr)
  const stdout = firstText(data.script_stdout, nested.script_stdout, nested.stdout)
  const error = firstText(data.last_error, data.error)
  const label = stderr ? 'stderr tail' : stdout ? 'stdout tail' : error ? 'last error' : 'no captured output'
  const summary = Object.fromEntries(Object.entries(data).filter(([key, value]) => !['script_stdout', 'script_stderr', 'script_command', 'run_entry_script', 'last_error'].includes(key) && mapping(value) && ['errors', 'summary', 'passed', 'path', 'artifact_path', 'report_path', 'status'].some(field => Object.hasOwn(value, field))).map(([key, value]) => [key, sanitize(value, 2)]))
  const lines = ['## Failure Evidence', '', '### Command Metadata', `- Command: ${command || '(not available)'}`, `- Exit Code: ${exit ?? '(not available)'}`, `- Duration Seconds: ${duration ?? '(not available)'}`, '', `### Output Evidence (${label})`, '```', stderr || stdout || error ? tail(stderr || stdout || error, 4500) : '(No stderr/stdout output captured.)', '```']
  if (Object.keys(summary).length) lines.push('', '### Result Metadata', '```json', tail(JSON.stringify(summary, null, 2), 1500), '```')
  return tail(lines.join('\n').trim(), 8000)
}
