import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ContainerExecutionBackend, LocalExecutionBackend, type BackendCommandResult } from '../src/execution-backend.js'
import { createContainerBashTool } from '../src/container-tool.js'

const success = (stdout = ''): BackendCommandResult => ({ exit_code: 0, stdout, stderr: '', duration: 0 })

test('local execution backend delegates command semantics', async () => {
  const backend = new LocalExecutionBackend()
  const result = await backend.run(['node', '-e', 'process.stdout.write("ok")'], { cwd: process.cwd(), signal: new AbortController().signal })
  assert.equal(result.exit_code, 0)
  assert.equal(result.stdout, 'ok')
})

test('container backend binds shared workspace and executes phases inside it', async () => {
  const calls: string[][] = []
  const backend = new ContainerExecutionBackend({ mode: 'container', source: 'image', image: 'fixture:latest', env_vars: { MODEL_PATH: '/models/model' }, cleanup: true }, '/tmp/workspace', async command => {
    const args = command as string[]
    calls.push(args)
    if (args[1] === 'create') return success('container-id\n')
    if (args[1] === 'start') return success('container-id\n')
    if (args[1] === 'exec') return success('inside\n')
    if (args[1] === 'rm') return success()
    throw new Error(`Unexpected runtime call: ${args.join(' ')}`)
  })
  const result = await backend.run('printf inside', { cwd: '/tmp/workspace/project', env: { FLAG: '1' }, signal: new AbortController().signal })
  assert.equal(result.stdout, 'inside\n')
  assert.ok(calls[0]!.includes('/tmp/workspace:/workspace'))
  assert.ok(calls[0]!.includes('MODEL_PATH=/models/model'))
  assert.ok(calls.some(args => args.includes('/workspace/project')))
  assert.ok(calls.some(args => args.includes('FLAG=1')))
  await backend.close(new AbortController().signal)
  assert.deepEqual(calls.at(-1), ['docker', 'rm', '-f', 'container-id'])
})

test('container backend rejects cwd outside the shared workspace', async () => {
  let calls = 0
  const backend = new ContainerExecutionBackend({ mode: 'container', image: 'fixture:latest' }, '/tmp/workspace', async command => {
    calls++
    const args = command as string[]
    if (args[1] === 'create') return success('container-id')
    return success()
  })
  await assert.rejects(backend.run('true', { cwd: '/tmp/elsewhere', signal: new AbortController().signal }), /outside the shared workspace/)
  assert.equal(calls, 3)
})

test('existing container must expose the exact shared workspace mount', async () => {
  const backend = new ContainerExecutionBackend({ mode: 'container', source: 'existing_container', container_name: 'shared' }, '/tmp/workspace', async command => {
    const args = command as string[]
    if (args[1] === 'inspect' && args[2] === '--format' && args[3] === '{{.Id}} {{.State.Running}}') return success('id true')
    if (args[1] === 'inspect') return success('/other:/workspace ')
    throw new Error(`Unexpected runtime call: ${args.join(' ')}`)
  })
  await assert.rejects(backend.run('true', { cwd: '/tmp/workspace', signal: new AbortController().signal }), /must bind \/tmp\/workspace:\/workspace/)
})

test('container backend validates required host environment before runtime use', () => {
  assert.throws(() => new ContainerExecutionBackend({ mode: 'container', image: 'fixture:latest', required_env_vars: ['SEAM_MISSING_TEST_ENV'] }, '/tmp/workspace', async () => success()), /Missing required host environment variables/)
})

test('scoped bash tool dispatches through the shared container backend', async () => {
  const calls: string[][] = []
  const backend = new ContainerExecutionBackend({ mode: 'container', image: 'fixture:latest' }, '/tmp/workspace', async command => {
    const args = command as string[]
    calls.push(args)
    if (args[1] === 'create') return success('container-id')
    if (args[1] === 'start') return success()
    if (args[1] === 'exec') return success('from-container')
    throw new Error(`Unexpected runtime call: ${args.join(' ')}`)
  })
  const tool = createContainerBashTool(backend, '/tmp/workspace')
  const output = await tool.execute({ command: 'pwd', description: 'Show current directory', workdir: 'subdir' }, {
    signal: new AbortController().signal,
    agent: { session: { header: { cwd: '/tmp/workspace' } } },
  } as never) as { exitCode: number | null; stdout: { text: string } }
  assert.equal(output.exitCode, 0)
  assert.equal(output.stdout.text, 'from-container')
  assert.ok(calls.some(args => args.includes('/workspace/subdir')))
})

test('container command cancellation attempts process-group cleanup inside the container', async () => {
  const calls: string[][] = []
  const cancellation = new AbortController()
  const backend = new ContainerExecutionBackend({ mode: 'container', image: 'fixture:latest' }, '/tmp/workspace', async command => {
    const args = command as string[]
    calls.push(args)
    if (args[1] === 'create') return success('container-id')
    if (args[1] === 'start') return success()
    if (args[1] === 'exec' && args.includes('--workdir')) {
      cancellation.abort(new Error('cancelled'))
      throw new Error('client cancelled')
    }
    return success()
  })
  await assert.rejects(backend.run('sleep 30', { cwd: '/tmp/workspace', signal: cancellation.signal }), /client cancelled/)
  assert.ok(calls.some(args => args[1] === 'exec' && args.at(-1)?.includes('kill -TERM')))
})
