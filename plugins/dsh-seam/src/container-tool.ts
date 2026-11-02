import { resolve as resolvePath } from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContainerExecutionBackend } from './execution-backend.js'

interface BashArguments {
  command: string
  description: string
  timeoutMs?: number
  workdir?: string
}

export function createContainerBashTool(backend: ContainerExecutionBackend, workspace: string) {
  return defineTool({
    name: 'bash',
    description: 'Execute a shell command in the SEAM task container. Files are shared with the task workspace. Check the exit code before relying on results.',
    parameters: {
      command: { type: 'string', required: true, description: 'The command to execute.' },
      description: { type: 'string', required: true, description: 'A short description of the command.' },
      timeoutMs: { type: 'number', description: 'Optional execution timeout in milliseconds.' },
      workdir: { type: 'string', description: 'Optional working directory within the task workspace.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          kind: { type: 'string', required: true, const: 'foreground' },
          exitCode: { required: true, oneOf: [{ type: 'integer' }, { type: 'null' }] },
          signal: { required: true, oneOf: [{ type: 'string' }, { type: 'null' }] },
          timedOut: { type: 'boolean', required: true },
          aborted: { type: 'boolean', required: true },
          timeoutMs: { type: 'number', required: true },
          stdout: { type: 'object', additionalProperties: false, properties: { text: { type: 'string', required: true }, truncated: { type: 'boolean', required: true } } },
          stderr: { type: 'object', additionalProperties: false, properties: { text: { type: 'string', required: true }, truncated: { type: 'boolean', required: true } } },
        },
      },
      render: (_args, raw) => {
        const value = raw as { exitCode: number | null; stdout: { text: string }; stderr: { text: string } }
        return [{ type: 'text', text: `Exit code: ${value.exitCode}\n${value.stdout.text}${value.stderr.text ? `\nSTDERR:\n${value.stderr.text}` : ''}` }]
      },
    },
    async execute(args: BashArguments, exec) {
      if (!args.command.trim() || !args.description.trim()) throw new Error('command and description must be non-empty')
      if (args.timeoutMs !== undefined && (!Number.isFinite(args.timeoutMs) || args.timeoutMs <= 0)) throw new Error('timeoutMs must be a positive finite number')
      const base = exec.agent?.session.header.cwd ?? workspace
      const cwd = args.workdir ? resolvePath(base, args.workdir) : base
      const result = await backend.run(args.command, { cwd, signal: exec.signal, timeoutMs: args.timeoutMs })
      const maxOutputBytes = 64 * 1024
      const bounded = (value: string) => {
        const buffer = Buffer.from(value)
        return { text: buffer.length > maxOutputBytes ? buffer.subarray(0, maxOutputBytes).toString('utf8') : value, truncated: buffer.length > maxOutputBytes }
      }
      return {
        kind: 'foreground' as const,
        exitCode: result.exit_code,
        signal: null,
        timedOut: false,
        aborted: false,
        timeoutMs: args.timeoutMs ?? 120_000,
        stdout: bounded(result.stdout),
        stderr: bounded(result.stderr),
      }
    },
  })
}
