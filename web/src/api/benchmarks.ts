import client from './client'

export type BenchmarkStatus = 'passed' | 'failed' | 'unknown'

export interface BenchmarkArtifact {
  name: string
  kind: 'json' | 'markdown' | 'html' | 'log' | 'directory' | 'other'
  path: string
  size: number | null
}

export interface BenchmarkRunSummary {
  project: string
  project_label: string
  suite: string
  suite_label: string
  run_id: string
  title: string
  generated_at: string | null
  modified_at: string
  status: BenchmarkStatus
  passed: boolean | null
  git_sha: string | null
  summary: Record<string, unknown>
  metrics: Record<string, unknown>
  artifacts: BenchmarkArtifact[]
  deletable: boolean
  delete_hint: string | null
}

export interface BenchmarkRunDetail extends BenchmarkRunSummary {
  cases: Array<Record<string, unknown>>
  payload: Record<string, unknown>
  markdown: string | null
}

export interface BenchmarkSuiteSummary {
  id: string
  label: string
  description: string | null
  run_count: number
  latest_status: BenchmarkStatus
  latest_modified_at: string | null
}

export interface BenchmarkProjectSummary {
  id: string
  label: string
  run_count: number
  latest_status: BenchmarkStatus
  latest_modified_at: string | null
  suites: BenchmarkSuiteSummary[]
}

export async function listBenchmarkProjects(): Promise<BenchmarkProjectSummary[]> {
  const { data } = await client.get<{ projects: BenchmarkProjectSummary[] }>(
    '/benchmarks/projects',
    { suppressToast: true },
  )
  return data.projects
}

export async function listUnifiedBenchmarkRuns(params: {
  project?: string
  suite?: string
} = {}): Promise<BenchmarkRunSummary[]> {
  const { data } = await client.get<{ runs: BenchmarkRunSummary[] }>(
    '/benchmarks/runs',
    { params, suppressToast: true },
  )
  return data.runs
}

export async function getUnifiedBenchmarkRun(
  project: string,
  suite: string,
  runId: string,
): Promise<BenchmarkRunDetail> {
  const { data } = await client.get<BenchmarkRunDetail>(
    `/benchmarks/runs/${encodeURIComponent(project)}/${encodeURIComponent(suite)}/${encodeURIComponent(runId)}`,
    { suppressToast: true },
  )
  return data
}

export async function deleteUnifiedBenchmarkRun(
  project: string,
  suite: string,
  runId: string,
): Promise<{ trashed_path: string }> {
  const { data } = await client.delete<{ trashed_path: string }>(
    `/benchmarks/runs/${encodeURIComponent(project)}/${encodeURIComponent(suite)}/${encodeURIComponent(runId)}`,
  )
  return data
}
