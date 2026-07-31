declare const process: {
  env: Record<string, string | undefined>
}

declare module "node:fs" {
  export function appendFileSync(path: string, data: string, encoding: string): void
  export function existsSync(path: string): boolean
  export function mkdtempSync(prefix: string): string
  export function readFileSync(path: string, encoding: string): string
  export function rmSync(
    path: string,
    options: { readonly recursive?: boolean; readonly force?: boolean },
  ): void
  export function writeFileSync(path: string, data: string, encoding: string): void
}

declare module "node:os" {
  export function tmpdir(): string
}

declare module "node:path" {
  export function join(...parts: readonly string[]): string
}

declare module "bun:test" {
  export type Matcher = {
    toBe(expected: unknown): void
    toBeGreaterThan(expected: number): void
    toBeLessThan(expected: number): void
    toBeLessThanOrEqual(expected: number): void
    toBeNull(): void
    toContain(expected: unknown): void
    toEqual(expected: unknown): void
    toHaveLength(expected: number): void
    toThrow(expected?: unknown): void
    readonly not: Matcher
    readonly rejects: {
      toThrow(expected?: unknown): Promise<void>
    }
  }
  export function afterEach(fn: () => void): void
  export function beforeEach(fn: () => void): void
  export function describe(name: string, fn: () => void): void
  export function expect(value: unknown): Matcher
  export function test(name: string, fn: () => void | Promise<void>): void
}
