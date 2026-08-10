import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RuntimeBlackboardResponse } from '../src/api/missionControl'
import { blackboardCapabilities, blackboardDevices, rawBlackboardEntry } from '../src/modules/mission-control/blackboard'
import RuntimeBlackboardViewer from '../src/modules/mission-control/components/RuntimeBlackboardViewer.vue'

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }))

vi.mock('../src/api/client', () => ({
  default: { get: getMock },
}))

const fixture: RuntimeBlackboardResponse = {
  generated_at: '2026-07-18T12:00:01Z',
  bucket: 'EIDOLON_RUNTIME_DEVICES',
  owner_filter: null,
  read_only: true,
  entries: [{
    key: 'owner.hash.current',
    owner_id: 'owner-1',
    error: '',
    snapshot: {
      schema_version: 2,
      owner_id: 'owner-1',
      epoch: 'epoch-1',
      revision: 7,
      ready: true,
      hub_lease_expires_at: '2099-07-18T12:00:45Z',
      updated_at: '2026-07-18T12:00:00Z',
      future_snapshot_field: { must_survive: true },
      devices: {
        'camera-1': {
          device_id: 'camera-1',
          registration_id: 'registration-1',
          provider_companion_id: 'companion-guard',
          provider_companion_name: 'Guard Companion',
          name: 'Front Camera',
          aliases: ['front'],
          visibility: 'owner',
          capabilities: [{
            name: 'camera.capture',
            version: 3,
            description: 'Capture a diagnostic image',
            input_schema: { type: 'object', properties: { quality: { type: 'integer' } } },
            result_schema: { type: 'object', properties: { image_id: { type: 'string' } } },
          }],
          manifest_revision: 'sha256:manifest',
          status: 'online',
          registered_at: '2026-07-18T11:59:00Z',
          lease_expires_at: '2099-07-18T12:00:45Z',
          last_seen_at: '2026-07-18T12:00:00Z',
          room_name: 'owner-room',
          participant_sid: 'PA_camera',
          presence_revision: 'presence-9',
        },
      },
    },
  }],
}

describe('Mission Control Runtime Blackboard', () => {
  beforeEach(() => getMock.mockReset())

  it('uses one read-only GET and supports all-owner or owner-scoped reads', async () => {
    getMock.mockResolvedValue({ data: fixture })
    const { getRuntimeBlackboard } = await import('../src/api/missionControl')

    await getRuntimeBlackboard()
    await getRuntimeBlackboard('owner-1')

    expect(getMock).toHaveBeenNthCalledWith(1, '/mission-control/runtime-blackboard', {
      params: undefined,
      suppressToast: true,
    })
    expect(getMock).toHaveBeenNthCalledWith(2, '/mission-control/runtime-blackboard', {
      params: { owner_id: 'owner-1' },
      suppressToast: true,
    })
  })

  it('preserves v2 contracts, provider companion name, and unknown raw fields', () => {
    const entry = fixture.entries[0]
    const devices = blackboardDevices(entry)
    const capabilities = blackboardCapabilities(devices[0].device)
    const raw = JSON.stringify(rawBlackboardEntry(entry))

    expect(devices[0].device.provider_companion_name).toBe('Guard Companion')
    expect(capabilities[0].input_schema.properties.quality.type).toBe('integer')
    expect(capabilities[0].result_schema.properties.image_id.type).toBe('string')
    expect(raw).toContain('future_snapshot_field')
    expect(raw).toContain('must_survive')
  })

  it('renders the complete operational hierarchy in structured mode', async () => {
    const app = createSSRApp({
      render: () => h(RuntimeBlackboardViewer, { owners: [], initialResponse: fixture }),
    })
    app.use(ElementPlus)
    const html = await renderToString(app)

    expect(html).toContain('共享 Runtime Blackboard')
    expect(html).toContain('owner-1')
    expect(html).toContain('epoch-1')
    expect(html).toContain('Guard Companion')
    expect(html).toContain('registration-1')
    expect(html).toContain('presence-9')
    expect(html).toContain('camera.capture')
    expect(html).toContain('Capture a diagnostic image')
    expect(html).toContain('INPUT SCHEMA')
    expect(html).toContain('RESULT SCHEMA')
    expect(html).toContain('quality')
    expect(html).toContain('image_id')
  })
})
