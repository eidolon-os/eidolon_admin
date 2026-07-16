// Sovereign-domain navigation model, extracted from AdminLayout so the IA is
// declarative and one place. The default shape is user-first: My Eidolon,
// Companions, Devices, Activity. Engineering surfaces live in Advanced.
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
  /** Use a different target for active state, or disable active state for aliases. */
  activeMatch?: RouteTarget | false
  /** Optional lightweight grouping inside a nav group, used for Advanced. */
  section?: string
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
    id: 'home',
    label: 'My Eidolon',
    code: 'ME',
    icon: 'HomeFilled',
    pinned: true,
    items: [
      {
        id: 'my-eidolon',
        label: '我的 Eidolon',
        hint: '启动 / 创建 / 修复',
        icon: 'HomeFilled',
        route: { name: 'home' },
        entity: 'owner',
      },
      {
        id: 'identity-security',
        label: '身份与安全',
        hint: 'Guard / Owner Face',
        icon: 'Lock',
        route: { name: 'identity-security' },
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
        id: 'companions',
        label: 'Companions',
        hint: '伙伴与人格',
        icon: 'Avatar',
        route: { name: 'companions' },
        entity: 'companion',
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
        label: 'Devices',
        hint: '身体与设备',
        icon: 'Monitor',
        route: { name: 'devices', params: { section: 'overview' } },
        entity: 'device',
      },
      {
        id: 'device-connect',
        label: '接入设备',
        hint: '批准 / 认领 / 绑定',
        icon: 'Connection',
        route: { name: 'devices', params: { section: 'connect' } },
        entity: 'device',
      },
    ],
  },
  {
    id: 'activity',
    label: 'Activity',
    code: 'ACT',
    icon: 'Tickets',
    items: [
      { id: 'agent-conversations', label: 'Conversations', hint: '对话轮次', icon: 'Tickets', route: { name: 'feature', params: { serviceId: 'agent', feature: 'conversations' } } },
      { id: 'hub-events', label: 'Device Events', hint: '设备事件流', icon: 'Bell', route: { name: 'feature', params: { serviceId: 'hub', feature: 'events' } } },
    ],
  },
  {
    id: 'system',
    label: 'Advanced',
    code: 'ADV',
    icon: 'Operation',
    collapsible: true,
    defaultCollapsed: true,
    items: [
      { id: 'mission-control', label: 'Mission Control', hint: '运行时驾驶舱', icon: 'Aim', section: 'Runtime', route: { name: 'mission-control' } },
      { id: 'supervisor', label: 'Supervisor', hint: '进程与健康', icon: 'Cpu', section: 'Runtime', route: { name: 'supervisor' } },
      { id: 'configs', label: 'Service Configs', hint: '运行时配置', icon: 'Document', section: 'Runtime', route: { name: 'configs' } },
      { id: 'client-web-overview', label: 'Client Web', hint: '网页端 · 状态/配置', icon: 'ChromeFilled', section: 'Runtime', route: { name: 'feature', params: { serviceId: 'client-web', feature: 'overview' } } },
      { id: 'channel-overview', label: 'Channel', hint: '语音通道 · 状态/配置', icon: 'DataLine', section: 'Runtime', route: { name: 'feature', params: { serviceId: 'channel', feature: 'overview' } } },
      { id: 'memory-items', label: 'Memory', hint: '记忆条目', icon: 'Collection', section: 'Memory', route: { name: 'feature', params: { serviceId: 'memory', feature: 'memories' } } },
      { id: 'memory-search', label: 'Memory Search', hint: '召回检索', icon: 'Search', section: 'Memory', route: { name: 'feature', params: { serviceId: 'memory', feature: 'search' } } },
      { id: 'memory-graph', label: 'Knowledge Graph', hint: '实体关系图谱', icon: 'Share', section: 'Memory', route: { name: 'feature', params: { serviceId: 'memory', feature: 'graph' } } },
      { id: 'memory-runners', label: 'Memory Runners', hint: '工作进程', icon: 'Operation', section: 'Memory', route: { name: 'feature', params: { serviceId: 'memory', feature: 'runners' } } },
      { id: 'agent-chat', label: 'Chat Test', hint: '实时请求链路', icon: 'ChatLineRound', section: 'Agent Lab', route: { name: 'feature', params: { serviceId: 'agent', feature: 'chat-test' } } },
      { id: 'agent-tasks', label: 'Long Tasks', hint: '协作任务队列', icon: 'Timer', section: 'Agent Lab', route: { name: 'feature', params: { serviceId: 'agent', feature: 'long-tasks' } } },
      { id: 'agent-reports', label: 'Replay Reports', hint: '评测产物', icon: 'DocumentChecked', section: 'Agent Lab', route: { name: 'feature', params: { serviceId: 'agent', feature: 'replay-reports' } } },
      { id: 'benchmark-agent', label: 'Benchmarks', hint: '基准产物', icon: 'DataAnalysis', section: 'Agent Lab', route: { name: 'benchmarks', params: { project: 'agent' } } },
      { id: 'hub-devices', label: 'Hub Devices', hint: '注册 / 批准 / 可达性', icon: 'Monitor', section: 'Device Infrastructure', route: { name: 'hub-devices' } },
      { id: 'hub-discovery', label: 'Hub · Discovery', hint: '附近设备', icon: 'Aim', section: 'Device Infrastructure', route: { name: 'feature', params: { serviceId: 'hub', feature: 'discovery' } } },
      { id: 'hub-commands', label: 'Hub · Commands', hint: '控制面', icon: 'Position', section: 'Device Infrastructure', route: { name: 'feature', params: { serviceId: 'hub', feature: 'commands' } } },
      { id: 'hub-metrics', label: 'Hub · Metrics', hint: '运行计数', icon: 'DataLine', section: 'Device Infrastructure', route: { name: 'feature', params: { serviceId: 'hub', feature: 'metrics' } } },
      { id: 'device-firmware', label: 'Firmware & Serial', hint: '烧录 / 串口 / 诊断', icon: 'Tools', section: 'System Tools', route: { name: 'system-firmware' } },
      { id: 'data-inspector', label: 'Data Inspector', hint: '主权域原始记录', icon: 'Grid', section: 'Data', route: { name: 'data-inspector', params: { section: 'conversations' } }, activeMatch: { name: 'data-inspector' } },
      { id: 'workspace-initialize', label: 'Workspace Initialization', hint: 'Companion / Genome / Realm 原始配置', icon: 'SetUp', section: 'Data', route: { name: 'workspace-initialize' } },
    ],
  },
]
