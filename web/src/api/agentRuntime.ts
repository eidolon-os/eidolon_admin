import client from './client'

export interface PairingMemoryReadiness {
  ready: boolean
  owner_id: string
  companion_id: string
  memory_space_id: string
  memory_realm_id: string
  reason: string | null
  mcp_http_url: string | null
}

export interface PairingCode {
  code: string
  expires_at: string
  pair_url: string
  memory: PairingMemoryReadiness | null
}

export async function issuePairingCode(body: {
  owner_id: string
  companion_id: string
}): Promise<PairingCode> {
  const { data } = await client.post<PairingCode>('/services/agent/pairing/codes', body)
  return data
}

export function pairingQrUrl(code: string): string {
  return `/api/services/agent/pairing/codes/${encodeURIComponent(code)}.png`
}

export function chatTestUrl(): string {
  return '/api/services/agent/chat/test'
}
