import { Ajv, type AnySchema, type ValidateFunction } from 'ajv'

const ajv = new Ajv({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false })
const validators = new Map<string, ValidateFunction>()

export function compileContract(schema: unknown): ValidateFunction {
  if (typeof schema !== 'boolean' && (!schema || typeof schema !== 'object' || Array.isArray(schema))) throw new Error('Stage contract must be a JSON Schema object or boolean')
  if (typeof schema === 'object' && '$async' in schema) throw new Error('Async stage schemas are not supported')
  const key = JSON.stringify(schema)
  let validator = validators.get(key)
  if (!validator) {
    validator = ajv.compile(schema as AnySchema)
    validators.set(key, validator)
  }
  return validator
}

export function contractErrors(schema: unknown, value: unknown): string[] {
  const validate = compileContract(schema)
  return validate(value) ? [] : (validate.errors ?? []).map(error => `${error.instancePath || '/'} ${error.message}`)
}
