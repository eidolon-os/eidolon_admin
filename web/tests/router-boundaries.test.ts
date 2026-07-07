import { describe, expect, it } from 'vitest'
import router from '../src/router'

describe('router ownership boundaries', () => {
  // History: an earlier ownership-boundary pass removed the flat `/devices`
  // compatibility redirect. M2c (2026-07-04) reversed that call and consolidated
  // device management into a single top-level Device Center (Fleet + Firmware
  // tabs). `/devices` is now a real page — not a redirect — and both the sidebar
  // nav and the legacy `tools/esp32` deep link resolve into it. This test pins
  // that current contract so the route can't silently regress to a bare shim.
  it('/devices is the Device Center page, not a compatibility redirect', () => {
    const route = router.getRoutes().find((r) => r.name === 'devices')
    expect(route).toBeDefined()
    expect(route?.path).toBe('/devices/:tab?')
    // a real page mounts a component; a compat shim would only carry `redirect`
    expect(route?.components?.default).toBeTruthy()
    expect(route?.redirect).toBeFalsy()
  })

  it('the legacy tools/esp32 deep link redirects into the Device Center', () => {
    const esp32 = router.getRoutes().find((r) => r.path === '/tools/esp32')
    expect(esp32?.redirect).toBeTruthy()
  })
})
