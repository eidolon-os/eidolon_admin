import { beforeEach, describe, expect, it, vi } from 'vitest'

const { post } = vi.hoisted(() => ({
  post: vi.fn(),
}))

vi.mock('../src/api/client', () => ({
  default: { post },
}))

import { wakeDevice } from '../src/api/devices'

describe('manual session intent contract', () => {
  beforeEach(() => {
    post.mockReset()
    post.mockResolvedValue({ data: {} })
  })

  it('marks the Admin start-session action as user initiated', async () => {
    await wakeDevice('mobile/device')

    expect(post).toHaveBeenCalledWith(
      '/services/hub/devices/mobile%2Fdevice/commands',
      {
        op: 'room.join',
        payload: { session_intent: 'user_initiated' },
      },
    )
  })
})
