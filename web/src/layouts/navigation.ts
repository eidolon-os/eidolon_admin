// Sovereign-domain navigation model, extracted from AdminLayout so the IA is
// declarative and one place. Structure: a pinned Cockpit launcher at the top,
// entity-centric groups (Fleet / Companions / Devices / Memory / Activity), and
// a collapsed "System · Infrastructure" section holding the runtime substrate.
//
// M1 scope: items point at EXISTING routes only (Device Center temporarily
// resolves to the current hub/devices page; the consolidated center + companion
// authoring surfaces land in later milestones).

export type RouteTarget = {
  name: string
  params?: Record<string, string>
}

export type NavItem = {
  id: string
  label: string
  hint?: string
  icon: string
  route: RouteTarget
  /** Marks the entity a route represents, for active-owner/entity highlighting. */
  entity?: 'owner' | 'companion' | 'device'
}

export type NavGroup = {
  id: string
  label: string
  code: string
  icon: string
  items: NavItem[]
  /** Rendered as a single top launcher without a group header. */
  pinned?: boolean
  /** Section can be collapsed; persisted per group. */
  collapsible?: boolean
  defaultCollapsed?: boolean
}

export const navigation: NavGroup[] = [
  {
    id: 'cockpit',
    label: 'Cockpit',
    code: 'OS',
    icon: 'Aim',
    pinned: true,
    items: [
      {
        id: 'mission-control',
        label: 'Mission Control',
        hint: '运行时驾驶舱',
        icon: 'Aim',
        route: { name: 'mission-control' },
      },
    ],
  },
  {
    id: 'fleet',
    label: 'Fleet',
    code: 'OWN',
    icon: 'UserFilled',
    items: [
      {
        id: 'owners',
        label: 'Owners',
        hint: '主人 / 主权域',
        icon: 'UserFilled',
        route: { name: 'owners' },
        entity: 'owner',
      },
    ],
  },
  {
    id: 'companions',
    label: 'Companions',
    code: 'CMP',
    icon: 'Avatar',
    items: [
      {
        id: 'agent-chat',
        label: 'Chat Test',
        hint: '实时请求链路',
        icon: 'ChatLineRound',
        route: { name: 'feature', params: { serviceId: 'agent', feature: 'chat-test' } },
      },
    ],
  },
  {
    id: 'devices',
    label: 'Devices',
    code: 'DEV',
    icon: 'Monitor',
    items: [
      {
        id: 'device-center',
        label: 'Device Center',
        hint: '接入 / 绑定 / 命令',
        icon: 'Monitor',
        route: { name: 'devices', params: { tab: 'fleet' } },
        entity: 'device',
      },
      {
        id: 'device-firmware',
        label: 'Firmware Tool',
        hint: '烧录 / 串口',
        icon: 'Tools',
        route: { name: 'devices', params: { tab: 'firmware' } },
      },
    ],
  },
  {
    id: 'memory',
    label: 'Memory',
    code: 'MEM',
    icon: 'Collection',
    items: [
      { id: 'memory-items', label: 'Memories', hint: '记忆记录', icon: 'Collection', route: { name: 'feature', params: { serviceId: 'memory', feature: 'memories' } } },
      { id: 'memory-search', label: 'Search', hint: '召回检索', icon: 'Search', route: { name: 'feature', params: { serviceId: 'memory', feature: 'search' } } },
      { id: 'memory-graph', label: 'Graph', hint: '记忆宫殿图', icon: 'Share', route: { name: 'feature', params: { serviceId: 'memory', feature: 'graph' } } },
      { id: 'memory-kg', label: 'Knowledge Graph', hint: '三元组 / 事实', icon: 'Connection', route: { name: 'feature', params: { serviceId: 'memory', feature: 'kg' } } },
      { id: 'memory-runners', label: 'Runners', hint: '工作进程', icon: 'Operation', route: { name: 'feature', params: { serviceId: 'memory', feature: 'runners' } } },
      { id: 'memory-mcp', label: 'MCP Tools', hint: '工具面', icon: 'SetUp', route: { name: 'feature', params: { serviceId: 'memory', feature: 'mcp' } } },
    ],
  },
  {
    id: 'activity',
    label: 'Activity',
    code: 'ACT',
    icon: 'Tickets',
    items: [
      { id: 'agent-conversations', label: 'Conversations', hint: '对话轮次', icon: 'Tickets', route: { name: 'feature', params: { serviceId: 'agent', feature: 'conversations' } } },
      { id: 'agent-tasks', label: 'Long Tasks', hint: '协作任务队列', icon: 'Timer', route: { name: 'feature', params: { serviceId: 'agent', feature: 'long-tasks' } } },
      { id: 'agent-reports', label: 'Replay Reports', hint: '评测产物', icon: 'DocumentChecked', route: { name: 'feature', params: { serviceId: 'agent', feature: 'replay-reports' } } },
      { id: 'hub-events', label: 'Hub Events', hint: '设备事件流', icon: 'Bell', route: { name: 'feature', params: { serviceId: 'hub', feature: 'events' } } },
    ],
  },
  {
    id: 'system',
    label: 'System · Infra',
    code: 'SYS',
    icon: 'Cpu',
    collapsible: true,
    defaultCollapsed: true,
    items: [
      { id: 'supervisor', label: 'Supervisor', hint: '进程与健康', icon: 'Cpu', route: { name: 'supervisor' } },
      { id: 'configs', label: 'Service Configs', hint: '运行时配置', icon: 'Document', route: { name: 'configs' } },
      { id: 'benchmark-agent', label: 'Benchmarks', hint: '基准产物', icon: 'DataAnalysis', route: { name: 'benchmarks', params: { project: 'agent' } } },
      { id: 'hub-discovery', label: 'Hub · Discovery', hint: '附近设备', icon: 'Aim', route: { name: 'feature', params: { serviceId: 'hub', feature: 'discovery' } } },
      { id: 'hub-commands', label: 'Hub · Commands', hint: '控制面', icon: 'Position', route: { name: 'feature', params: { serviceId: 'hub', feature: 'commands' } } },
      { id: 'hub-metrics', label: 'Hub · Metrics', hint: '运行计数', icon: 'DataLine', route: { name: 'feature', params: { serviceId: 'hub', feature: 'metrics' } } },
      { id: 'channel-overview', label: 'Channel', hint: '语音通道 · 状态/配置', icon: 'DataLine', route: { name: 'feature', params: { serviceId: 'channel', feature: 'overview' } } },
      { id: 'client-web-overview', label: 'Client Web', hint: '网页端 · 状态/配置', icon: 'ChromeFilled', route: { name: 'feature', params: { serviceId: 'client-web', feature: 'overview' } } },
    ],
  },
]
