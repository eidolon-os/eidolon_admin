/**
 * Display-layer formatting helpers. Lives outside ``api/`` on purpose —
 * API clients should only shape data, not presentation.
 */

/** Compact display for ISO timestamps; ``—`` for null/undefined/invalid. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString()
  } catch {
    return iso
  }
}

/**
 * Unwrap an axios/Error/anything into a human-readable string.
 *
 * Centralizes the ``e?.response?.data?.detail || e?.message || e`` pattern
 * that was duplicated across every catalog page. FastAPI puts errors under
 * ``detail``; axios wraps them under ``response.data``.
 */
export function extractErrorMessage(err: unknown): string {
  if (!err) return 'unknown error'
  if (typeof err === 'string') return err
  const anyErr = err as Record<string, any>
  const detail = anyErr?.response?.data?.detail
  if (detail) {
    if (typeof detail === 'string') return detail
    try {
      return JSON.stringify(detail)
    } catch {
      // fall through to message
    }
  }
  if (anyErr?.message) return String(anyErr.message)
  return String(err)
}
