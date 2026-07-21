import type { CompanionFaceView } from '@/api/eidolonData'

/**
 * Presentation model for a companion's offline idle-loop generation, derived
 * purely from a {@link CompanionFaceView}. Keeping this pure keeps the detail
 * view dumb (it just renders the model) and the state logic exhaustively
 * unit-testable across every lifecycle value.
 *
 * Lifecycle: none → pending → generating → ready | failed.
 */
export type IdleTone = 'ready' | 'progress' | 'failed' | 'idle'

export interface IdleFacePresentation {
  /** Raw idle_status, for keys/debug. */
  status: string
  /** Short human label (中文) for the status tag. */
  label: string
  /** Maps to an el-tag type. */
  tone: IdleTone
  /** Generation in flight → show a spinner and poll for completion. */
  generating: boolean
  /** A playable clip exists → show the loop preview. */
  ready: boolean
  /** Regeneration is a sensible action right now (not mid-generation). */
  canRegenerate: boolean
  /** One-line explanation shown under the tag. */
  hint: string
}

export function describeIdleFace(
  face: CompanionFaceView | null,
): IdleFacePresentation | null {
  if (!face) return null
  const status = face.idle_status || 'none'
  switch (status) {
    case 'ready':
      return {
        status,
        label: '已就绪',
        tone: 'ready',
        generating: false,
        ready: face.idle_ready === true,
        canRegenerate: true,
        hint: '静息时会循环播放这段 idle 动画。',
      }
    case 'pending':
    case 'generating':
      return {
        status,
        label: status === 'pending' ? '排队中' : '生成中',
        tone: 'progress',
        generating: true,
        ready: false,
        canRegenerate: false,
        hint: '正在离线生成 idle 动画，就绪后会自动显示。',
      }
    case 'failed':
      return {
        status,
        label: '生成失败',
        tone: 'failed',
        generating: false,
        ready: false,
        canRegenerate: true,
        hint: face.idle_error ? `生成失败：${face.idle_error}` : '生成失败，可重试。',
      }
    default:
      return {
        status: 'none',
        label: '未生成',
        tone: 'idle',
        generating: false,
        ready: false,
        canRegenerate: true,
        hint: '未生成 idle 动画时，静息回退到静态微动 / 粒子头。',
      }
  }
}

/** el-tag ``type`` for an idle tone. */
export function idleTagType(tone: IdleTone): 'success' | 'warning' | 'danger' | 'info' {
  switch (tone) {
    case 'ready':
      return 'success'
    case 'progress':
      return 'warning'
    case 'failed':
      return 'danger'
    default:
      return 'info'
  }
}
