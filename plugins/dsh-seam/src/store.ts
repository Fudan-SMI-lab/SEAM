import { mkdir, open, readFile, rename, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import type { RunSnapshot } from './types.js'

export class RunStore {
  private sequence = 0
  private locked = false
  readonly directory: string
  constructor(root: string, readonly runId: string) {
    if (!/^[a-zA-Z0-9_-]{1,100}$/.test(runId)) throw new Error('Invalid run id')
    this.directory = join(root, runId)
  }
  async acquire(): Promise<void> {
    await mkdir(this.directory, { recursive: true })
    await mkdir(join(this.directory, 'lock'))
    this.locked = true
    try {
      const handle = await open(join(this.directory, 'created'), 'wx')
      await handle.close()
    } catch (error) { await this.release(); throw error }
  }
  async release(): Promise<void> {
    if (!this.locked) return
    await rm(join(this.directory, 'lock'), { recursive: true })
    this.locked = false
  }
  async snapshot(snapshot: RunSnapshot): Promise<void> {
    const temporary = join(this.directory, `.state-${randomUUID()}.tmp`)
    const handle = await open(temporary, 'wx', 0o600)
    try { await handle.writeFile(JSON.stringify(snapshot, null, 2)); await handle.sync() }
    finally { await handle.close() }
    await rename(temporary, join(this.directory, 'state.json'))
  }
  async artifact(phase: string, value: unknown): Promise<string> {
    const filename = `${String(++this.sequence).padStart(6, '0')}-${phase.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`
    const handle = await open(join(this.directory, filename), 'wx', 0o600)
    try { await handle.writeFile(JSON.stringify(value, null, 2)); await handle.sync() }
    finally { await handle.close() }
    return join(this.directory, filename)
  }
  async read(): Promise<RunSnapshot> {
    const snapshot: RunSnapshot = JSON.parse(await readFile(join(this.directory, 'state.json'), 'utf8'))
    if (snapshot.format !== 1 || snapshot.runId !== this.runId) throw new Error('Invalid SEAM snapshot')
    return snapshot
  }
}
