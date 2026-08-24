// The management surface may only reach the Host through the generated client.
//
// Phase 0's last gate (plan §Phase 0), and it exists because the alternative is
// a review habit. This repository already contains a hand-written client for the
// operator plane — the shape the management surface must never take — and the
// easiest way to add a management page is to import what is already there and
// call /api/local/v1 or /api/control-plane/v1 directly. That works, once. Then
// two clients disagree about the wire, and the document neither of them is
// generated from stops being the contract.
//
// Enforced by reading the sources rather than by types, because the failure mode
// is an import that compiles perfectly well.

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const MANAGEMENT = join(dirname(fileURLToPath(import.meta.url)), '../src/management')
const GENERATED = 'generated/management-v1'

function sources(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry)
    return statSync(path).isDirectory() ? sources(path) : [path]
  })
}

function imports(source: string): string[] {
  return [...source.matchAll(/from\s+'([^']+)'/g)].map((match) => match[1])
}

describe('the management surface', () => {
  const files = sources(MANAGEMENT).filter(
    (path) => path.endsWith('.ts') || path.endsWith('.vue'),
  )

  it('has sources to check, so this gate cannot pass vacuously', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  it('imports no hand-written API client', () => {
    // Named individually rather than as "anything under api/": the point is
    // that these clients speak surfaces the plan forbids here, and a future one
    // should have to be considered rather than silently matched.
    const forbidden = ['@/api/operatorPlane', '../api/operatorPlane', '@/api/client']
    for (const path of files) {
      for (const name of imports(readFileSync(path, 'utf8'))) {
        expect(forbidden.includes(name), `${path} imports ${name}`).toBe(false)
      }
    }
  })

  it('never spells a Host URL itself', () => {
    // A literal path here is a second definition of the contract, and the one
    // that will not be regenerated when the document changes.
    for (const path of files) {
      if (path.includes(GENERATED)) continue
      const source = readFileSync(path, 'utf8')
      expect(source, `${path} contains a literal API path`).not.toMatch(/['"`]\/api\//)
      expect(source, `${path} calls fetch directly`).not.toMatch(/\bfetch\s*\(/)
    }
  })

  it('keeps the generated types generated', () => {
    const generated = files.filter((path) => path.includes(GENERATED))
    expect(generated).toHaveLength(1)
    // The header is what a person reads before editing; the drift gate in the
    // server suite is what stops them.
    expect(readFileSync(generated[0], 'utf8')).toMatch(/Do not edit/)
  })
})
