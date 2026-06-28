import { describe, expect, it } from 'vitest'
import router from '../src/router'

describe('router ownership boundaries', () => {
  it('/devices no longer exposes a compatibility redirect', () => {
    const route = router.getRoutes().find((r) => r.name === 'devices')

    expect(route).toBeUndefined()
  })
})
