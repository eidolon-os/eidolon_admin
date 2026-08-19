import { describe, expect, it } from 'vitest'
import router from '../src/router'
import { navigation } from '../src/layouts/navigation'

describe('Data V2 / Kernel control-plane routes', () => {
  it('uses the Hub to Kernel workflow as the only domain home', () => {
    const home = router.getRoutes().find((route) => route.name === 'home')
    expect(home?.path).toBe('/')
    expect(home?.components?.default).toBeTruthy()
  })

  it('does not expose removed cross-database product routes', () => {
    const names = new Set(router.getRoutes().map((route) => route.name))
    for (const removed of [
      'spaces',
      'owner-workspace',
      'data-inspector',
      'workspace-initialize',
      'companions',
      'identity-security',
      'devices',
      'device-detail',
      'hub-devices',
    ]) {
      expect(names.has(removed)).toBe(false)
    }
  })

  it('reaches the cockpit, which owns nothing and only composes', () => {
    // The one name that left the list above. Every other entry is a surface
    // for data another component is now the authority for; serving those here
    // would give a Host two answers to one question. Mission Control is a
    // view — it was removed because its server half opened the database, and
    // that half now goes through the same HTTP clients as everything else.
    const names = new Set(router.getRoutes().map((route) => route.name))
    expect(names.has('mission-control')).toBe(true)
  })

  it('reaches Host services through eidolond rather than a platform-locked page', () => {
    const items = navigation.flatMap((group) => group.items)
    const hostServices = items.find((item) => item.id === 'host-services')
    expect(hostServices?.route.name).toBe('host-services')
    // The supervisord console stays, but must not claim to cover every Host.
    expect(items.find((item) => item.id === 'supervisor')?.hint).toMatch(/macOS/)
  })

  it('links bounded contexts through public API consoles', () => {
    const items = navigation.flatMap((group) => group.items)
    expect(items.find((item) => item.id === 'agent-api')?.route.params?.feature).toBe('console')
    expect(items.find((item) => item.id === 'memory-api')?.route.params?.feature).toBe('console')
    expect(items.find((item) => item.id === 'hub-api')?.route.params?.feature).toBe('console')
  })
})

describe('nothing is reachable only by typing its URL', () => {
  it('offers Mission Control in the navigation, not just in the route table', () => {
    // A route with nothing linking to it is the same fault as a module with
    // no route: present, working, and invisible. This restore produced both
    // in turn before it produced neither.
    const routed = new Set(
      navigation.flatMap((group) => group.items.map((item) => item.route?.name)),
    )
    expect(routed.has('mission-control')).toBe(true)
  })
})
