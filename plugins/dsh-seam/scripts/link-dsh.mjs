import { readdir, readFile, realpath, mkdir, symlink, lstat, unlink } from 'node:fs/promises'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = await realpath(process.argv[2] ?? (() => { throw new Error('Usage: npm run link:dsh -- /path/to/deepseek-harness') })())
const destination = resolve(dirname(fileURLToPath(import.meta.url)), '../node_modules')
const packages = new Map()
async function discover(directory) {
  let metadata
  try { metadata = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8')) }
  catch (error) { if (error.code !== 'ENOENT') throw error }
  if (metadata?.name?.startsWith('@deepseek-ai/')) packages.set(metadata.name, directory)
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && !['node_modules', 'dist', '.git'].includes(entry.name)) await discover(join(directory, entry.name))
  }
}
await discover(join(root, 'packages'))
await discover(join(root, 'vendor'))
for (const [name, source] of packages) {
  const target = join(destination, name)
  await mkdir(dirname(target), { recursive: true })
  const existing = await lstat(target).catch(error => { if (error.code !== 'ENOENT') throw error })
  if (existing && !existing.isSymbolicLink()) throw new Error(`Refusing to replace installed package: ${target}`)
  if (existing) await unlink(target)
  await symlink(source, target, 'dir')
}
console.log(`Linked ${packages.size} local dsh packages from ${root}`)
