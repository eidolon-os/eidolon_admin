import { onBeforeUnmount, ref } from 'vue'

export interface UseEventStreamOptions {
  onMessage?: (data: string) => void
  onError?: (e: Event) => void
}

export function useEventStream(opts: UseEventStreamOptions = {}) {
  const connected = ref(false)
  const lines = ref<string[]>([])
  let source: EventSource | null = null

  function open(url: string) {
    close()
    source = new EventSource(url)
    source.onopen = () => {
      connected.value = true
    }
    source.onmessage = (e) => {
      lines.value.push(e.data)
      opts.onMessage?.(e.data)
    }
    source.onerror = (e) => {
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
