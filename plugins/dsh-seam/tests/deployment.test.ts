import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { parseDocument } from 'yaml'

test('deployment overlay enables native workflow and role tools without enabling model orchestration', async () => {
  const source = await readFile(new URL('../examples/seam.patch.yml', import.meta.url), 'utf8')
  const document = parseDocument(source, { customTags: [{ tag: 'tag:yaml.org,2002:js', resolve: (value: string) => value }] })
  assert.deepEqual(document.errors, [])
  assert.deepEqual(document.warnings, [])
  const patches = document.toJS() as { id?: string; disabled?: boolean; insert?: { id: string; config: { roleTools: Record<string, string[]> } }[] }[]
  for (const id of ['workflow-worker-thread', 'tool-bash', 'tool-fs', 'tool-fs-search']) {
    assert.equal(patches.find(patch => patch.id === id)?.disabled, false, id)
  }
  assert.ok(!patches.some(patch => patch.id === 'tool-workflow' && patch.disabled === false))
  const plugin = patches.flatMap(patch => patch.insert ?? []).find(patch => patch.id === 'seam-workflow')
  assert.ok(plugin)
  for (const tools of Object.values(plugin.config.roleTools)) assert.ok(!tools.includes('seam_workflow'))
})
