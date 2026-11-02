import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { RunStore } from '../src/store.js'
import type { RunSnapshot } from '../src/types.js'

test('store locks runs, versions evidence and rejects re-execution', async context => {
  const root = await mkdtemp(join(tmpdir(), 'seam-store-'))
  context.after(() => rm(root, { recursive: true, force: true }))
  const store = new RunStore(root, 'fixture')
  await store.acquire()
  await assert.rejects(new RunStore(root, 'fixture').acquire(), /EEXIST/)
  const first = await store.artifact('phase/1', { value: 1 })
  const second = await store.artifact('phase/1', { value: 2 })
  assert.notEqual(first, second)
  assert.equal(JSON.parse(await readFile(first, 'utf8')).value, 1)
  const snapshot: RunSnapshot = { format: 1, runId: 'fixture', workflow: 'test', workflowHash: 'hash', workspace: root, status: 'complete', state: {}, context: {}, sessions: { engineer: 'saved-chat' } }
  await store.snapshot(snapshot)
  assert.deepEqual(await store.read(), snapshot)
  assert.equal((await readdir(store.directory)).some(file => file.endsWith('.tmp')), false)
  await store.release()
  await assert.rejects(new RunStore(root, 'fixture').acquire(), /EEXIST/)
  assert.throws(() => new RunStore(root, '../unsafe'), /Invalid run id/)
})
