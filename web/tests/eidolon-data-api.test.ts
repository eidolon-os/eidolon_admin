import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()
const postMock = vi.fn()
const patchMock = vi.fn()

vi.mock('../src/api/client', () => ({
  default: {
    get: getMock,
    post: postMock,
    patch: patchMock,
    defaults: { baseURL: '/api' },
  },
}))

describe('api/eidolonData.ts', () => {
  beforeEach(() => {
    getMock.mockReset()
    postMock.mockReset()
    patchMock.mockReset()
  })

  it('lists and creates owners through admin data endpoints', async () => {
    getMock.mockResolvedValueOnce({
      data: {
        owners: [{ owner_id: 'owner-a', display_name: 'Owner A', kind: 'person' }],
      },
    })
    postMock.mockResolvedValueOnce({
      data: { owner_id: 'owner-b', display_name: 'Owner B', kind: 'person' },
    })

    const { createOwner, listOwners } = await import('../src/api/eidolonData')
    const owners = await listOwners()
    const created = await createOwner({ owner_id: 'owner-b', display_name: 'Owner B' })

    expect(getMock).toHaveBeenCalledWith('/owners')
    expect(postMock).toHaveBeenCalledWith('/owners', {
      owner_id: 'owner-b',
      display_name: 'Owner B',
    })
    expect(owners[0].owner_id).toBe('owner-a')
    expect(created.owner_id).toBe('owner-b')
  })

  it('URL-encodes owner-scoped endpoints', async () => {
    getMock
      .mockResolvedValueOnce({ data: { owner: { owner_id: 'owner/with space' }, counts: {} } })
      .mockResolvedValueOnce({ data: { companions: [] } })
      .mockResolvedValueOnce({ data: { persona_genomes: [] } })
      .mockResolvedValueOnce({ data: { devices: [] } })
      .mockResolvedValueOnce({ data: { conversations: [] } })
      .mockResolvedValueOnce({ data: { memory_realms: [] } })
      .mockResolvedValueOnce({ data: { jobs: [] } })
      .mockResolvedValueOnce({ data: { events: [] } })

    const {
      getOwnerOverview,
      listOwnerCompanions,
      listOwnerConversations,
      listOwnerDevices,
      listOwnerEvents,
      listOwnerJobs,
      listOwnerMemoryRealms,
      listOwnerPersonaGenomes,
    } = await import('../src/api/eidolonData')
    const ownerId = 'owner/with space'

    await getOwnerOverview(ownerId)
    await listOwnerCompanions(ownerId)
    await listOwnerPersonaGenomes(ownerId)
    await listOwnerDevices(ownerId)
    await listOwnerConversations(ownerId)
    await listOwnerMemoryRealms(ownerId)
    await listOwnerJobs(ownerId)
    await listOwnerEvents(ownerId)

    const encoded = 'owner%2Fwith%20space'
    expect(getMock).toHaveBeenNthCalledWith(1, `/owners/${encoded}/workspace`)
    expect(getMock).toHaveBeenNthCalledWith(2, `/owners/${encoded}/companions`)
    expect(getMock).toHaveBeenNthCalledWith(3, `/owners/${encoded}/persona-genomes`)
    expect(getMock).toHaveBeenNthCalledWith(4, `/owners/${encoded}/devices`)
    expect(getMock).toHaveBeenNthCalledWith(5, `/owners/${encoded}/conversations`)
    expect(getMock).toHaveBeenNthCalledWith(6, `/owners/${encoded}/memory-realms`)
    expect(getMock).toHaveBeenNthCalledWith(7, `/owners/${encoded}/jobs`)
    expect(getMock).toHaveBeenNthCalledWith(8, `/owners/${encoded}/events`)
  })

  it('initializes owner workspace through canonical endpoint', async () => {
    postMock.mockResolvedValueOnce({
      data: {
        companion: { companion_id: 'c:owner-a:default' },
        persona_genome: { genome_id: 'g:owner-a:default' },
        memory_realm: { realm_id: 'r:owner-a:default' },
      },
    })

    const { initializeOwnerWorkspace } = await import('../src/api/eidolonData')
    await initializeOwnerWorkspace('owner-a', { companion_display_name: 'Xiaoyi' })

    expect(postMock).toHaveBeenCalledWith('/owners/owner-a/workspace/initialize', {
      companion_display_name: 'Xiaoyi',
    })
  })

  it('claims and binds an approved device through owner-scoped endpoints', async () => {
    getMock.mockResolvedValueOnce({ data: { devices: [], hub_available: true } })
    postMock
      .mockResolvedValueOnce({ data: { device_id: 'esp/1', owner_id: 'owner/1' } })
      .mockResolvedValueOnce({ data: { device_id: 'esp/1', bound_companion_id: 'c/1' } })

    const { addNearbyDeviceToOwner, bindOwnerDevice, listNearbyOwnerDevices } = await import('../src/api/eidolonData')
    await listNearbyOwnerDevices('owner/1')
    await addNearbyDeviceToOwner('owner/1', 'esp/1', { companion_id: 'c/1' })
    await bindOwnerDevice('owner/1', 'esp/1', 'c/1')

    expect(getMock).toHaveBeenCalledWith('/owners/owner%2F1/nearby-devices')
    expect(postMock).toHaveBeenNthCalledWith(
      1,
      '/owners/owner%2F1/nearby-devices/esp%2F1/claim',
      { companion_id: 'c/1' },
    )
    expect(postMock).toHaveBeenNthCalledWith(
      2,
      '/owners/owner%2F1/devices/esp%2F1/bind-companion',
      null,
      { params: { companion_id: 'c/1' } },
    )
  })

  it('calls companion persona governance endpoints with encoded ids', async () => {
    getMock
      .mockResolvedValueOnce({ data: { current_genome: null, history: [] } })
      .mockResolvedValueOnce({ data: { proposals: [], timeline: [] } })
      .mockResolvedValueOnce({ data: { events: [] } })
    postMock
      .mockResolvedValueOnce({ data: { genome_id: 'g/1', status: 'committed' } })
      .mockResolvedValueOnce({ data: { genome_id: 'g/2', status: 'rejected' } })
      .mockResolvedValueOnce({ data: { genome_id: 'g/0', status: 'committed' } })

    const {
      approveCompanionPersonaProposal,
      listCompanionGenomes,
      listCompanionPersonaProposals,
      listCompanionPersonaTimeline,
      rejectCompanionPersonaProposal,
      rollbackCompanionGenome,
    } = await import('../src/api/eidolonData')

    await listCompanionGenomes('owner/1', 'c with space')
    await listCompanionPersonaProposals('owner/1', 'c with space', 'all')
    await listCompanionPersonaTimeline('owner/1', 'c with space')
    await approveCompanionPersonaProposal('owner/1', 'c with space', 'g/1', {
      expected_base_genome_id: 'g/0',
    })
    await rejectCompanionPersonaProposal('owner/1', 'c with space', 'g/2', {
      reason: 'not desired',
    })
    await rollbackCompanionGenome('owner/1', 'c with space', 'g/0')

    const owner = 'owner%2F1'
    const companion = 'c%20with%20space'
    expect(getMock).toHaveBeenNthCalledWith(1, `/owners/${owner}/companions/${companion}/genomes`)
    expect(getMock).toHaveBeenNthCalledWith(
      2,
      `/owners/${owner}/companions/${companion}/genome/proposals`,
      { params: { status: 'all' } },
    )
    expect(getMock).toHaveBeenNthCalledWith(
      3,
      `/owners/${owner}/companions/${companion}/genome/timeline`,
    )
    expect(postMock).toHaveBeenNthCalledWith(
      1,
      `/owners/${owner}/companions/${companion}/genome/proposals/g%2F1/approve`,
      { expected_base_genome_id: 'g/0' },
    )
    expect(postMock).toHaveBeenNthCalledWith(
      2,
      `/owners/${owner}/companions/${companion}/genome/proposals/g%2F2/reject`,
      { reason: 'not desired' },
    )
    expect(postMock).toHaveBeenNthCalledWith(
      3,
      `/owners/${owner}/companions/${companion}/genomes/g%2F0/rollback`,
      {},
    )
  })

  it('re-triggers idle generation on the active face', async () => {
    postMock.mockResolvedValueOnce({ data: { companion_id: 'c-1', idle_status: 'pending' } })
    const { regenerateCompanionIdle } = await import('../src/api/eidolonData')
    const res = await regenerateCompanionIdle('owner/a', 'c 1')
    expect(postMock).toHaveBeenCalledWith(
      '/owners/owner%2Fa/companions/c%201/face/idle:regenerate',
    )
    expect(res.idle_status).toBe('pending')
  })

  it('builds a cache-busted idle video URL', async () => {
    const { companionIdleVideoUrl } = await import('../src/api/eidolonData')
    expect(companionIdleVideoUrl('owner/a', 'c 1')).toBe(
      '/api/owners/owner%2Fa/companions/c%201/face/idle/video',
    )
    expect(companionIdleVideoUrl('o', 'c', '2026-07-21T00:00:00Z')).toBe(
      '/api/owners/o/companions/c/face/idle/video?v=2026-07-21T00%3A00%3A00Z',
    )
  })
})
