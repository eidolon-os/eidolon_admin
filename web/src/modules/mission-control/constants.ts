// Static registries for the cockpit: the runtime substrate (infra rail),
// its request-flow ordering, glyphs and integration-mode copy.
import type { InfraDef } from './types'

// Runtime SUBSTRATE only. Deliberately excludes:
//   - client-web — an end/client product (a web body), lives with devices.
//   - admin — the console / control plane you're viewing through, not a
//     backing service companions run on.
export const INFRA: InfraDef[] = [
  { id: 'hub', cn: '设备中枢', code: 'eidolon_hub', mode: 'proxy', tier: 'service', role: '管理硬件身体的接入、发现与指令下发，签发 LiveKit 房间令牌。' },
  { id: 'channel', cn: '语音通道', code: 'eidolon_channel', mode: 'process', tier: 'service', role: '语音转文字（STT）、文字转语音（TTS），作为 LiveKit worker 运行在语音房间里，经 gRPC 调用智能体。' },
  { id: 'agent', cn: '智能体引擎', code: 'eidolon_agent', mode: 'proxy', tier: 'service', role: '通用推理引擎（PersonasService）：理解、规划、调用工具、生成回应。它运行每个伙伴的人格（persona / genome），伙伴的名字与身份存在 eidolon_data，不在这里。' },
  { id: 'memory', cn: '记忆服务', code: 'eidolon_memory', mode: 'native', tier: 'service', role: '保存与召回伙伴的长期记忆，经 NATS 消费对话轮次，管理记忆空间与后台整理。' },
  { id: 'livekit', cn: 'LiveKit', code: 'livekit-server', mode: 'infra', tier: 'middleware', role: '实时音视频服务器 —— 承载语音房间，Hub 与语音通道都连它。' },
  { id: 'nats', cn: 'NATS', code: 'nats-server', mode: 'infra', tier: 'middleware', role: '消息总线 / JetStream —— 各子项目之间的事件与数据流通道。' },
  { id: 'mementos', cn: 'Mementos', code: 'mementos', mode: 'process', tier: 'external', role: '后台数字员工 —— 承接智能体交办的长任务并产出产物（外挂扩展，非核心链路）。' },
]

export const SVC_GLYPH: Record<string, string> = {
  'client-web': '⌂', hub: '⎔', channel: '◍', agent: '◊', memory: '◈',
  admin: '▦', mementos: '✦', nats: '⇄', livekit: '⧉',
}

/** Substrate architecture graph: node placement (in a 0..1000 × 0..300 space).
 * Laid out in three horizontal tiers so the hierarchy reads at a glance:
 * 业务组件 (top) → 中间件 (middle) → 外挂 (bottom). Edges cross tiers to show
 * real dependencies. */
export const INFRA_VB = { w: 1000, h: 300 }
export interface InfraLayoutNode { id: string; x: number; y: number }
export const INFRA_LAYOUT: InfraLayoutNode[] = [
  // 业务组件 — the request spine
  { id: 'hub', x: 130, y: 80 },
  { id: 'channel', x: 355, y: 80 },
  { id: 'agent', x: 580, y: 80 },
  { id: 'memory', x: 800, y: 80 },
  // 中间件
  { id: 'livekit', x: 250, y: 192 },
  { id: 'nats', x: 660, y: 192 },
  // 外挂 · 扩展
  { id: 'mementos', x: 555, y: 262 },
]

export interface TierBand { tier: 'service' | 'middleware' | 'external'; label: string; y0: number; y1: number }
/** Horizontal tier bands (in INFRA_VB coords) for background zones + captions. */
export const TIER_BANDS: TierBand[] = [
  { tier: 'service', label: '业务组件', y0: 8, y1: 138 },
  { tier: 'middleware', label: '中间件', y0: 148, y1: 222 },
  { tier: 'external', label: '外挂 · 扩展', y0: 232, y1: 292 },
]

export type InfraEdgeKind = 'rtc' | 'grpc' | 'nats' | 'task' | 'ctrl'
export interface InfraEdge { from: string; to: string; kind: InfraEdgeKind; spine?: boolean }
/** Edges reflect real relationships (see reverse-engineered topology). */
export const INFRA_EDGES: InfraEdge[] = [
  { from: 'hub', to: 'livekit', kind: 'rtc', spine: true },
  { from: 'livekit', to: 'channel', kind: 'rtc', spine: true },
  { from: 'channel', to: 'agent', kind: 'grpc', spine: true },
  { from: 'agent', to: 'memory', kind: 'nats', spine: true },
  { from: 'agent', to: 'nats', kind: 'nats' },
  { from: 'memory', to: 'nats', kind: 'nats' },
  { from: 'hub', to: 'nats', kind: 'nats' },
  { from: 'agent', to: 'mementos', kind: 'task' },
]
export const EDGE_LABEL: Record<InfraEdgeKind, string> = {
  rtc: 'RTC', grpc: 'gRPC', nats: 'NATS', task: 'task', ctrl: 'ctrl',
}

export const MODE_CN: Record<string, string> = {
  native: '内建', proxy: '代理', process: '托管', device: '设备', infra: '基础',
}
export const MODE_EXP: Record<string, string> = {
  native: '管理接口内建在网关里',
  proxy: '透明转发到子项目自己的接口',
  process: '由 supervisord 托管，只读状态',
  device: '硬件入口，经 Hub 接入',
  infra: '共享基础设施，由 supervisord 托管',
}

/** Map legacy voice-stage keys to substrate nodes for voice-detail fallback. */
export const STAGE_SVC: Record<string, string> = {
  input: 'channel', speech: 'channel', duck: 'channel', eot: 'channel',
  commit: 'agent', agent_turn: 'agent', brain: 'agent', response: 'agent',
  tts: 'channel', playback: 'channel',
  memory_recall: 'memory', tools: 'agent', memory_write: 'memory',
}

/** Request-spine order (hub→…→memory). The wavefront flows only the edges whose
 * target the current stage's service has reached, so the signal reads as
 * travelling to where it is now — not the whole spine lighting at once. */
export const SPINE_ORDER = ['hub', 'livekit', 'channel', 'agent', 'memory']

/** Numbered signal-flow annotations absorbed from the circuit-board cockpit. */
export const BUS_FLOW_STEPS = ['①身体→通道', '②输入→大脑', '③记忆召回', '④工具调用', '⑤权限校验']
