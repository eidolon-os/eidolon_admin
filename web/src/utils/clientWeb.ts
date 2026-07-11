// The host-local web body runs in the standalone eidolon_client_web app. The
// admin cockpit launches it in a new tab with the body's identity in the URL
// (see the web-body plan, W3-4/W3-5). Base URL is overridable at build time via
// VITE_CLIENT_WEB_URL; the default matches the dev supervisord client-web port.
export function clientWebBase(): string {
  const env = (import.meta as any).env?.VITE_CLIENT_WEB_URL
  return (typeof env === 'string' && env) || 'http://127.0.0.1:3001'
}

/**
 * Build the launch URL for a companion's web body. The client reads these query
 * params on mount and auto-connects as that body (identity = device_id); hub
 * derives the room when it is omitted.
 */
export function webBodyLaunchUrl(params: {
  ownerId: string
  companionId: string
  deviceId: string
  room?: string
}): string {
  const q = new URLSearchParams({
    owner_id: params.ownerId,
    companion_id: params.companionId,
    device_id: params.deviceId,
  })
  if (params.room) q.set('room', params.room)
  return `${clientWebBase()}/?${q.toString()}`
}
