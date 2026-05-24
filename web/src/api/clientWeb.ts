import client from './client'

export interface DotenvEntry {
  key: string
  value: string
  masked: boolean
}

export interface DotenvResponse {
  env_file: string
  entries: DotenvEntry[]
}

export async function getClientWebConfig(): Promise<DotenvResponse> {
  const { data } = await client.get<DotenvResponse>('/client-web/config')
  return data
}
