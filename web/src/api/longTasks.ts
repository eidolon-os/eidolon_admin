/**
 * Typed client for agent long-task browse endpoints.
 *
 * Requests flow through admin's gateway proxy:
 * /api/services/agent/long-tasks -> eidolon_agent /api/admin/long-tasks.
 */
import client from './client'

export interface LongTaskSummary {
  task_id: string
  provider: string
  status: string
  tenant_id: string
  user_id: string
  agent_instance_id: string | null
  conversation_id: string | null
  turn_id: string
  trace_id: string | null
  session_key: string
  task_key: string
  task_date: string
  task: string
  task_type: string
  urgency: string
  expected_output: string | null
  progress_summary: string | null
  result_text: string | null
  error_code: string | null
  error_message: string | null
  worker_id: string | null
  external_status: string | null
  mementos_session_id: string | null
  mementos_conversation_id: string | null
  created_at: string | null
  updated_at: string | null
  started_at: string | null
  submitted_at: string | null
  last_progress_at: string | null
  last_polled_at: string | null
  completed_at: string | null
}

export interface LongTaskDetail extends LongTaskSummary {
  session_id: string | null
  tool_call_id: string | null
  user_text: string | null
  context_summary: string | null
  attachments: Record<string, unknown>[]
  request_payload: Record<string, unknown>
  mementos_run_id: string | null
  mementos_latest_seq: number | null
  mementos_workspace_dir: string | null
  progress_events: Record<string, unknown>[]
  result_payload: Record<string, unknown> | null
  artifact_paths: string[]
  error_payload: Record<string, unknown> | null
  callback_subject: string | null
  callback_status: string
  callback_attempts: number
  callback_last_error: string | null
  callback_delivered_at: string | null
  lease_until: string | null
  attempt_count: number
  next_retry_at: string | null
}

export interface ListLongTasksParams {
  tenant_id?: string
  user_id?: string
  status?: string
  provider?: string
  task_type?: string
  limit?: number
  before?: string
}

export interface ListLongTasksResponse {
  tasks: LongTaskSummary[]
  next_before: string | null
}

export async function listLongTasks(
  params: ListLongTasksParams = {},
): Promise<ListLongTasksResponse> {
  const { data } = await client.get<ListLongTasksResponse>(
    '/services/agent/long-tasks',
    { params },
  )
  return data
}

export async function getLongTask(taskId: string): Promise<LongTaskDetail> {
  const { data } = await client.get<LongTaskDetail>(
    `/services/agent/long-tasks/${encodeURIComponent(taskId)}`,
  )
  return data
}
