// Motion tokens + helpers for the cockpit. One source of truth for easing,
// duration and reduced-motion so every primitive/component animates in the
// same, restrained language (A3.3). Purposeful motion only.

/** Easing curves as CSS-ready cubic-bezier strings. */
export const EASING = {
  /** Snappy, slight overshoot — for state changes / reveals. */
  spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
  /** Standard decelerate — for enters. */
  out: 'cubic-bezier(0.16, 1, 0.3, 1)',
  /** Symmetric — for hovers / toggles. */
  inOut: 'cubic-bezier(0.65, 0, 0.35, 1)',
} as const

/** Duration scale in milliseconds. */
export const DURATION = {
  fast: 160,
  base: 260,
  slow: 420,
  ambient: 900,
} as const

let _reduced: boolean | null = null

/** True when the user asked for reduced motion. Cached; safe on SSR. */
export function prefersReducedMotion(): boolean {
  if (_reduced !== null) return _reduced
  _reduced =
    typeof window !== 'undefined' &&
    !!window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return _reduced
}

/**
 * rAF tween of a numeric value. Respects reduced-motion (jumps to `to`).
 * Returns a cancel function. Used by DataNumber to animate counts/latency.
 */
export function tween(
  from: number,
  to: number,
  durationMs: number,
  onUpdate: (v: number) => void,
  ease: (t: number) => number = easeOutCubic,
): () => void {
  if (prefersReducedMotion() || durationMs <= 0 || from === to) {
    onUpdate(to)
    return () => {}
  }
  let raf = 0
  const start = performance.now()
  const delta = to - from
  const step = (nowTs: number) => {
    const t = Math.min(1, (nowTs - start) / durationMs)
    onUpdate(from + delta * ease(t))
    if (t < 1) raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
  return () => cancelAnimationFrame(raf)
}

export function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}
