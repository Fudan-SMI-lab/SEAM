import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { parse } from 'yaml'
import { compilerVersion, generateWorkflow, type CompiledWorkflow } from './workflow-compiler.js'
import { checkOperatorWorkflow } from './preflight.js'
import type { Workflow } from './types.js'

const runtimeVersion = 'seam-stage-runtime/1'
const digest = (value: string): string => createHash('sha256').update(value).digest('hex')
const encode = (value: unknown): string => `${JSON.stringify(value, null, 2)}\n`
interface PackageManifest {
  format: 1
  id: string
  compilerVersion: string
  runtimeVersion: string
  workflowName: string
  files: Record<string, string>
  bindings: Record<string, string>
}
export interface WorkflowPackage extends CompiledWorkflow {
  id: string
  directory: string
  bindings: Record<string, string>
}

async function repositoryBindings(repository: string): Promise<Record<string, string>> {
  const bindings: Record<string, string> = {}
  const isDirectory = async (path: string) => {
    try { return (await stat(path)).isDirectory() }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false; throw error }
  }
  const localSkills = !await isDirectory(join(repository, '.skills')) && await isDirectory(join(repository, '../.skills')) ? '../.skills' : '.skills'
  for (const root of ['src/prompts', 'src/validators', 'src/core', '.memory/skills', localSkills, 'plugins/dsh-seam/python']) {
    const visit = async (relative: string): Promise<void> => {
      let entries
      try { entries = await readdir(join(repository, relative), { withFileTypes: true }) }
      catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error }
      for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
        if (entry.name === '__pycache__' || entry.name.startsWith('.')) continue
        const path = `${relative}/${entry.name}`
        if (entry.isDirectory()) await visit(path)
        else if (entry.isFile() && /\.(md|py|json)$/.test(entry.name)) bindings[path] = digest(await readFile(join(repository, path), 'utf8'))
      }
    }
    await visit(root)
  }
  for (const filename of ['plugins/dsh-seam/package.json', 'plugins/dsh-seam/dist/phase-executor.js', 'plugins/dsh-seam/dist/dsh-runtime.js', 'plugins/dsh-seam/dist/bridge.js', 'plugins/dsh-seam/dist/contracts.js']) {
    try { bindings[filename] = digest(await readFile(join(repository, filename), 'utf8')) }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  }
  return bindings
}

export async function verifyPackageBindings(bundle: WorkflowPackage, repository: string): Promise<void> {
  if (JSON.stringify(await repositoryBindings(repository)) !== JSON.stringify(bundle.bindings)) throw new Error('Workflow package dependencies changed; rebuild required')
}

export async function buildWorkflowPackage(yamlPath: string, packageRoot: string, repository: string): Promise<WorkflowPackage> {
  const source = await readFile(yamlPath, 'utf8')
  const workflow = parse(source) as Workflow
  const compiled = await generateWorkflow(workflow, (workflow.execution_backend as { mode?: string } | undefined)?.mode === 'container')
  const bindings = await repositoryBindings(repository)
  const payload: Record<string, string> = {
    'workflow.js': compiled.script, 'stages.json': encode(compiled.stages),
    'definition.json': encode(compiled.workflow), 'source-map.json': encode(compiled.sourceMap), 'source.yaml': source,
  }
  const files = Object.fromEntries(Object.entries(payload).map(([name, content]) => [name, digest(content)]))
  const identity = { compilerVersion, runtimeVersion, workflowName: workflow.name, files, bindings }
  const id = digest(JSON.stringify(identity))
  const directory = resolve(packageRoot, id)
  const manifest: PackageManifest = { format: 1, id, ...identity }
  await mkdir(packageRoot, { recursive: true })
  const temporary = join(packageRoot, `.build-${randomUUID()}`)
  await mkdir(temporary)
  try {
    for (const [name, content] of Object.entries({ ...payload, 'manifest.json': encode(manifest) })) await writeFile(join(temporary, name), content, { mode: 0o600 })
    try { await rename(temporary, directory) }
    catch (error) { if (!['EEXIST', 'ENOTEMPTY'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error }
  } finally { await rm(temporary, { recursive: true, force: true }) }
  return loadWorkflowPackage(directory, repository)
}

export async function loadWorkflowPackage(directory: string, repository?: string): Promise<WorkflowPackage> {
  const manifest = JSON.parse(await readFile(join(directory, 'manifest.json'), 'utf8')) as PackageManifest
  if (manifest.format !== 1 || manifest.compilerVersion !== compilerVersion || manifest.runtimeVersion !== runtimeVersion) throw new Error('Unsupported workflow package version; rebuild required')
  const { compilerVersion: compiler, runtimeVersion: runtime, workflowName, files, bindings } = manifest
  if (!files || !bindings) throw new Error('Invalid workflow package manifest')
  const expectedFiles = ['definition.json', 'source-map.json', 'source.yaml', 'stages.json', 'workflow.js']
  if (Object.keys(files).sort().join(',') !== expectedFiles.join(',')) throw new Error('Invalid workflow package file list')
  if (digest(JSON.stringify({ compilerVersion: compiler, runtimeVersion: runtime, workflowName, files, bindings })) !== manifest.id) throw new Error('Workflow package identity mismatch')
  const payload: Record<string, string> = {}
  for (const [name, expected] of Object.entries(files)) {
    const content = await readFile(join(directory, name), 'utf8')
    if (digest(content) !== expected) throw new Error(`Workflow package integrity mismatch: ${name}`)
    payload[name] = content
  }
  const workflow = JSON.parse(payload['definition.json']!) as Workflow
  checkOperatorWorkflow(workflow, { containerBackend: (workflow.execution_backend as { mode?: string } | undefined)?.mode === 'container' })
  const stages = JSON.parse(payload['stages.json']!) as CompiledWorkflow['stages']
  const expectedStages = Object.fromEntries([...workflow.phases, ...Object.values(workflow.sub_workflows ?? {}).flatMap(loop => loop.phases)].filter(step => !['loop', 'dispatch'].includes(step.type ?? 'llm')).map(step => [step.id, step]))
  if (JSON.stringify(stages) !== JSON.stringify(expectedStages)) throw new Error('Workflow package stage configuration mismatch')
  const bundle: WorkflowPackage = { id: manifest.id, directory: resolve(directory), script: payload['workflow.js']!, workflow, stages, sourceMap: JSON.parse(payload['source-map.json']!), bindings }
  if (repository) await verifyPackageBindings(bundle, repository)
  return bundle
}
