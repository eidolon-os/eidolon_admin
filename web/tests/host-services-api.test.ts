import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()
const postMock = vi.fn()

vi.mock('../src/api/client', () => ({
  default: {
    get: getMock,
    post: postMock,
  },
}))

describe('api/hostServices.ts', () => {
  beforeEach(() => {
    getMock.mockReset()
    postMock.mockReset()
  })

  it('returns the page, not the HTTP response around it', async () => {
    // The table stayed empty while the API answered: the client passes the
    // whole axios response through, and the page is its `data`.
    getMock.mockResolvedValueOnce({
      data: {
        driver: 'supervisord',
        services: [{ service_id: 'channel', revision: 1, runtime_state: 'ready' }],
      },
    })

    const { listHostServices } = await import('../src/api/hostServices')
    const page = await listHostServices()

    expect(getMock).toHaveBeenCalledWith('/host/services')
    expect(page.driver).toBe('supervisord')
    expect(page.services.map((service) => service.service_id)).toEqual(['channel'])
  })

  it('restarts with the revision that was displayed and returns eidolond\'s result', async () => {
    postMock.mockResolvedValueOnce({
      data: { service_id: 'channel', operation: 'restart', audit_position: 7, replayed: false },
    })

    const { changeHostService } = await import('../src/api/hostServices')
    const result = await changeHostService('channel', 'restart', 3)

    expect(postMock).toHaveBeenCalledWith('/host/services/channel/restart', {
      expected_revision: 3,
    })
    expect(result.audit_position).toBe(7)
  })
})
