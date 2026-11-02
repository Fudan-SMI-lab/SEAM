import { execFile, spawn } from 'node:child_process'
import { promisify } from 'node:util'
import { StringDecoder } from 'node:string_decoder'

const executeFile = promisify(execFile)

export async function runShell(command: string | string[], options: { cwd: string; env?: Record<string, string>; signal: AbortSignal; timeoutMs?: number; maxBytes?: number }): Promise<{ exit_code: number | null; stdout: string; stderr: string; duration: number }> {
  options.signal.throwIfAborted()
  const started = performance.now()
  const child = Array.isArray(command)
    ? spawn(command[0]!, command.slice(1), { cwd: options.cwd, env: { ...process.env, ...options.env }, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] })
    : spawn(command, { cwd: options.cwd, env: { ...process.env, ...options.env }, shell: true, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] })
  let stdout = ''
  let stderr = ''
  const decoders = { stdout: new StringDecoder('utf8'), stderr: new StringDecoder('utf8') }
  let bytes = 0
  let failure: Error | undefined
  let stopping: Promise<void> | undefined
  const stop = (): Promise<void> => stopping ??= (async () => {
    if (!child.pid) return
    if (process.platform === 'win32') {
      await executeFile('taskkill', ['/pid', String(child.pid), '/T', '/F']).catch(() => {})
      return
    }
    try { process.kill(-child.pid, 'SIGTERM') } catch {}
    await new Promise(resolve => setTimeout(resolve, 300))
    try { process.kill(-child.pid, 'SIGKILL') } catch {}
  })()
  const onAbort = (): void => { failure = options.signal.reason instanceof Error ? options.signal.reason : new Error('Shell phase cancelled'); void stop() }
  const timer = options.timeoutMs === undefined ? undefined : setTimeout(() => { failure = new Error('Shell phase timed out'); void stop() }, options.timeoutMs)
  const collect = (target: 'stdout' | 'stderr', chunk: Buffer): void => {
    bytes += chunk.length
    if (bytes > (options.maxBytes ?? 8 * 1024 * 1024)) { failure = new Error('Shell output exceeds byte limit'); void stop(); return }
    if (target === 'stdout') stdout += decoders.stdout.write(chunk)
    else stderr += decoders.stderr.write(chunk)
  }
  child.stdout.on('data', (chunk: Buffer) => collect('stdout', chunk))
  child.stderr.on('data', (chunk: Buffer) => collect('stderr', chunk))
  options.signal.addEventListener('abort', onAbort, { once: true })
  if (options.signal.aborted) onAbort()
  try {
    const exit_code = await new Promise<number | null>((resolve, reject) => {
      child.once('error', reject)
      child.once('close', (code, signal) => {
        stdout += decoders.stdout.end()
        stderr += decoders.stderr.end()
        failure ? reject(failure) : resolve(code ?? (signal ? 128 : null))
      })
    })
    return { exit_code, stdout, stderr, duration: (performance.now() - started) / 1000 }
  } finally {
    if (timer) clearTimeout(timer)
    options.signal.removeEventListener('abort', onAbort)
    await stopping
  }
}

export async function runProcess(command: string, args: string[], options: { cwd: string; input: unknown; signal: AbortSignal; maxBytes?: number }): Promise<string> {
  options.signal.throwIfAborted()
  const child = spawn(command, args, { cwd: options.cwd, detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'] })
  let output = ''
  let errors = ''
  const decoders = { stdout: new StringDecoder('utf8'), stderr: new StringDecoder('utf8') }
  let size = 0
  let failure: Error | undefined
  let stopping: Promise<void> | undefined
  const stop = (): Promise<void> => stopping ??= (async () => {
    if (!child.pid) return
    const pids = new Set([child.pid])
    if (process.platform === 'win32') {
      await executeFile('taskkill', ['/pid', String(child.pid), '/T', '/F']).catch(() => {})
      return
    }
    const table = await executeFile('ps', ['-eo', 'pid=,ppid=,lstart=']).catch(() => ({ stdout: '' }))
    const rows = table.stdout.trim().split('\n').map(line => {
      const [pid, parent, ...started] = line.trim().split(/\s+/)
      return { pid: Number(pid), parent: Number(parent), started: started.join(' ') }
    })
    let previous = 0
    while (previous !== pids.size) {
      previous = pids.size
      for (const { pid, parent } of rows) if (pid && parent && pids.has(parent)) pids.add(pid)
    }
    for (const pid of [...pids].reverse()) { try { process.kill(pid, 'SIGTERM') } catch {} }
    try { process.kill(-child.pid, 'SIGTERM') } catch {}
    await new Promise(resolve => setTimeout(resolve, 300))
    const current = await executeFile('ps', ['-eo', 'pid=,ppid=,lstart=']).catch(() => ({ stdout: '' }))
    const alive = new Set(current.stdout.trim().split('\n').map(line => {
      const [pid, , ...started] = line.trim().split(/\s+/)
      return `${pid} ${started.join(' ')}`
    }))
    for (const row of rows.reverse()) {
      if (pids.has(row.pid) && alive.has(`${row.pid} ${row.started}`)) { try { process.kill(row.pid, 'SIGKILL') } catch {} }
    }
  })()
  const onAbort = (): void => { failure = options.signal.reason instanceof Error ? options.signal.reason : new Error('Process cancelled'); void stop() }
  const capture = (stream: 'stdout' | 'stderr', chunk: Buffer): void => {
    size += chunk.length
    if (size > (options.maxBytes ?? 8 * 1024 * 1024)) {
      failure = new Error('Bridge output exceeds byte limit')
      void stop()
      return
    }
    if (stream === 'stdout') output += decoders.stdout.write(chunk)
    else errors += decoders.stderr.write(chunk)
  }
  child.stdout.on('data', chunk => capture('stdout', chunk))
  child.stderr.on('data', chunk => capture('stderr', chunk))
  child.stdin.on('error', error => { failure ??= error; void stop() })
  options.signal.addEventListener('abort', onAbort, { once: true })
  if (options.signal.aborted) onAbort()
  try {
    await new Promise<void>((resolve, reject) => {
      child.once('error', reject)
      child.once('close', (code, signal) => {
        output += decoders.stdout.end()
        errors += decoders.stderr.end()
        if (failure) reject(failure)
        else if (code !== 0) reject(new Error(`Bridge exited ${code ?? signal}: ${errors}`))
        else resolve()
      })
      child.stdin.end(JSON.stringify(options.input))
    })
    return output
  } finally {
    options.signal.removeEventListener('abort', onAbort)
    await stopping
  }
}
