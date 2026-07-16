import { describe, expect, it } from 'vitest'
import router from '../src/router'
import { navigation } from '../src/layouts/navigation'

describe('router ownership boundaries', () => {
  // Product devices are Owner-scoped. Firmware, Hub infrastructure and Guard
  // security have independent routes and must not return as Device Center tabs.
  it('/devices is the Device Center page, not a compatibility redirect', () => {
    const route = router.getRoutes().find((r) => r.name === 'devices')
    expect(route).toBeDefined()
    expect(route?.path).toBe('/devices/:section?')
    // a real page mounts a component; a compat shim would only carry `redirect`
    expect(route?.components?.default).toBeTruthy()
    expect(route?.redirect).toBeFalsy()
  })

  it('keeps firmware, Hub infrastructure and Guard outside the Device Center', () => {
    const esp32 = router.getRoutes().find((r) => r.path === '/tools/esp32')
    const firmware = router.getRoutes().find((r) => r.name === 'system-firmware')
    const hub = router.getRoutes().find((r) => r.name === 'hub-devices')
    const security = router.getRoutes().find((r) => r.name === 'identity-security')
    const legacyDeviceFirmware = router.getRoutes().find((r) => r.path === '/devices/firmware')
    const legacyDeviceGuard = router.getRoutes().find((r) => r.path === '/devices/guard')

    expect(esp32?.redirect).toBeTruthy()
    expect(firmware?.path).toBe('/advanced/system/firmware')
    expect(hub?.path).toBe('/advanced/device-infrastructure/hub')
    expect(security?.path).toBe('/identity-security')
    expect(legacyDeviceFirmware?.redirect).toBeTruthy()
    expect(legacyDeviceGuard?.redirect).toBeTruthy()
  })

  it('exposes task-based Device navigation', () => {
    const navItems = navigation.flatMap((group) => group.items)
    const overview = navItems.find((item) => item.id === 'device-center')
    const connect = navItems.find((item) => item.id === 'device-connect')
    const firmware = navItems.find((item) => item.id === 'device-firmware')

    expect(overview?.route).toEqual({ name: 'devices', params: { section: 'overview' } })
    expect(connect?.route).toEqual({ name: 'devices', params: { section: 'connect' } })
    expect(firmware?.route.name).toBe('system-firmware')
  })

  it('uses My Eidolon as the only primary owner surface', () => {
    const navItems = navigation.flatMap((group) => group.items)
    expect(navItems.some((item) => item.id === 'owners')).toBe(false)
    expect(navItems.some((item) => item.id === 'my-eidolon')).toBe(true)
    expect(navItems.some((item) => item.id === 'data-inspector')).toBe(true)
    expect(navItems.some((item) => item.id === 'workspace-initialize')).toBe(true)
    expect(navItems.some((item) => item.id === 'identity-security')).toBe(true)
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
