/**
 * Pure-unit tests for ``src/utils/format.ts``.
 *
 * Unlike ``api-devices.test.ts`` these need no live admin — the helpers
 * are deterministic functions over their arguments. Adding them now (in
 * 29.K cleanup) so the error-display + timestamp-format contract has
 * tests at all; the helpers are imported by every catalog page and a
 * regression would silently degrade every error message in the UI.
 */
import { describe, expect, it } from 'vitest'
import { extractErrorMessage, formatTimestamp } from '../src/utils/format'

describe('extractErrorMessage', () => {
  it('prefers axios response.data.detail (string)', () => {
    const err = { response: { data: { detail: 'tenant not found' } }, message: 'Request failed' }
    expect(extractErrorMessage(err)).toBe('tenant not found')
  })

  it('JSON-encodes a structured detail (FastAPI validation errors)', () => {
    const detail = [{ loc: ['body', 'user_id'], msg: 'field required' }]
    const err = { response: { data: { detail } } }
    // shape isn't pinned exactly — we just don't want the user seeing "[object Object]"
    const msg = extractErrorMessage(err)
    expect(msg).toContain('field required')
    expect(msg).toContain('user_id')
  })

  it('falls back to .message when no response data', () => {
    expect(extractErrorMessage(new Error('network unreachable'))).toBe('network unreachable')
  })

  it('passes through plain strings', () => {
    expect(extractErrorMessage('boom')).toBe('boom')
  })

  it('returns "unknown error" for null/undefined', () => {
    expect(extractErrorMessage(null)).toBe('unknown error')
    expect(extractErrorMessage(undefined)).toBe('unknown error')
  })

  it('coerces unknown shapes to string instead of throwing', () => {
    // Defensive: any object without .response or .message should still
    // produce *something* — the catalog pages call this from try/catch
    // wrappers and we never want the toast itself to throw.
    expect(typeof extractErrorMessage({ weird: true })).toBe('string')
  })
})

describe('formatTimestamp', () => {
  it('returns em-dash for null/undefined/empty', () => {
    expect(formatTimestamp(null)).toBe('—')
    expect(formatTimestamp(undefined)).toBe('—')
    expect(formatTimestamp('')).toBe('—')
  })

  it('formats a valid ISO timestamp via toLocaleString', () => {
    const out = formatTimestamp('2025-01-15T10:30:00Z')
    // toLocaleString output is locale-dependent; we just check it's non-empty
    // and doesn't include the raw "T" separator (i.e. it really got reformatted).
    expect(out).not.toBe('—')
    expect(out).not.toContain('T10:30')
  })

  it('returns input unchanged when string is not a parseable date', () => {
    // Some sources send a sentinel like "n/a" instead of a real ISO date;
    // we shouldn't render "Invalid Date" — falling back to the raw string
    // is more informative.
    expect(formatTimestamp('n/a')).toBe('n/a')
  })
})
