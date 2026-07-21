import { describe, expect, it } from 'vitest'
import { describeIdleFace, idleTagType } from '../src/modules/companions/idleFace'
import type { CompanionFaceView } from '../src/api/eidolonData'

function face(overrides: Partial<CompanionFaceView>): CompanionFaceView {
  return {
    companion_id: 'c-1',
    face_asset_id: 'fa-1',
    version: 1,
    source: 'upload',
    content_type: 'image/jpeg',
    size_bytes: 1234,
    sha256: 'abc',
    width: 448,
    height: 448,
    idle_status: 'none',
    idle_ready: false,
    idle_error: null,
    created_at: '2026-07-21T00:00:00Z',
    updated_at: '2026-07-21T00:00:00Z',
    ...overrides,
  }
}

describe('describeIdleFace', () => {
  it('returns null when no face is configured', () => {
    expect(describeIdleFace(null)).toBeNull()
  })

  it('treats an unset/none status as "not generated", regenerate allowed', () => {
    const idle = describeIdleFace(face({ idle_status: 'none' }))!
    expect(idle.tone).toBe('idle')
    expect(idle.generating).toBe(false)
    expect(idle.ready).toBe(false)
    expect(idle.canRegenerate).toBe(true)
  })

  it('shows pending/generating as in-flight and blocks regenerate', () => {
    for (const status of ['pending', 'generating'] as const) {
      const idle = describeIdleFace(face({ idle_status: status }))!
      expect(idle.tone).toBe('progress')
      expect(idle.generating).toBe(true)
      expect(idle.ready).toBe(false)
      expect(idle.canRegenerate).toBe(false)
    }
  })

  it('marks ready only when a clip actually exists', () => {
    const ready = describeIdleFace(face({ idle_status: 'ready', idle_ready: true }))!
    expect(ready.tone).toBe('ready')
    expect(ready.ready).toBe(true)
    expect(ready.canRegenerate).toBe(true)

    // ready status but no clip bytes → not renderable
    const halfReady = describeIdleFace(face({ idle_status: 'ready', idle_ready: false }))!
    expect(halfReady.ready).toBe(false)
  })

  it('surfaces the failure reason and allows retry', () => {
    const idle = describeIdleFace(
      face({ idle_status: 'failed', idle_error: 'ditto unreachable' }),
    )!
    expect(idle.tone).toBe('failed')
    expect(idle.generating).toBe(false)
    expect(idle.canRegenerate).toBe(true)
    expect(idle.hint).toContain('ditto unreachable')
  })

  it('falls back to a generic failure hint when no error is recorded', () => {
    const idle = describeIdleFace(face({ idle_status: 'failed', idle_error: null }))!
    expect(idle.hint).not.toContain('null')
    expect(idle.hint.length).toBeGreaterThan(0)
  })
})

describe('idleTagType', () => {
  it('maps every tone to an el-tag type', () => {
    expect(idleTagType('ready')).toBe('success')
    expect(idleTagType('progress')).toBe('warning')
    expect(idleTagType('failed')).toBe('danger')
    expect(idleTagType('idle')).toBe('info')
  })
})
