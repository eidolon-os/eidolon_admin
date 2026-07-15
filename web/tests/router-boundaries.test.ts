import { describe, expect, it } from 'vitest'
import router from '../src/router'
import { navigation } from '../src/layouts/navigation'

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

  it('uses My Eidolon as the only primary owner surface', () => {
    const navItems = navigation.flatMap((group) => group.items)
    expect(navItems.some((item) => item.id === 'owners')).toBe(false)
    expect(navItems.some((item) => item.id === 'my-eidolon')).toBe(true)
    expect(navItems.some((item) => item.id === 'data-inspector')).toBe(true)
    expect(navItems.some((item) => item.id === 'workspace-initialize')).toBe(true)
  })

  it('keeps legacy owner links as redirects and exposes unified destinations', () => {
    const spaces = router.getRoutes().find((route) => route.name === 'spaces')
    const inspector = router.getRoutes().find((route) => route.name === 'data-inspector')
    const initializer = router.getRoutes().find((route) => route.name === 'workspace-initialize')
    const legacyList = router.getRoutes().find((route) => route.path === '/owners')
    const legacyWorkspace = router.getRoutes().find((route) => route.name === 'owner-workspace')

    expect(spaces?.path).toBe('/spaces')
    expect(inspector?.path).toBe('/advanced/data/:section?')
    expect(initializer?.path).toBe('/advanced/workspace-initialize')
    expect(legacyList?.redirect).toBeTruthy()
    expect(legacyWorkspace?.redirect).toBeTruthy()
    expect(legacyWorkspace?.components?.default).toBeFalsy()
  })
})
