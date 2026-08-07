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
      'mission-control',
    ]) {
      expect(names.has(removed)).toBe(false)
    }
  })

  it('links bounded contexts through public API consoles', () => {
    const items = navigation.flatMap((group) => group.items)
    expect(items.find((item) => item.id === 'agent-api')?.route.params?.feature).toBe('console')
    expect(items.find((item) => item.id === 'memory-api')?.route.params?.feature).toBe('console')
    expect(items.find((item) => item.id === 'hub-api')?.route.params?.feature).toBe('console')
  })
})
