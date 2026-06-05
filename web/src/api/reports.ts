import client from './client'

export type ReportKind = 'replay' | 'realtime'

export interface ReportSummary {
  id: string
  kind: ReportKind
  filename: string
  generated_at: string | null
  modified_at: string
  passed: boolean | null
  schema_version: string | null
  summary: Record<string, unknown>
  metrics: Record<string, unknown>
}

export interface ListReportsResponse {
  reports: ReportSummary[]
}

export interface ReportDetail {
  summary: ReportSummary
  payload: Record<string, unknown>
}

export async function listReports(kind?: ReportKind): Promise<ListReportsResponse> {
  const { data } = await client.get<ListReportsResponse>(
    '/services/agent/reports',
    { params: kind ? { kind } : undefined },
  )
  return data
}

export async function getReport(kind: ReportKind, filename: string): Promise<ReportDetail> {
  const { data } = await client.get<ReportDetail>(
    `/services/agent/reports/${kind}/${encodeURIComponent(filename)}`,
  )
  return data
}
