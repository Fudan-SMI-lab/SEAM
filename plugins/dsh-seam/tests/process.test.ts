import assert from 'node:assert/strict'
import { test } from 'node:test'
import { runProcess, runShell } from '../src/process.js'

test('sidecar preserves split UTF-8, stdin JSON and stderr separation', async () => {
  const output = await runProcess(process.execPath, ['-e', `process.stdin.resume();process.stdin.on('end',()=>{const bytes=Buffer.from('算子');process.stdout.write(bytes.subarray(0,1));setTimeout(()=>{process.stderr.write('diagnostic');process.stdout.write(bytes.subarray(1))},10)})`], { cwd: process.cwd(), input: {}, signal: new AbortController().signal })
  assert.equal(output, '算子')
})

test('sidecar enforces combined output limit', { timeout: 5000 }, async () => {
  await assert.rejects(runProcess(process.execPath, ['-e', `process.stdin.resume();process.stdout.write('x'.repeat(4096));setInterval(()=>{},1000)`], { cwd: process.cwd(), input: {}, signal: new AbortController().signal, maxBytes: 100 }), /byte limit/)
})

test('sidecar cancels a TERM-resistant descendant before returning', { timeout: 7000, skip: process.platform === 'win32' }, async () => {
  const controller = new AbortController()
  const code = `const {spawn}=require('node:child_process');process.stdin.resume();spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:'inherit'});setInterval(()=>{},1000)`
  const result = runProcess(process.execPath, ['-e', code], { cwd: process.cwd(), input: {}, signal: controller.signal })
  setTimeout(() => controller.abort(new Error('cancel fixture')), 300)
  await assert.rejects(result, /cancel fixture/)
})

test('sidecar rejects nonzero exits and already cancelled requests', async () => {
  await assert.rejects(runProcess(process.execPath, ['-e', `process.stderr.write('fixture failure');process.exit(7)`], { cwd: process.cwd(), input: {}, signal: new AbortController().signal }), /fixture failure/)
  await assert.rejects(runProcess('does-not-exist', [], { cwd: process.cwd(), input: {}, signal: AbortSignal.abort(new Error('already stopped')) }), /already stopped/)
})

test('shell phase captures output, environment and nonzero exit without collapsing diagnostics', async () => {
  const result = await runShell('printf "$SEAM_FIXTURE"; printf diagnostic >&2; exit 7', { cwd: process.cwd(), env: { SEAM_FIXTURE: 'stdout' }, signal: new AbortController().signal })
  assert.equal(result.exit_code, 7)
  assert.equal(result.stdout, 'stdout')
  assert.equal(result.stderr, 'diagnostic')
})

test('shell phase decodes UTF-8 split across process chunks', async () => {
  const result = await runShell("printf '\\xe7'; sleep 0.02; printf '\\xae\\x97\\xe5\\xad\\x90'", { cwd: process.cwd(), signal: new AbortController().signal })
  assert.equal(result.stdout, '算子')
})

test('shell phase enforces cancellation and drains descendants', { timeout: 7000, skip: process.platform === 'win32' }, async () => {
  const controller = new AbortController()
  const result = runShell('sleep 30 & wait', { cwd: process.cwd(), signal: controller.signal })
  setTimeout(() => controller.abort(new Error('shell cancelled')), 250)
  await assert.rejects(result, /shell cancelled/)
})
