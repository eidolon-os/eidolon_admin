import client from './client'

export interface ChannelConfigEntry {
  key: string
  value: string
  masked: boolean
}

export interface ChannelConfigResponse {
  env_file: string
  entries: ChannelConfigEntry[]
}

export async function getChannelConfig(): Promise<ChannelConfigResponse> {
  const { data } = await client.get<ChannelConfigResponse>('/channel/config')
  return data
}
