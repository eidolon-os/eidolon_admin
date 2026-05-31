import client from './client'

export interface BenchmarkMetricStats {
  count: number
  avg: number | null
  p50: number | null
  p95: number | null
  max: number | null
}

export interface BenchmarkRunnerSummary {
  runner: string
  run: {
    run_id?: string
    git_sha?: string
    runner?: string
    profile?: string
  }
  summary: {
    total?: number
    passed?: number
    failed?: number
    pass_rate?: number
    metrics?: Record<string, BenchmarkMetricStats>
  }
  report_html: boolean
}

export interface BenchmarkRunSummary {
  run_id: string
  path: string
  modified_at: number
  dashboard_html: boolean
  dashboard_with_room_html: boolean
  runners: BenchmarkRunnerSummary[]
}

export interface BenchmarkRunsResponse {
  runs_dir: string
  runs: BenchmarkRunSummary[]
}

export interface BenchmarkCaseResult {
  case_id: string
  suite: string
  runner: string
  passed: boolean
  metrics: Record<string, unknown>
  decisions: Array<Record<string, unknown>>
  events: Array<Record<string, unknown>>
  errors: string[]
}

export interface BenchmarkMetricsPayload {
  run: Record<string, unknown>
  summary: BenchmarkRunnerSummary['summary']
  cases: BenchmarkCaseResult[]
}

export interface BenchmarkRunDetail extends BenchmarkRunSummary {
  metrics: Record<string, BenchmarkMetricsPayload>
}

export async function listBenchmarkRuns(): Promise<BenchmarkRunsResponse> {
  const { data } = await client.get<BenchmarkRunsResponse>('/channel/benchmarks/runs', {
    suppressToast: true,
  })
  return data
}

export async function getBenchmarkRun(runId: string): Promise<BenchmarkRunDetail> {
  const { data } = await client.get<BenchmarkRunDetail>(
    `/channel/benchmarks/runs/${encodeURIComponent(runId)}`,
    { suppressToast: true },
  )
  return data
}
