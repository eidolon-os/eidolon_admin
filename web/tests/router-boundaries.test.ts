import { describe, expect, it } from 'vitest'
import router from '../src/router'

describe('router ownership boundaries', () => {
  it('/devices keeps a compatibility redirect to the single Hub devices entry', () => {
    const route = router.getRoutes().find((r) => r.name === 'devices')

    expect(route).toBeTruthy()
    expect(route?.path).toBe('/devices')
    expect(route?.redirect).toEqual({
      name: 'feature',
      params: { serviceId: 'hub', feature: 'devices' },
    })
  })
})
