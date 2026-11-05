import { resolve } from 'node:path'
import { buildWorkflowPackage } from '../src/workflow-package.js'

const [yaml, output, repository] = process.argv.slice(2)
if (!yaml || !output || !repository) throw new Error('Usage: npm run compile:workflow -- workflow.yaml package-root repository')
const bundle = await buildWorkflowPackage(resolve(yaml), resolve(output), resolve(repository))
console.log(JSON.stringify({ id: bundle.id, directory: bundle.directory, stages: Object.keys(bundle.stages) }))
