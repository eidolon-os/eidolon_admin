import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()
const postMock = vi.fn()
const patchMock = vi.fn()

vi.mock('../src/api/client', () => ({
  default: {
    get: getMock,
    post: postMock,
    patch: patchMock,
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
})
