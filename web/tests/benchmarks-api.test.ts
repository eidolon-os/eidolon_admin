import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()
const deleteMock = vi.fn()

vi.mock('../src/api/client', () => ({
  default: {
    get: getMock,
    delete: deleteMock,
  },
}))

describe('api/benchmarks.ts', () => {
  beforeEach(() => {
    getMock.mockReset()
    deleteMock.mockReset()
  })

  it('lists benchmark projects from the unified endpoint', async () => {
    getMock.mockResolvedValueOnce({
      data: {
        projects: [
          {
            id: 'agent',
            label: 'Eidolon Agent',
            run_count: 0,
            latest_status: 'unknown',
            latest_modified_at: null,
            suites: [],
          },
        ],
      },
    })

    const { listBenchmarkProjects } = await import('../src/api/benchmarks')
    const projects = await listBenchmarkProjects()

    expect(getMock).toHaveBeenCalledWith('/benchmarks/projects', { suppressToast: true })
    expect(projects[0].id).toBe('agent')
  })

  it('lists runs with project and suite filters', async () => {
    getMock.mockResolvedValueOnce({ data: { runs: [] } })

    const { listUnifiedBenchmarkRuns } = await import('../src/api/benchmarks')
    const runs = await listUnifiedBenchmarkRuns({ project: 'channel', suite: 'voice' })

    expect(getMock).toHaveBeenCalledWith('/benchmarks/runs', {
      params: { project: 'channel', suite: 'voice' },
      suppressToast: true,
    })
    expect(runs).toEqual([])
  })

  it('URL-encodes run identifiers for detail and delete calls', async () => {
    getMock.mockResolvedValueOnce({
      data: {
        project: 'agent',
        suite: 'realtime',
        run_id: 'latest.json',
      },
    })
    deleteMock.mockResolvedValueOnce({ data: { trashed_path: '/tmp/trash/latest.json' } })

    const { deleteUnifiedBenchmarkRun, getUnifiedBenchmarkRun } = await import('../src/api/benchmarks')
    await getUnifiedBenchmarkRun('agent', 'realtime', 'latest.json')
    const deleted = await deleteUnifiedBenchmarkRun('agent', 'realtime', 'latest.json')

    expect(getMock).toHaveBeenCalledWith(
      '/benchmarks/runs/agent/realtime/latest.json',
      { suppressToast: true },
    )
    expect(deleteMock).toHaveBeenCalledWith('/benchmarks/runs/agent/realtime/latest.json')
    expect(deleted.trashed_path).toContain('latest.json')
  })
})
