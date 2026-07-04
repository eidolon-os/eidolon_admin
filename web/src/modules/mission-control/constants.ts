// Static registries for the cockpit: the runtime substrate (infra rail),
// its request-flow ordering, glyphs and integration-mode copy.
import type { InfraDef } from './types'

// Runtime SUBSTRATE only. client-web is an end/client product (a web body),
// not system infrastructure, so it lives with the devices — not here.
export const INFRA: InfraDef[] = [
  { id: 'hub', cn: '设备中枢', code: 'eidolon_hub', mode: 'proxy', role: '管理硬件身体的接入、发现与指令下发，签发 LiveKit 房间令牌。' },
  { id: 'channel', cn: '语音通道', code: 'eidolon_channel', mode: 'process', role: '语音转文字（STT）、文字转语音（TTS），作为 LiveKit worker 运行在语音房间里，经 gRPC 调用智能体。' },
  { id: 'agent', cn: '智能体引擎', code: 'eidolon_agent', mode: 'proxy', role: '通用推理引擎（PersonasService）：理解、规划、调用工具、生成回应。它运行每个伙伴的人格（persona / genome），伙伴的名字与身份存在 eidolon_data，不在这里。' },
  { id: 'memory', cn: '记忆服务', code: 'eidolon_memory', mode: 'native', role: '保存与召回伙伴的长期记忆，经 NATS 消费对话轮次，管理记忆空间与后台整理。' },
  { id: 'admin', cn: '控制台', code: 'eidolon_admin', mode: 'native', role: '你正在看的管理网关，聚合并转发各子项目的接口。' },
  { id: 'mementos', cn: 'Mementos', code: 'mementos', mode: 'process', role: '后台数字员工 —— 承接智能体交办的长任务并产出产物。' },
  { id: 'nats', cn: 'NATS', code: 'nats-server', mode: 'infra', role: '消息总线 / JetStream —— 各子项目之间的事件与数据流通道。' },
  { id: 'livekit', cn: 'LiveKit', code: 'livekit-server', mode: 'infra', role: '实时音视频服务器 —— 承载语音房间，Hub 与语音通道都连它。' },
]

export const SVC_GLYPH: Record<string, string> = {
  'client-web': '⌂', hub: '⎔', channel: '◍', agent: '◊', memory: '◈',
  admin: '▦', mementos: '✦', nats: '⇄', livekit: '⧉',
}

/** Substrate architecture graph: node placement (in a 0..1000 × 0..300 space). */
export const INFRA_VB = { w: 1000, h: 300 }
export interface InfraLayoutNode { id: string; x: number; y: number }
export const INFRA_LAYOUT: InfraLayoutNode[] = [
  { id: 'admin', x: 340, y: 48 },
  { id: 'hub', x: 130, y: 108 },
  { id: 'livekit', x: 130, y: 232 },
  { id: 'channel', x: 350, y: 232 },
  { id: 'agent', x: 565, y: 150 },
  { id: 'mementos', x: 565, y: 272 },
  { id: 'memory', x: 810, y: 100 },
  { id: 'nats', x: 810, y: 238 },
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
  { from: 'admin', to: 'hub', kind: 'ctrl' },
  { from: 'admin', to: 'agent', kind: 'ctrl' },
  { from: 'admin', to: 'memory', kind: 'ctrl' },
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

/** Which bus node the active turn's current stage lights up (signal flow). */
export const STAGE_SVC: Record<string, string> = {
  input: 'channel', memory_recall: 'memory', agent_turn: 'agent', tools: 'agent', memory_write: 'memory',
}

/** Numbered signal-flow annotations absorbed from the circuit-board cockpit. */
export const BUS_FLOW_STEPS = ['①身体→通道', '②输入→大脑', '③记忆召回', '④工具调用', '⑤权限校验']
