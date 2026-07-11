import { afterEach, describe, expect, it, vi } from 'vitest'
import { useEventStream } from '../src/components/useEventStream'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2

  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  readyState = FakeEventSource.CONNECTING
  listeners = new Map<string, Array<(event: MessageEvent) => void>>()

  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener(event: string, listener: (event: MessageEvent) => void) {
    const listeners = this.listeners.get(event) || []
    listeners.push(listener)
    this.listeners.set(event, listeners)
  }

  close() {
    this.readyState = FakeEventSource.CLOSED
  }

  emitOpen() {
    this.readyState = FakeEventSource.OPEN
    this.onopen?.(new Event('open'))
  }

  emitError() {
    this.onerror?.(new Event('error'))
  }

  emitNamed(event: string, data: string) {
    const message = new MessageEvent(event, { data })
    for (const listener of this.listeners.get(event) || []) listener(message)
  }

  emitMessage(data: string) {
    this.onmessage?.(new MessageEvent('message', { data }))
  }
}

describe('useEventStream', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    FakeEventSource.instances = []
  })

  it('tracks named SSE frames and recovers the connected state when data resumes', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const stream = useEventStream({ eventNames: ['connected', 'hub_event', 'ping'] })

    stream.open('/api/services/hub/stream/events')
    const source = FakeEventSource.instances[0]

    expect(stream.status.value).toBe('connecting')
    expect(source.url).toBe('/api/services/hub/stream/events')

    source.emitNamed('connected', '{"type":"connected"}')
    expect(stream.connected.value).toBe(true)
    expect(stream.frames.value).toHaveLength(1)
    expect(stream.frames.value[0]).toMatchObject({ event: 'connected', data: '{"type":"connected"}' })

    source.emitError()
    expect(stream.connected.value).toBe(false)
    expect(stream.status.value).toBe('reconnecting')

    source.emitNamed('hub_event', '{"type":"probe_cycle","detected":1}')
    expect(stream.connected.value).toBe(true)
    expect(stream.status.value).toBe('open')
    expect(stream.lines.value.at(-1)).toBe('{"type":"probe_cycle","detected":1}')
  })

  it('ignores late frames from a closed source after reopening', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const stream = useEventStream({ eventNames: ['hub_event'] })

    stream.open('/old')
    const oldSource = FakeEventSource.instances[0]
    stream.open('/new')
    const newSource = FakeEventSource.instances[1]

    oldSource.emitNamed('hub_event', '{"type":"old"}')
    newSource.emitNamed('hub_event', '{"type":"new"}')

    expect(stream.frames.value).toHaveLength(1)
    expect(stream.frames.value[0].data).toBe('{"type":"new"}')
  })
})
