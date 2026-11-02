import { cp, mkdir, readdir, readFile, rename, stat, writeFile } from 'node:fs/promises'
import { join, relative, resolve } from 'node:path'
import type { Data, Workflow } from './types.js'

type Hook = NonNullable<Workflow['hooks']>[string][number]

async function writeJson(path: string, value: unknown): Promise<void> {
  await mkdir(resolve(path, '..'), { recursive: true })
  const temporary = `${path}.${process.pid}.tmp`
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 })
  await rename(temporary, path)
}

async function snapshotProject(projectDirectory: string, outputDirectory: string, point: string): Promise<Data> {
  const snapshot: Data = {}
  const visit = async (directory: string): Promise<void> => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) await visit(path)
      else if (entry.isFile() && path.endsWith('.py')) {
        const info = await stat(path)
        try { snapshot[relative(projectDirectory, path)] = { content: await readFile(path, 'utf8'), size: info.size, mtime: info.mtimeMs / 1000 } }
        catch (error) { snapshot[relative(projectDirectory, path)] = { error: String(error) } }
      }
    }
  }
  await visit(projectDirectory)
  const filename = point === 'workflow_end' ? 'after_snapshot.json' : 'before_snapshot.json'
  const path = join(outputDirectory, filename)
  await writeJson(path, snapshot)
  return { snapshot_path: path, file_count: Object.keys(snapshot).length }
}

async function execute(hook: Hook, context: Data, outputDirectory: string, workspace: string, point: string): Promise<Data> {
  const params = hook.params ?? {}
  const projectDirectory = resolve(String(params.project_dir ?? context.PROJECT_DIR ?? workspace))
  switch (hook.operation) {
    case 'snapshot_project': return snapshotProject(projectDirectory, outputDirectory, point)
    case 'copy_artifacts': {
      const source = join(projectDirectory, '.sm-artifacts')
      const destination = join(outputDirectory, '.sm-artifacts')
      try { await stat(source) }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') return { artifacts_copied: false, dest: destination, reason: 'source not found' }
        throw error
      }
      await cp(source, destination, { recursive: true, force: true, errorOnExist: false })
      return { artifacts_copied: true, dest: destination }
    }
    case 'write_summary': {
      const path = join(outputDirectory, 'summary.json')
      await writeJson(path, { state: context.state ?? {}, phase_results: context.phase_results ?? {} })
      return { summary_path: path }
    }
    case 'save_telemetry': {
      const telemetry = context.telemetry && typeof context.telemetry === 'object' ? context.telemetry : {}
      const path = join(outputDirectory, 'telemetry.json')
      await writeJson(path, telemetry)
      return { telemetry_saved: true, path }
    }
    case 'noop': return { noop: true }
    default: throw new Error(`Unsupported hook operation: ${hook.operation}`)
  }
}

export async function executeWorkflowHooks(hooks: Hook[], context: Data, outputDirectory: string, workspace: string): Promise<Data> {
  const point = String(context._hook_point ?? '')
  const results: Data = {}
  for (const hook of hooks) {
    const key = hook.save_as ?? hook.operation
    try {
      results[key] = await execute(hook, context, outputDirectory, workspace, point)
    } catch (error) {
      if (hook.critical) throw new Error(`Hook '${hook.operation}' at '${point}' failed: ${String(error)}`, { cause: error })
      results[key] = { success: false, error: String(error) }
    }
  }
  return results
}
