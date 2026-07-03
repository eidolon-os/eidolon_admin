import { onBeforeUnmount, ref } from 'vue'

export interface UseEventStreamOptions {
  onMessage?: (data: string) => void
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
}

export function useEventStream(opts: UseEventStreamOptions = {}) {
  const connected = ref(false)
  const lines = ref<string[]>([])
  let source: EventSource | null = null
  const maxLines = opts.maxLines ?? 5000

  function open(url: string) {
    close()
    // EventSource auto-resumes via Last-Event-ID on reconnect (browser
    // built-in). The backend honors that header and skips re-seeding the
    // tail, so reconnects don't duplicate lines — provided the server
    // emits `id:` fields, which our supervisor/logs endpoint does.
    source = new EventSource(url)
    source.onopen = () => {
      connected.value = true
    }
    const handleFrame = (data: string) => {
      lines.value.push(data)
      // Trim from the head when the buffer exceeds the cap. Slicing
      // creates a new array — fine for Vue reactivity, and infrequent
      // (only fires once we cross the threshold).
      if (maxLines > 0 && lines.value.length > maxLines) {
        lines.value = lines.value.slice(lines.value.length - maxLines)
      }
      opts.onMessage?.(data)
    }
    if (opts.eventName) {
      source.addEventListener(opts.eventName, (e) => handleFrame((e as MessageEvent).data))
    } else {
      source.onmessage = (e) => handleFrame(e.data)
    }
    source.onerror = (e) => {
      // EventSource will auto-reconnect; we just flag "currently
      // disconnected" so the UI tag flips. Don't close() here — that
      // would prevent the browser's retry.
      connected.value = false
      opts.onError?.(e)
    }
  }

  function close() {
    if (source) {
      source.close()
      source = null
    }
    connected.value = false
  }

  function clear() {
    lines.value = []
  }

  onBeforeUnmount(close)

  return { connected, lines, open, close, clear }
}
