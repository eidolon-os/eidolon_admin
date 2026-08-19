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
  activeMatch?: RouteTarget | false
  section?: string
}

export type NavGroup = {
  id: string
  label: string
  code: string
  icon: string
  items: NavItem[]
  pinned?: boolean
  collapsible?: boolean
  defaultCollapsed?: boolean
}

export const navigation: NavGroup[] = [
  {
    id: 'control',
    label: 'Control Plane',
    code: 'CTL',
    icon: 'Connection',
    pinned: true,
    items: [
      {
        id: 'device-admission',
        label: 'Device Admission & Mount',
        hint: 'Hub → Kernel orchestration',
        icon: 'Connection',
        route: { name: 'home' },
      },
    ],
  },
  {
    id: 'services',
    label: 'Bounded Context APIs',
    code: 'API',
    icon: 'Link',
    items: [
      { id: 'agent-api', label: 'Agent API', hint: 'Agent-owned runtime/read models', icon: 'ChatLineRound', route: { name: 'feature', params: { serviceId: 'agent', feature: 'console' } } },
      { id: 'memory-api', label: 'Memory API', hint: 'Memory-owned admin contract', icon: 'Collection', route: { name: 'feature', params: { serviceId: 'memory', feature: 'console' } } },
      { id: 'hub-api', label: 'Hub API', hint: 'Device admission contract', icon: 'Monitor', route: { name: 'feature', params: { serviceId: 'hub', feature: 'console' } } },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    code: 'OPS',
    icon: 'Operation',
    collapsible: true,
    items: [
      { id: 'mission-control', label: 'Mission Control', hint: 'Read-only cockpit over the authorities', icon: 'Aim', route: { name: 'mission-control' } },
      { id: 'host-services', label: 'Host Services', hint: 'eidolond: Mac and Pi', icon: 'Cpu', route: { name: 'host-services' } },
      { id: 'supervisor', label: 'Supervisor', hint: 'macOS supervisord only', icon: 'Monitor', route: { name: 'supervisor' } },
      { id: 'configs', label: 'Service Configs', hint: 'Declared config files', icon: 'Document', route: { name: 'configs' } },
      { id: 'benchmark-agent', label: 'Benchmarks', hint: 'Diagnostic artifacts', icon: 'DataAnalysis', route: { name: 'benchmarks', params: { project: 'agent' } } },
      { id: 'firmware', label: 'Firmware & Serial', hint: 'Isolated system tool', icon: 'Tools', route: { name: 'system-firmware' } },
      { id: 'mobile', label: 'Mobile', hint: 'Isolated Android tool', icon: 'Cellphone', route: { name: 'system-mobile' } },
    ],
  },
]
