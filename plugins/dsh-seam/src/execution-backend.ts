import { existsSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { runShell } from './process.js'

export interface ContainerBackendConfig {
  mode: 'container'
  source?: 'image' | 'existing_container'
  runtime?: 'docker' | 'podman'
  image?: string
  images?: string[]
  container_name?: string
  container_name_prefix?: string
  devices?: string[]
  volumes?: string[]
  env_vars?: Record<string, string>
  required_env_vars?: string[]
  required_devices?: string[]
  container_workdir?: string
  network_mode?: string
  runtime_flags?: string[]
  timeout?: number
  cleanup?: boolean
}

export interface BackendCommandResult {
  exit_code: number | null
  stdout: string
  stderr: string
  duration: number
}

export interface ExecutionBackend {
  run(command: string | string[], options: { cwd: string; env?: Record<string, string>; signal: AbortSignal; timeoutMs?: number }): Promise<BackendCommandResult>
  close(signal: AbortSignal): Promise<void>
}

export class LocalExecutionBackend implements ExecutionBackend {
  async run(command: string | string[], options: { cwd: string; env?: Record<string, string>; signal: AbortSignal; timeoutMs?: number }): Promise<BackendCommandResult> {
    return runShell(command, options)
  }
  async close(): Promise<void> {}
}

type Runner = (command: string | string[], options: { cwd: string; env?: Record<string, string>; signal: AbortSignal; timeoutMs?: number }) => Promise<BackendCommandResult>

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`execution_backend.${field} must be a non-empty string`)
  return value
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`
}

export class ContainerExecutionBackend implements ExecutionBackend {
  private containerId: string | undefined
  private created = false
  private initialized: Promise<string> | undefined
  private readonly runtime: string
  private readonly containerWorkdir: string
  private readonly runner: Runner

  constructor(private readonly config: ContainerBackendConfig, private readonly workspace: string, runner: Runner = runShell) {
    this.runner = runner
    this.runtime = config.runtime ?? 'docker'
    if (!['docker', 'podman'].includes(this.runtime)) throw new Error(`Unsupported container runtime: ${this.runtime}`)
    this.containerWorkdir = config.container_workdir ?? '/workspace'
    if (!isAbsolute(this.containerWorkdir)) throw new Error('execution_backend.container_workdir must be absolute')
    this.validateConfig()
  }

  private validateConfig(): void {
    const source = this.config.source ?? 'image'
    if (!['image', 'existing_container'].includes(source)) throw new Error(`Unsupported execution_backend.source: ${source}`)
    if (source === 'existing_container') requiredString(this.config.container_name, 'container_name')
    if (source === 'image' && !(this.config.images?.length || this.config.image)) throw new Error('execution_backend image source requires image or images')
    for (const [key, value] of Object.entries(this.config.env_vars ?? {})) {
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || typeof value !== 'string') throw new Error(`Invalid execution_backend.env_vars entry: ${key}`)
    }
    const missingEnv = (this.config.required_env_vars ?? []).filter(name => !process.env[name])
    if (missingEnv.length) throw new Error(`Missing required host environment variables: ${missingEnv.join(', ')}`)
    const missingDevices = (this.config.required_devices ?? []).filter(device => !existsSync(device))
    if (missingDevices.length) throw new Error(`Missing required device paths: ${missingDevices.join(', ')}`)
  }

  private command(args: string[], signal: AbortSignal): Promise<BackendCommandResult> {
    return this.runner([this.runtime, ...args], { cwd: this.workspace, signal, timeoutMs: 120_000 })
  }

  private async ensure(signal: AbortSignal): Promise<string> {
    if (this.containerId) return this.containerId
    this.initialized ??= this.createOrAttach(signal).catch(error => { this.initialized = undefined; throw error })
    return this.initialized
  }

  private async createOrAttach(signal: AbortSignal): Promise<string> {
    signal.throwIfAborted()
    const source = this.config.source ?? 'image'
    if (source === 'existing_container') {
      const name = requiredString(this.config.container_name, 'container_name')
      const inspected = await this.command(['inspect', '--format', '{{.Id}} {{.State.Running}}', name], signal)
      if (inspected.exit_code !== 0) throw new Error(`Existing container is unavailable: ${inspected.stderr.trim()}`)
      const [id, running] = inspected.stdout.trim().split(/\s+/)
      if (!id || running !== 'true') throw new Error(`Existing container ${name} is not running`)
      const mounts = await this.command(['inspect', '--format', '{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}', id], signal)
      const expected = `${this.workspace}:${this.containerWorkdir}`
      if (mounts.exit_code !== 0 || !mounts.stdout.split(/\s+/).includes(expected)) throw new Error(`Existing container must bind ${expected}`)
      await this.verifyExecutionSupport(id, signal)
      this.containerId = id
      return id
    }
    const images = [...(this.config.images ?? (this.config.image ? [this.config.image] : []))].filter(Boolean)
    const failures: string[] = []
    for (const image of images) {
      signal.throwIfAborted()
      const args = ['create', '--workdir', this.containerWorkdir, '--volume', `${this.workspace}:${this.containerWorkdir}`]
      if (this.config.network_mode) args.push('--network', this.config.network_mode)
      for (const [key, value] of Object.entries(this.config.env_vars ?? {})) args.push('--env', `${key}=${value}`)
      for (const device of this.config.devices ?? []) args.push('--device', device)
      args.push(...(this.config.volumes ?? []).flatMap(volume => ['--volume', volume]))
      args.push(...(this.config.runtime_flags ?? []))
      const name = `${this.config.container_name_prefix ?? 'seam'}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
      args.push('--name', name, image, 'sh', '-lc', 'while :; do sleep 3600; done')
      const created = await this.command(args, signal)
      if (created.exit_code !== 0 || !created.stdout.trim()) { failures.push(`${image}: ${created.stderr.trim()}`); continue }
      const id = created.stdout.trim().split(/\s+/)[0]!
      this.created = true
      const started = await this.command(['start', id], signal)
      if (started.exit_code !== 0) {
        await this.command(['rm', '-f', id], new AbortController().signal).catch(() => undefined)
        this.created = false
        failures.push(`${image}: ${started.stderr.trim()}`)
        continue
      }
      try { await this.verifyExecutionSupport(id, signal) }
      catch (error) {
        await this.command(['rm', '-f', id], new AbortController().signal).catch(() => undefined)
        this.created = false
        failures.push(`${image}: ${error instanceof Error ? error.message : String(error)}`)
        continue
      }
      this.containerId = id
      return id
    }
    throw new Error(`Unable to create an execution container: ${failures.join('; ')}`)
  }

  private async verifyExecutionSupport(id: string, signal: AbortSignal): Promise<void> {
    const result = await this.command(['exec', id, 'sh', '-lc', 'command -v setsid >/dev/null'], signal)
    if (result.exit_code !== 0) throw new Error('Container image must provide sh and setsid for cancellable command execution')
  }

  private containerPath(hostPath: string): string {
    const path = resolve(hostPath)
    const pathRelative = relative(this.workspace, path)
    if (pathRelative === '..' || pathRelative.startsWith(`..${sep}`) || isAbsolute(pathRelative)) throw new Error(`Execution path is outside the shared workspace: ${hostPath}`)
    return pathRelative ? `${this.containerWorkdir}/${pathRelative.split(sep).join('/')}` : this.containerWorkdir
  }

  async run(command: string | string[], options: { cwd: string; env?: Record<string, string>; signal: AbortSignal; timeoutMs?: number }): Promise<BackendCommandResult> {
    const id = await this.ensure(options.signal)
    const cwd = this.containerPath(options.cwd)
    const envArgs = Object.entries(options.env ?? {}).flatMap(([key, value]) => ['--env', `${key}=${value}`])
    const script = Array.isArray(command) ? command.map(shellQuote).join(' ') : command
    const operationId = randomUUID()
    const pidFile = `/tmp/seam-exec-${operationId}.pid`
    const trackedScript = `pid_file=${shellQuote(pidFile)}; setsid sh -lc ${shellQuote(script)} & command_pid=$!; printf '%s' "$command_pid" > "$pid_file"; wait "$command_pid"; command_status=$?; rm -f "$pid_file"; exit "$command_status"`
    try {
      return await this.runner([this.runtime, 'exec', ...envArgs, '--workdir', cwd, id, 'sh', '-lc', trackedScript], {
        cwd: this.workspace,
        signal: options.signal,
        timeoutMs: options.timeoutMs ?? (this.config.timeout ?? 7200) * 1000,
      })
    } finally {
      const cleanupScript = `if test -r ${shellQuote(pidFile)}; then command_pid=$(cat ${shellQuote(pidFile)}); kill -TERM -- -"$command_pid" 2>/dev/null || true; sleep 0.3; kill -KILL -- -"$command_pid" 2>/dev/null || true; rm -f ${shellQuote(pidFile)}; fi`
      await this.runner([this.runtime, 'exec', id, 'sh', '-lc', cleanupScript], { cwd: this.workspace, signal: new AbortController().signal, timeoutMs: 5000 }).catch(() => undefined)
    }
  }

  async close(_signal: AbortSignal): Promise<void> {
    if (!this.containerId || !this.created || this.config.cleanup === false) return
    const id = this.containerId
    this.containerId = undefined
    this.created = false
    const result = await this.command(['rm', '-f', id], new AbortController().signal)
    if (result.exit_code !== 0) throw new Error(`Failed to remove owned container ${id}: ${result.stderr.trim()}`)
  }
}
