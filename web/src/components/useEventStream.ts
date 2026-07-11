import { computed, getCurrentScope, onScopeDispose, ref } from 'vue'

export type EventStreamStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface EventStreamFrame {
  event: string
  data: string
  receivedAt: number
}

export interface UseEventStreamOptions {
  onMessage?: (data: string, eventName: string) => void
  onFrame?: (frame: EventStreamFrame) => void
  onError?: (e: Event) => void
  /**
   * Cap the in-memory line buffer. When exceeded, the oldest lines are
   * trimmed so the DOM doesn't grow unbounded on long-running tails.
   * Default 5000; pass 0 to disable.
   */
  maxLines?: number
  /**
   * Listen to a named SSE event (`event: <name>` frames) instead of the
   * default unnamed `message` event. Mission Control emits `runtime_event`
   * frames, so it passes `eventName: 'runtime_event'`. When set, the default
   * onmessage handler is not wired.
   */
  eventName?: string
  /** Listen to several named SSE events. Takes precedence over `eventName`. */
  eventNames?: string[]
}

export function useEventStream(opts: UseEventStreamOptions = {}) {
  const status = ref<EventStreamStatus>('idle')
  const connected = computed(() => status.value === 'open')
  const lines = ref<string[]>([])
  const frames = ref<EventStreamFrame[]>([])
  const lastEventAt = ref<number | null>(null)
  const lastErrorAt = ref<number | null>(null)
  let source: EventSource | null = null
  const maxLines = opts.maxLines ?? 5000

  function open(url: string) {
    close()
    status.value = 'connecting'
    // EventSource owns the retry loop. Some endpoints support id-based resume
    // (logs), while live runtime streams are intentionally forward-only.
    const nextSource = new EventSource(url)
    source = nextSource
    source.onopen = () => {
      if (source !== nextSource) return
      status.value = 'open'
    }
    const trimBuffers = () => {
      if (maxLines <= 0) return
      if (lines.value.length > maxLines) {
        lines.value = lines.value.slice(lines.value.length - maxLines)
      }
      if (frames.value.length > maxLines) {
        frames.value = frames.value.slice(frames.value.length - maxLines)
      }
    }
    const handleFrame = (data: string, eventName = 'message') => {
      if (source !== nextSource) return
      const frame = { event: eventName, data, receivedAt: Date.now() }
      frames.value.push(frame)
      lines.value.push(data)
      lastEventAt.value = frame.receivedAt
      status.value = 'open'
      // Trim from the head when the buffer exceeds the cap. Slicing creates a
      // new array — fine for Vue reactivity, and infrequent.
      trimBuffers()
      opts.onFrame?.(frame)
      opts.onMessage?.(data, eventName)
    }
    const eventNames = opts.eventNames?.length ? opts.eventNames : opts.eventName ? [opts.eventName] : []
    if (eventNames.length) {
      for (const eventName of eventNames) {
        source.addEventListener(eventName, (e) => handleFrame((e as MessageEvent).data, eventName))
      }
    } else {
      source.onmessage = (e) => handleFrame(e.data, 'message')
    }
    source.onerror = (e) => {
      if (source !== nextSource) return
      // EventSource will auto-reconnect; we just flag "currently
      // disconnected" so the UI tag flips. Don't close() here — that
      // would prevent the browser's retry.
      lastErrorAt.value = Date.now()
      status.value = 'reconnecting'
      opts.onError?.(e)
    }
  }

  function close() {
    if (source) {
      source.close()
      source = null
    }
    status.value = 'closed'
  }

  function clear() {
    lines.value = []
    frames.value = []
    lastEventAt.value = null
    lastErrorAt.value = null
  }

  if (getCurrentScope()) {
    onScopeDispose(close)
  }

  return { connected, status, lines, frames, lastEventAt, lastErrorAt, open, close, clear }
}
