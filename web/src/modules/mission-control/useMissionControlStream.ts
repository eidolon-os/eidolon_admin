// The single owner of cockpit side-effects and derived state. Both former
// SFCs (MissionControl.vue + MissionControlCyber.vue) each re-implemented this
// lifecycle; it now lives here once so every view component is thin/pure.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listOwners, type OwnerView } from '@/api/eidolonData'
import {
  getMissionControlSnapshot,
  missionControlEventsUrl,
  type RuntimeActivity,
  type RuntimeCompanion,
  type RuntimeEvent,
  type RuntimeSnapshot,
  type RuntimeService,
  type RuntimeTurn,
} from '@/api/missionControl'
import { useEventStream } from '@/components/useEventStream'
import {
  demoFlowDevice,
  demoFlowTurn,
  eventBelongsToTurn,
  eventToPulse,
  eventTone,
  EVENT_PULSE_MS,
  isDemoFlowTarget,
  pulseInScope,
  pulseThrottled,
  type FlowLeg,
  type PulseScope,
  type PulseTone,
} from './flow'
import { INFRA, SVC_GLYPH } from './constants'
import { activityServiceId, activitySortTime, isActiveActivity, traceSpansForTurn } from './activity'
import { deviceShort, fmtClock, fmtLatency, privacyModeLabel, streamLabel, systemStateLabel } from './format'
import type { CompanionUnit, InfraNode, StreamState } from './types'

const POLL_MS = 8000
const ACTIVE_STATES = ['running', 'active', 'pending', 'queued', 'processing', 'generating', 'speaking', 'deferred']

export interface MissionControlMode {
  /** 'replay' streams recorded fixtures (M4); 'live' is the default. */
  mode?: 'live' | 'replay'
  /** Initial owner selected by the caller, usually from My Eidolon. */
  ownerId?: string
  /**
   * DEV-only visual hook: id of a companion to overlay a synthetic voice activity
   * onto (or '' for the first companion) so internal circulation can be
   * seen without staging a real agent turn. Ignored outside `import.meta.env.DEV`.
   */
  demoFlow?: string
  /**
   * Directed event pulses overlay one-shot darts from the live event stream on
   * top of activity circulation. On by default; pass false to disable.
   */
  flowEvents?: boolean
  /**
   * Which companions' events emit darts: 'all' by default, or 'focused' for a
   * deliberately quiet single-companion view.
   */
  flowEventsScope?: PulseScope
}

/** A transient pulse: a companion leg lit briefly by one observed event. */
export interface FlowPulse {
  id: string
  companionId: string
  leg: FlowLeg
  dir: 'in' | 'out'
  tone: PulseTone
  deviceId?: string
}

function demoActivity(turn: RuntimeTurn): RuntimeActivity {
  return {
    activity_id: `demo:${turn.turn_id}`,
    kind: 'voice_turn', owner_id: turn.owner_id, companion_id: turn.companion_id,
    trace_id: turn.trace_id, turn_id: turn.turn_id, job_id: null,
    origin_device_id: turn.device_id, target_device_ids: turn.device_id ? [turn.device_id] : [],
    status: turn.status, outcome: turn.outcome, summary: 'DEV visual flow',
    current_hop_id: 'demo:agent', started_at: turn.started_at, updated_at: turn.started_at,
    finished_at: null, event_ids: [],
    route: [{
      hop_id: 'demo:agent', node_type: 'service', node_id: 'agent', label: 'Demo agent',
      stage: 'agent_turn', status: 'running', direction: 'internal', ts: null, latency_ms: null,
    }],
  }
}

export function useMissionControlStream(opts: MissionControlMode = {}) {
  // Gate the demo hook to dev builds so a stray `?demoFlow` can never fabricate
  // runtime activity in production.
  const demoFlow = import.meta.env.DEV ? opts.demoFlow : undefined
  // Directed pulses are on by default for every companion; `?flow2=off`
  // opts it out. No longer dev-gated — it only reacts to real events.
  const flowEventsEnabled = opts.flowEvents ?? true
  const pulseScope: PulseScope = opts.flowEventsScope ?? 'all'
  const owners = ref<OwnerView[]>([])
  const ownerId = ref(opts.ownerId || '')
  // A secondary selection layered on the owner scope: when set, companion-scoped
  // modules (live trace, evidence lanes, event flow) re-scope to this companion.
  const focusedCompanionId = ref('')
  const selectedTurnId = ref('')
  const selectedEventId = ref('')
  const hoveredEventId = ref('')
  const snapshot = ref<RuntimeSnapshot | null>(null)
  const liveEvents = ref<RuntimeEvent[]>([])
  // Transient directed pulses currently in flight (auto-expire).
  const activePulses = ref<FlowPulse[]>([])
  const loading = ref(false)
  const error = ref('')
  const streamState = ref<StreamState>('connecting')
  const now = ref(Date.now())
  const replay = opts.mode === 'replay'

  let pollTimer: number | undefined
  let clockTimer: number | undefined
  let channelRefreshTimer: number | undefined

  // ── primitives off the snapshot ──────────────────────────────────────
  const experience = computed(() => snapshot.value?.experience)
  const memory = computed(() => snapshot.value?.memory)
  const devices = computed(() => snapshot.value?.devices || [])
  const runtimeBlackboard = computed(() => snapshot.value?.runtime_blackboard)
  const services = computed(() => snapshot.value?.services || [])
  const jobs = computed(() => snapshot.value?.jobs || [])
  const activities = computed(() => snapshot.value?.activities || [])
  const activeActivities = computed(() => activities.value.filter(isActiveActivity))
  const companions = computed<RuntimeCompanion[]>(() => {
    const list = snapshot.value?.companions || []
    return list.length ? list : snapshot.value?.companion ? [snapshot.value.companion] : []
  })
  const companionNames = computed<Record<string, string>>(() => Object.fromEntries(
    companions.value.map((companion) => [companion.companion_id, companion.display_name || companion.companion_id]),
  ))
  const deviceNames = computed<Record<string, string>>(() => Object.fromEntries(
    devices.value.map((device) => [device.device_id, deviceShort(device)]),
  ))

  const ownerName = computed(
    () => snapshot.value?.owner?.display_name || snapshot.value?.owner?.owner_id || '未选择主人',
  )
  const onlineDevices = computed(() => devices.value.filter((d) => d.online).length)
  const onlineServices = computed(() => services.value.filter((s) => s.online).length)
  const activeJobs = computed(
    () => jobs.value.filter((j) => ACTIVE_STATES.includes((j.status || '').toLowerCase())).length,
  )
  const degradedSources = computed(() => (snapshot.value?.source_status || []).filter((s) => !s.ok))
  const runtimeTurns = computed(() => snapshot.value?.recent_turns || [])
  const selectedTurn = computed<RuntimeTurn | null>(() =>
    selectedTurnId.value
      ? runtimeTurns.value.find((turn) => turn.turn_id === selectedTurnId.value) || null
      : null,
  )
  const primaryActiveVoiceTurn = computed<RuntimeTurn | null>(() => {
    const voice = activeActivities.value.find((activity) => activity.kind === 'voice_turn' && activity.turn_id)
    return voice ? runtimeTurns.value.find((turn) => turn.turn_id === voice.turn_id) || null : null
  })
  const pipelineActive = computed(() => activeActivities.value.length > 0)
  const primaryCompanionId = computed(() => snapshot.value?.companion?.companion_id || '')
  const privacyMode = computed(() =>
    privacyModeLabel(memory.value?.privacy_mode || primaryActiveVoiceTurn.value?.privacy_mode || 'safe'),
  )
  const deviceRatio = computed(() =>
    devices.value.length ? Math.round((onlineDevices.value / devices.value.length) * 100) : 0,
  )

  const recentEvents = computed(() => {
    const merged = [...liveEvents.value, ...(snapshot.value?.recent_events || [])]
    const seen = new Set<string>()
    return merged.filter((e) => (seen.has(e.event_id) ? false : (seen.add(e.event_id), true))).slice(0, 120)
  })
  const highlightedEvent = computed<RuntimeEvent | null>(() => {
    const eventId = hoveredEventId.value || selectedEventId.value
    return eventId ? recentEvents.value.find((event) => event.event_id === eventId) || null : null
  })
  const traceId = computed(() =>
    (selectedTurn.value?.trace_id || activeActivities.value[0]?.trace_id || activeActivities.value[0]?.activity_id || snapshot.value?.owner?.owner_id || 'STANDBY').slice(0, 14).toUpperCase(),
  )
  const evidenceChains = computed(() => snapshot.value?.evidence_chains || [])
  const permissionLedger = computed(() => snapshot.value?.permission_ledger || [])
  const demoMode = computed(() => snapshot.value?.demo_mode || 'live')

  function svc(id: string): RuntimeService | null {
    return services.value.find((s) => s.service_id === id) || null
  }

  // ── companions projected into the sovereign-domain view ───────────────
  const companionUnits = computed<CompanionUnit[]>(() => {
    const m = memory.value
    const activeRealm = m?.active_realm_id
    return companions.value.map((c, i) => {
      let devs = devices.value.filter((d) => d.companion_id === c.companion_id)
      const cJobs = jobs.value.filter((j) => j.companion_id === c.companion_id)
      const cTurns = runtimeTurns.value.filter((turn) => turn.companion_id === c.companion_id)
      const cActivities = activities.value
        .filter((activity) => activity.companion_id === c.companion_id)
        .sort((a, b) => activitySortTime(b) - activitySortTime(a))
      let activeActivity = cActivities.find(isActiveActivity) || null
      let active = activeActivity?.turn_id
        ? cTurns.find((turn) => turn.turn_id === activeActivity?.turn_id) || null
        : null
      let turn = selectedTurn.value?.companion_id === c.companion_id ? selectedTurn.value : active
      // DEV-only: overlay a synthetic turn + online body on the demo target so
      // both flow legs light. Purely presentational — never touches the wire.
      if (isDemoFlowTarget(c.companion_id, i, demoFlow)) {
        if (!turn) turn = demoFlowTurn(c.companion_id || 'demo')
        if (!active) active = turn
        if (!activeActivity) activeActivity = demoActivity(turn)
        if (!devs.some((d) => d.online))
          devs = devs.length
            ? devs.map((d, di) => (di === 0 ? { ...d, online: true } : d))
            : [demoFlowDevice(c.companion_id || 'demo')]
      }
      const isActiveRealm = !!c.memory_realm_id && c.memory_realm_id === activeRealm
      return {
        id: c.companion_id || 'unknown',
        name: c.display_name || c.companion_id || '未命名伙伴',
        kind: c.kind || 'companion',
        status: c.status || 'idle',
        genome: c.genome_id || '',
        realm: c.memory_realm_id || '',
        isActiveRealm,
        recall: isActiveRealm ? m?.last_recall_hits ?? 0 : null,
        runners: isActiveRealm ? `${m?.runners_online ?? 0}/${m?.runners_total ?? 0}` : '',
        write: isActiveRealm ? (m?.fanout_allowed ? m?.last_write_disposition || 'ALLOW' : 'HOLD') : '',
        devices: devs,
        activities: cActivities,
        activeActivity,
        activeVoiceTurn: active,
        turn,
        turns: cTurns,
        jobs: cJobs,
        // The master companion is authoritative when the snapshot carries the
        // flag; fall back to the "default companion" heuristic otherwise.
        isPrimary: c.is_master || c.companion_id === primaryCompanionId.value,
      }
    })
  })
  const boundIds = computed(() => new Set(companions.value.map((c) => c.companion_id)))
  const unboundDevices = computed(() =>
    devices.value.filter((d) => !d.companion_id || !boundIds.value.has(d.companion_id)),
  )

  // Translate the newest live event into a one-shot directed pulse. The default
  // owner view includes every companion; callers may request focused scope.
  // Dedup by event_id so the same head event
  // never fires twice;
  // per-leg throttle (§9 flood control) — except 'bad' tones, which always show
  // so failures are never swallowed; self-expire after the dart crosses.
  const pulseTimers = new Set<number>()
  const lastLegEmit = new Map<string, number>() // `cid:leg` → last emit ms
  let lastPulseEventId = ''
  let pulseSeq = 0
  if (flowEventsEnabled) {
    watch(liveEvents, (events) => {
      const e = events[0]
      if (!e || e.event_id === lastPulseEventId) return
      lastPulseEventId = e.event_id
      const cid = e.companion_id
      if (!cid || !pulseInScope(cid, focusedCompanionId.value, pulseScope)) return
      const p = eventToPulse(e)
      if (!p) return
      const tone = eventTone(e.severity, e.outcome)
      const key = `${cid}:${p.leg}`
      const nowMs = Date.now()
      if (tone !== 'bad' && pulseThrottled(lastLegEmit.get(key) ?? 0, nowMs)) return
      lastLegEmit.set(key, nowMs)
      const id = `fp-${pulseSeq++}`
      activePulses.value = [...activePulses.value, {
        id, companionId: cid, leg: p.leg, dir: p.dir, tone,
        deviceId: e.device_id || undefined,
      }].slice(-12)
      const timer = window.setTimeout(() => {
        activePulses.value = activePulses.value.filter((x) => x.id !== id)
        pulseTimers.delete(timer)
      }, EVENT_PULSE_MS + 120)
      pulseTimers.add(timer)
    })
  }

  // DEV-only: once companions load, auto-focus the ?demoFlow target so the
  // circulation shows without a click. Applies once, then the user is free to
  // click elsewhere.
  if (demoFlow !== undefined) {
    let applied = false
    watch(
      companions,
      (list) => {
        if (applied || !list.length) return
        const target = demoFlow || list[0]?.companion_id || ''
        if (target) {
          focusedCompanionId.value = target
          applied = true
        }
      },
      { immediate: true },
    )
  }

  // ── focused-companion scope (owner is the page scope; a companion is a focus) ──
  const focusedCompanion = computed<CompanionUnit | null>(
    () => companionUnits.value.find((c) => c.id === focusedCompanionId.value) || null,
  )
  const focusedDeviceIds = computed(
    () => new Set((focusedCompanion.value?.devices || []).map((d) => d.device_id)),
  )
  // Voice detail follows the selected/focused voice turn only. It is not a
  // snapshot-wide playhead for Guard, device, or background activities.
  const scopedTurn = computed<RuntimeTurn | null>(() => {
    const f = focusedCompanion.value
    if (selectedTurn.value && (!f || selectedTurn.value.companion_id === f.id)) return selectedTurn.value
    if (!f) return primaryActiveVoiceTurn.value
    return f.activeVoiceTurn || f.turns[0] || null
  })
  const traceSpans = computed(() => traceSpansForTurn(snapshot.value?.trace_spans || [], scopedTurn.value))
  const scopedActivities = computed(() => {
    const scoped = focusedCompanion.value
      ? activities.value.filter((activity) => activity.companion_id === focusedCompanion.value!.id)
      : activities.value
    const active = scoped.filter(isActiveActivity)
    return (active.length ? active : scoped.slice(0, 4)).sort((a, b) => activitySortTime(b) - activitySortTime(a))
  })
  const scopedJobs = computed(() => (focusedCompanion.value ? focusedCompanion.value.jobs : jobs.value))
  // permission_ledger carries device_id (no companion_id) — scope via device→companion.
  const scopedPermissions = computed(() => {
    const f = focusedCompanion.value
    if (!f) return permissionLedger.value
    const ids = focusedDeviceIds.value
    return permissionLedger.value.filter((p) => p.device_id && ids.has(p.device_id))
  })
  // Per-companion event stream. Events already carry companion_id + device_id +
  // turn_id, so this doubles as the hook for the future device→agent→return flow.
  const companionEvents = computed(() => {
    if (selectedTurn.value) {
      return recentEvents.value.filter((event) => eventBelongsToTurn(event, selectedTurn.value!))
    }
    return focusedCompanion.value
      ? recentEvents.value.filter((event) => event.companion_id === focusedCompanion.value!.id)
      : recentEvents.value
  })

  function selectTurn(turnId: string, companionId?: string) {
    selectedTurnId.value = turnId
    selectedEventId.value = ''
    if (companionId) focusedCompanionId.value = companionId
  }

  function followLive() {
    selectedTurnId.value = ''
    selectedEventId.value = ''
  }

  function selectEvent(event: RuntimeEvent) {
    selectedEventId.value = event.event_id
    const linkedTurn = runtimeTurns.value.find((turn) => eventBelongsToTurn(event, turn))
    const turnId = String(event.payload?.channel_turn_id || linkedTurn?.turn_id || event.turn_id || '')
    if (turnId) selectTurn(turnId, event.companion_id || undefined)
    else if (event.companion_id) focusedCompanionId.value = event.companion_id
    selectedEventId.value = event.event_id
  }

  function hoverEvent(event: RuntimeEvent | null) {
    hoveredEventId.value = event?.event_id || ''
  }

  // ── infra rail (runtime substrate, demoted) ──────────────────────────
  const infraNodes = computed<InfraNode[]>(() =>
    INFRA.map((f) => {
      const s = svc(f.id)
      const online = !!s?.online
      const checked = !!s?.checked
      const state = online ? 'online' : checked ? 'offline' : 'unknown'
      const stateCn = online ? '在线' : checked ? '离线' : '未探测'
      return {
        ...f,
        glyph: SVC_GLYPH[f.id] || '◆',
        online,
        checked,
        state,
        stateCn,
        latency: fmtLatency(s?.latency_ms),
        detail: s?.detail || '',
        events: recentEvents.value.filter((e) => e.source === f.id).slice(0, 3),
      }
    }),
  )
  // Concurrent observer lanes may have different playheads on the substrate.
  const serviceActivityOwners = computed<Record<string, string[]>>(() => {
    const ownersByService: Record<string, string[]> = {}
    for (const activity of activeActivities.value) {
      const serviceId = activityServiceId(activity)
      if (!serviceId) continue
      const companion = companionUnits.value.find((item) => item.id === activity.companion_id)
      const label = companion?.name || activity.companion_id || activity.kind
      const labels = ownersByService[serviceId] || (ownersByService[serviceId] = [])
      if (!labels.includes(label)) labels.push(label)
    }
    return ownersByService
  })
  const hotServices = computed(() => Object.keys(serviceActivityOwners.value))

  // ── header chrome ────────────────────────────────────────────────────
  const clock = computed(() => fmtClock(now.value))
  const streamLabelText = computed(() => streamLabel(streamState.value))
  const systemStateText = computed(() => systemStateLabel(experience.value?.system_state))

  // ── data flow ────────────────────────────────────────────────────────
  const es = useEventStream({
    eventName: 'runtime_event',
    onMessage: (data) => {
      try {
        const event = JSON.parse(data) as RuntimeEvent
        liveEvents.value = [event, ...liveEvents.value].slice(0, 80)
        streamState.value = event.severity === 'warn' && event.type.includes('degraded') ? 'degraded' : 'live'
        if (event.source === 'hub' || event.source === 'mission_control') void refresh()
        if (event.source === 'channel') {
          if (channelRefreshTimer) window.clearTimeout(channelRefreshTimer)
          channelRefreshTimer = window.setTimeout(() => void refresh(), 180)
        }
      } catch {
        streamState.value = 'degraded'
      }
    },
    onError: () => (streamState.value = 'degraded'),
  })

  function openStream() {
    streamState.value = 'connecting'
    const url = missionControlEventsUrl(ownerId.value || undefined)
    es.open(replay ? `${url}${url.includes('?') ? '&' : '?'}mode=replay` : url)
  }

  async function loadOwners() {
    try {
      owners.value = await listOwners()
      if (!ownerId.value && owners.value.length) ownerId.value = owners.value[0].owner_id
    } catch (e: any) {
      error.value = e?.message || 'OWNER LIST FAULT'
    }
  }

  async function refresh() {
    loading.value = true
    try {
      snapshot.value = await getMissionControlSnapshot(ownerId.value || undefined, replay ? 'replay' : undefined)
      error.value = ''
    } catch (e: any) {
      error.value = e?.response?.data?.detail || e?.message || 'LINK FAULT // MCC OFFLINE'
    } finally {
      loading.value = false
    }
  }

  let ownerWatchReady = false
  onMounted(async () => {
    await loadOwners()
    ownerWatchReady = true
    await refresh()
    openStream()
    pollTimer = window.setInterval(refresh, POLL_MS)
    clockTimer = window.setInterval(() => (now.value = Date.now()), 1000)
  })
  onBeforeUnmount(() => {
    if (pollTimer) window.clearInterval(pollTimer)
    if (clockTimer) window.clearInterval(clockTimer)
    if (channelRefreshTimer) window.clearTimeout(channelRefreshTimer)
    pulseTimers.forEach((t) => window.clearTimeout(t))
    pulseTimers.clear()
    es.close()
  })
  watch(ownerId, async () => {
    if (!ownerWatchReady) return
    focusedCompanionId.value = ''
    selectedTurnId.value = ''
    selectedEventId.value = ''
    hoveredEventId.value = ''
    liveEvents.value = []
    activePulses.value = []
    lastLegEmit.clear()
    await refresh()
    openStream()
  }, { flush: 'sync' })

  return {
    // state
    owners, ownerId, snapshot, liveEvents, loading, error, streamState, now, replay,
    // primitives
    experience, memory, devices, runtimeBlackboard, services, jobs, activities, activeActivities, companions,
    companionNames, deviceNames,
    ownerName, onlineDevices, onlineServices, activeJobs, degradedSources,
    primaryActiveVoiceTurn, runtimeTurns, selectedTurn, selectedTurnId, selectedEventId,
    highlightedEvent, hoveredEventId,
    pipelineActive, primaryCompanionId, privacyMode, deviceRatio,
    recentEvents, traceId, traceSpans, evidenceChains, permissionLedger, demoMode,
    // sovereign-domain view
    companionUnits, unboundDevices,
    focusedCompanionId, focusedCompanion, scopedTurn, scopedActivities, scopedJobs, scopedPermissions, companionEvents,
    // event-driven pulses
    activePulses, flowEventsEnabled,
    // infra rail
    infraNodes, hotServices, serviceActivityOwners,
    // header chrome
    clock, streamLabelText, systemStateText,
    // actions
    refresh, selectTurn, selectEvent, hoverEvent, followLive,
  }
}

export type MissionControlStream = ReturnType<typeof useMissionControlStream>
