import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()
const putMock = vi.fn()

vi.mock('../src/api/client', () => ({
  default: {
    get: getMock,
    put: putMock,
  },
}))

describe('api/controlPlane.ts', () => {
  beforeEach(() => {
    getMock.mockReset()
    putMock.mockReset()
  })

  it('encodes owner scope and forwards the operator-owned Hub credential', async () => {
    getMock.mockResolvedValueOnce({ data: { operation: 'admin.operator-device-inventory' } })
    const { getOwnerInventory } = await import('../src/api/controlPlane')

    await getOwnerInventory('owner one/二', '  Bearer operator  ')

    expect(getMock).toHaveBeenCalledWith(
      '/operator/v1/control-plane/owners/owner%20one%2F%E4%BA%8C/inventory',
      {
        headers: { Authorization: 'Bearer operator' },
        suppressToast: true,
      },
    )
  })

  it('submits the stable workflow request without rewriting its CAS fields', async () => {
    putMock.mockResolvedValueOnce({ data: { outcome: 'retry_required' } })
    const { admitDevice } = await import('../src/api/controlPlane')
    const input = {
      request_id: 'operator-1',
      owner_id: 'owner-1',
      device_id: 'device-1',
      companion_id: 'companion-1',
      expected_mount_revision: 3,
      replace_existing_mount: true,
    }

    const result = await admitDevice(input, 'Bearer operator')

    expect(putMock).toHaveBeenCalledWith(
      '/operator/v1/control-plane/device-admissions/device-1',
      input,
      {
        headers: { Authorization: 'Bearer operator' },
        suppressToast: true,
      },
    )
    expect(result.outcome).toBe('retry_required')
  })
})
