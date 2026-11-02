import type { Scope } from './types.js'

function field(source: unknown, keys: string[]): unknown {
  let value = source
  for (const key of keys) {
    if (['__proto__', 'constructor', 'prototype'].includes(key)) throw new Error('Unsafe field path')
    if (!value || typeof value !== 'object' || Array.isArray(value) || !Object.hasOwn(value, key)) return null
    value = (value as Record<string, unknown>)[key]
  }
  return value ?? null
}

function literal(text: string): unknown {
  if ((text.startsWith("'") && text.endsWith("'")) || (text.startsWith('"') && text.endsWith('"'))) return text.slice(1, -1)
  if (['none', 'null'].includes(text.toLowerCase())) return null
  if (text.toLowerCase() === 'true') return true
  if (text.toLowerCase() === 'false') return false
  if (text.trim() && Number.isFinite(Number(text))) return Number(text)
  return text
}

export function reference(expression: string, scope: Scope): unknown {
  const [path = '', fallback] = expression.split(/\|\s*default\s+/, 2)
  const parts = path.trim().split('.')
  const root = parts[0]!
  let value: unknown = null
  if (path.trim() === 'state.*') value = JSON.parse(JSON.stringify(scope.state))
  else if (path.trim() === 'loop_history') value = scope.loop_history
  else if (['state', 'context', 'globals', 'loop_vars', 'loop_state'].includes(root) && parts.length > 1) value = field(scope[root as keyof Scope], parts.slice(1))
  else if (parts.length > 1 && Object.hasOwn(scope.step_outputs, root)) {
    const output = scope.step_outputs[root]
    value = output && typeof output === 'object' && !Array.isArray(output) ? field(output, parts.slice(1)) : output
  } else if (Object.hasOwn(scope.state, root)) value = parts.length > 1 ? field(scope.state[root], parts.slice(1)) : scope.state[root]
  else for (const source of [scope.step_outputs, scope.globals, scope.context]) {
    if (Object.hasOwn(source, path.trim())) { value = source[path.trim()]; break }
  }
  return value === null || value === undefined ? fallback === undefined ? null : literal(fallback.trim()) : value
}

export function pythonString(value: unknown, nested = false): string {
  if (value === null || value === undefined) return 'None'
  if (typeof value === 'boolean') return value ? 'True' : 'False'
  if (typeof value === 'string') return nested ? `'${value.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t')}'` : value
  if (Array.isArray(value)) return `[${value.map(item => pythonString(item, true)).join(', ')}]`
  if (typeof value === 'object') return `{${Object.entries(value).map(([key, item]) => `${pythonString(key, true)}: ${pythonString(item, true)}`).join(', ')}}`
  return String(value)
}

export function resolve(value: unknown, scope: Scope): unknown {
  if (Array.isArray(value)) return value.map(item => resolve(item, scope))
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolve(item, scope)]))
  if (typeof value !== 'string') return value
  const exact = /^\$\{([^}]+)\}$/.exec(value)
  if (exact) return reference(exact[1]!, scope)
  return value.replace(/\$\{([^}]+)\}/g, (_, expression: string) => pythonString(reference(expression, scope)))
}

export function truth(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0
  if (value && typeof value === 'object') return Object.keys(value).length > 0
  return Boolean(value)
}

function equal(left: unknown, right: unknown): boolean {
  if (typeof left === 'boolean') left = Number(left)
  if (typeof right === 'boolean') right = Number(right)
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((item, index) => equal(item, right[index]))
  if (left && right && typeof left === 'object' && typeof right === 'object') {
    const entries = Object.entries(left)
    return entries.length === Object.keys(right).length && entries.every(([key, value]) => Object.hasOwn(right, key) && equal(value, (right as Record<string, unknown>)[key]))
  }
  return left === right
}

function compare(left: unknown, right: unknown, operator: string): boolean {
  if (operator === '==') return equal(left, right)
  if (operator === '!=') return !equal(left, right)
  if (operator === 'in' || operator === 'not in') {
    let contains: boolean
    if (typeof right === 'string' && typeof left === 'string') contains = right.includes(left)
    else if (Array.isArray(right)) contains = right.some(item => equal(left, item))
    else if (right && typeof right === 'object' && typeof left === 'string') contains = Object.hasOwn(right, left)
    else throw new Error('membership requires a string, mapping, sequence, or set')
    return operator === 'in' ? contains : !contains
  }
  if (typeof left === 'boolean') left = Number(left)
  if (typeof right === 'boolean') right = Number(right)
  if (!((typeof left === 'number' && typeof right === 'number') || (typeof left === 'string' && typeof right === 'string'))) throw new Error('ordered comparison requires matching strings or numbers')
  if (operator === '>') return left > right
  if (operator === '<') return left < right
  if (operator === '>=') return left >= right
  return left <= right
}

function evaluate(expression: string, environment: Record<string, unknown>, enhanced: boolean): boolean {
  const tokens: string[] = []
  const lexer = /\s+|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|==|!=|>=|<=|[()><]|[\p{L}\p{N}._-]+/uy
  for (let position = 0; position < expression.length;) {
    lexer.lastIndex = position
    const match = lexer.exec(expression)
    if (!match) {
      if (enhanced) throw new Error(`unexpected character ${expression[position]}`)
      position++
    } else {
      position = lexer.lastIndex
      if (match[0].trim()) tokens.push(match[0])
    }
  }
  let cursor = 0
  const take = (token: string): boolean => { if (tokens[cursor] === token) { cursor++; return true }; return false }
  const primary = (): unknown => {
    if (take('(')) {
      const result = expressionValue()
      if (!take(')')) throw new Error('expected closing parenthesis')
      return result
    }
    const token = tokens[cursor++]
    if (token === undefined) throw new Error('unexpected end of expression')
    if (['true', 'false', 'null', 'none'].includes(token) || /^["']/.test(token) || Number.isFinite(Number(token))) return literal(token)
    return Object.hasOwn(environment, token) ? environment[token] : token
  }
  const comparison = (): unknown => {
    let left = primary()
    let compared = false
    let result = true
    while (true) {
      let operator = tokens[cursor]
      if (enhanced && operator === 'not' && tokens[cursor + 1] === 'in') operator = 'not in'
      if (!operator || !['==', '!=', '>', '<', '>=', '<=', 'in', ...(enhanced ? ['not in'] : [])].includes(operator)) return compared ? result : left
      cursor += operator === 'not in' ? 2 : 1
      const right = primary()
      result = result && compare(left, right, operator)
      if (!enhanced) return result
      compared = true
      left = right
    }
  }
  const term = (): unknown => take('not') ? !truth(term()) : comparison()
  const conjunction = (): unknown => {
    let value = term()
    while (take('and')) { const right = term(); value = truth(value) && truth(right) }
    return value
  }
  const expressionValue = (): unknown => {
    let value = enhanced ? conjunction() : term()
    while (tokens[cursor] === 'or' || (!enhanced && tokens[cursor] === 'and')) {
      const operator = tokens[cursor++]
      const right = enhanced ? conjunction() : term()
      value = operator === 'and' ? truth(value) && truth(right) : truth(value) || truth(right)
    }
    return value
  }
  if (!tokens.length) return false
  const result = truth(expressionValue())
  if (enhanced && cursor !== tokens.length) throw new Error(`unexpected token ${tokens[cursor]}`)
  return result
}

function fields(expression: string, sources: Record<string, unknown>[]): string {
  return expression.replace(/\$\.([\p{L}\p{N}_]+)/gu, (_, name: string) => {
    for (const source of sources) if (Object.hasOwn(source, name)) return typeof source[name] === 'string' ? source[name] : JSON.stringify(source[name])
    return `'${name}'`
  })
}

export function conditionDecision(expression: string, scope: Scope, stop = false): { matched: boolean; error?: string } {
  let resolved: unknown = expression
  if (!stop) {
    if (/^\$\{[^{}]+\}$/.test(expression)) resolved = reference(expression.slice(2, -1), scope)
    else if (expression.includes('${')) resolved = expression.replace(/\$\{([^{}]+)\}/g, (_, path: string) => JSON.stringify(reference(path.trim(), scope)))
    else if (['true', 'false'].includes(expression.toLowerCase()) || (expression.trim() && Number.isFinite(Number(expression)))) resolved = literal(expression)
  }
  if (typeof resolved !== 'string') return { matched: truth(resolved) }
  const sources = stop ? [scope.loop_state, scope.globals] : [scope.step_outputs, scope.globals, scope.context, scope.loop_state]
  const environment = stop ? { ...scope.globals, ...scope.loop_state } : { ...scope.state, ...scope.globals, ...scope.context, ...scope.loop_state, ...scope.loop_vars, ...scope.step_outputs }
  try { return { matched: evaluate(fields(resolved, sources), environment, !stop && typeof scope.globals.review_fail_closed === 'boolean') } }
  catch (error) { return { matched: !stop, error: String(error) } }
}

export function condition(expression: string, scope: Scope): boolean { return conditionDecision(expression, scope).matched }
