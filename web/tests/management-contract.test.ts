// Web's obligation while it has no management pages (plan §1.4).
//
// A second consumer is the only outside pressure keeping this ABI from quietly
// taking the shape of one client. So the generated types are consumed here —
// compiled, narrowed, and asserted against — before any page exists. If the
// contract drifts into something a large screen cannot render, or into something
// only Mobile's assumptions satisfy, this is where it shows up.
//
// These are type-level assertions as much as runtime ones: the file has to
// compile under vue-tsc, and several checks exist to make it fail to compile if
// the contract changes shape.

import { describe, expect, it } from 'vitest'
import type {
  CompanionRosterView,
  CompanionSummaryView,
  ManagementContextView,
  ManagementResponses,
  OwnerContextView,
} from '../src/management/generated/management-v1'

/** A response body exactly as the Host sends it. */
const CONTEXT: ManagementContextView = {
  contract_version: '1',
  owner: { owner_id: 'owner-1', display_name: 'Manson', revision: 3 },
  default_companion_id: 'companion-a',
  capabilities: { 'companion.read': false, 'companion.create': false },
  limits: { max_active_companions: null },
}

describe('management v1 context', () => {
  it('names the Owner once, under owner', () => {
    // @ts-expect-error - a top-level owner_id would be a second place to read it
    const wrong: ManagementContextView = { ...CONTEXT, owner_id: 'owner-1' }
    expect(wrong).toBeTruthy()

    const owner: OwnerContextView = CONTEXT.owner
    expect(owner.owner_id).toBe('owner-1')
  })

  it('treats the default as a pointer that may be absent', () => {
    // Null is a real answer — no default-eligible Companion — and a client must
    // render that rather than choosing a Companion to stand in for it.
    const none: ManagementContextView = { ...CONTEXT, default_companion_id: null }
    const chosen = none.default_companion_id ?? null
    expect(chosen).toBeNull()
  })

  it('reads a capability as absent-or-false, never as permission', () => {
    // A name missing from the map is a version skew: this client knows of a
    // feature the Host does not describe. A name present and false is a feature
    // gate. Collapsing the two would make an old Host look like a new one with
    // everything switched off.
    const known = 'companion.create'
    const unknownToHost = 'companion.transmogrify'
    expect(CONTEXT.capabilities[known]).toBe(false)
    expect(CONTEXT.capabilities[unknownToHost]).toBeUndefined()

    const mayCreate = CONTEXT.capabilities[known] === true
    expect(mayCreate).toBe(false)
  })

  it('accepts a null limit without substituting a number', () => {
    // The plan proposes 8 active Companions. Nobody has measured it, so the
    // Host sends null and a client that hard-coded a number would outlive the
    // guess. What a client may do is treat null as "no limit to show".
    expect(CONTEXT.limits.max_active_companions).toBeNull()
    const label =
      CONTEXT.limits.max_active_companions === null
        ? 'no published limit'
        : String(CONTEXT.limits.max_active_companions)
    expect(label).toBe('no published limit')
  })

  it('keys the response type by the operation a client calls', () => {
    const answer: ManagementResponses['GET /api/management/v1/context'] = CONTEXT
    expect(answer.owner.revision).toBe(3)
  })

  it('has no way to ask on behalf of another Owner', () => {
    // Asserted by absence: the generated surface carries no request type with an
    // owner field, because the document declares no such parameter. If one ever
    // appears, this file is where a large-screen client would have started
    // sending it.
    const keys = Object.keys(CONTEXT)
    expect(keys).not.toContain('owner_id')
    expect(keys.sort()).toEqual(
      ['capabilities', 'contract_version', 'default_companion_id', 'limits', 'owner'].sort(),
    )
  })
})

/** A roster page exactly as the Host sends it. */
const ROSTER: CompanionRosterView = {
  contract_version: '1',
  default_companion_id: 'companion-a',
  companions: [
    {
      companion_id: 'companion-a',
      display_name: '小忆',
      kind: 'standard',
      lifecycle_state: 'active',
      revision: 2,
      created_at: '2026-08-24T09:30:00+00:00',
      updated_at: '2026-08-24T09:30:00+00:00',
    },
    {
      companion_id: 'companion-b',
      display_name: '',
      kind: 'standard',
      lifecycle_state: 'archived',
      revision: 5,
      created_at: '2026-08-24T09:31:00+00:00',
      updated_at: '2026-08-24T09:40:00+00:00',
    },
  ],
  next_cursor: null,
}

describe('management v1 roster', () => {
  it('reads the default from the page, never from a row', () => {
    // @ts-expect-error - a per-row flag would let two rows claim it
    const wrong: CompanionSummaryView = { ...ROSTER.companions[0], is_default: true }
    expect(wrong).toBeTruthy()

    const defaults = ROSTER.companions.filter(
      (row) => row.companion_id === ROSTER.default_companion_id,
    )
    expect(defaults).toHaveLength(1)
  })

  it('renders a null default rather than promoting a row', () => {
    const none: CompanionRosterView = { ...ROSTER, default_companion_id: null }
    const marked = none.companions.filter(
      (row) => row.companion_id === none.default_companion_id,
    )
    expect(marked).toHaveLength(0)
    // What a large screen may do is say so; what it may not do is pick one.
    expect(none.companions).toHaveLength(2)
  })

  it('shows archived Eidolons instead of filtering them out', () => {
    // Four states, so "the Owner archived it" is distinguishable from "it
    // cannot run right now". A client that treated this as a boolean would
    // have to guess which, and would guess the same way for both.
    expect(ROSTER.companions.map((row) => row.lifecycle_state)).toEqual([
      'active',
      'archived',
    ])
  })

  it('treats a kind it has never heard of as another kind, not an error', () => {
    // kind is a string in the contract on purpose: the set of product types is
    // the Host's to grow, and a row must stay renderable.
    const later: CompanionSummaryView = {
      ...ROSTER.companions[0],
      kind: 'a-kind-from-a-later-release',
    }
    expect(later.kind).toBe('a-kind-from-a-later-release')
  })

  it('leaves an unnamed Eidolon to the client rather than showing its id', () => {
    const unnamed = ROSTER.companions[1]
    expect(unnamed.display_name).toBe('')
    // The identifier is not a name, and substituting it here is how a person
    // ends up reading 'companion-b' where they expected what they called it.
    expect(unnamed.display_name || 'unnamed').not.toBe(unnamed.companion_id)
  })

  it('keys the roster response by the operation a client calls', () => {
    const answer: ManagementResponses['GET /api/management/v1/companions'] = ROSTER
    expect(answer.companions[0].revision).toBe(2)
  })

  it('has no way to ask for another Owner\'s roster', () => {
    const keys = Object.keys(ROSTER)
    expect(keys).not.toContain('owner_id')
    expect(ROSTER.companions.every((row) => !('owner_id' in row))).toBe(true)
  })
})
