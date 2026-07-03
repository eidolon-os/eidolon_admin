// The single owner of cockpit side-effects and derived state. Both former
// SFCs (MissionControl.vue + MissionControlCyber.vue) each re-implemented this
// lifecycle; it now lives here once so every view component is thin/pure.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listOwners, type OwnerView } from '@/api/eidolonData'
import {
  getMissionControlSnapshot,
  missionControlEventsUrl,
  type RuntimeCompanion,
  type RuntimeEvent,
  type RuntimeSnapshot,
  type RuntimeService,
  type RuntimeTurn,
} from '@/api/missionControl'
import { useEventStream } from '@/components/useEventStream'
import { INFRA, SVC_GLYPH, BUS_SPINE, BUS_AUX, STAGE_SVC } from './constants'
import { fmtClock, fmtLatency, privacyModeLabel, streamLabel, systemStateLabel } from './format'
import type { CompanionUnit, InfraNode, StreamState } from './types'

const POLL_MS = 8000
const ACTIVE_STATES = ['running', 'active', 'pending', 'queued']

export interface MissionControlMode {
  /** 'replay' streams recorded fixtures (M4); 'live' is the default. */
  mode?: 'live' | 'replay'
}

export function useMissionControlStream(opts: MissionControlMode = {}) {
  const owners = ref<OwnerView[]>([])
  const ownerId = ref('')
  const snapshot = ref<RuntimeSnapshot | null>(null)
  const liveEvents = ref<RuntimeEvent[]>([])
  const loading = ref(false)
  const error = ref('')
  const streamState = ref<StreamState>('connecting')
  const now = ref(Date.now())
  const replay = opts.mode === 'replay'

  let pollTimer: number | undefined
  let clockTimer: number | undefined

  // ── primitives off the snapshot ──────────────────────────────────────
  const experience = computed(() => snapshot.value?.experience)
  const memory = computed(() => snapshot.value?.memory)
  const devices = computed(() => snapshot.value?.devices || [])
  const services = computed(() => snapshot.value?.services || [])
  const jobs = computed(() => snapshot.value?.jobs || [])
  const companions = computed<RuntimeCompanion[]>(() => {
    const list = snapshot.value?.companions || []
    return list.length ? list : snapshot.value?.companion ? [snapshot.value.companion] : []
  })

  const ownerName = computed(
    () => snapshot.value?.owner?.display_name || snapshot.value?.owner?.owner_id || '未选择主人',
  )
  const onlineDevices = computed(() => devices.value.filter((d) => d.online).length)
  const onlineServices = computed(() => services.value.filter((s) => s.online).length)
  const activeJobs = computed(
    () => jobs.value.filter((j) => ACTIVE_STATES.includes((j.status || '').toLowerCase())).length,
  )
  const degradedSources = computed(() => (snapshot.value?.source_status || []).filter((s) => !s.ok))
  const activeTurn = computed<RuntimeTurn | null>(
    () => snapshot.value?.active_turn || snapshot.value?.recent_turns?.[0] || null,
  )
  const pipelineActive = computed(() => {
    const t = snapshot.value?.active_turn
    return t ? ACTIVE_STATES.includes((t.status || '').toLowerCase()) : false
  })
  const completion = computed(() => experience.value?.completion ?? 0)
  const primaryCompanionId = computed(() => snapshot.value?.companion?.companion_id || '')
  const privacyMode = computed(() =>
    privacyModeLabel(memory.value?.privacy_mode || activeTurn.value?.privacy_mode || 'safe'),
  )
  const deviceRatio = computed(() =>
    devices.value.length ? Math.round((onlineDevices.value / devices.value.length) * 100) : 0,
  )

  const recentEvents = computed(() => {
    const merged = [...liveEvents.value, ...(snapshot.value?.recent_events || [])]
    const seen = new Set<string>()
    return merged.filter((e) => (seen.has(e.event_id) ? false : (seen.add(e.event_id), true))).slice(0, 16)
  })
  const traceId = computed(() =>
    (activeTurn.value?.turn_id || snapshot.value?.owner?.owner_id || 'STANDBY').slice(0, 14).toUpperCase(),
  )

  function svc(id: string): RuntimeService | null {
    return services.value.find((s) => s.service_id === id) || null
  }

  // ── companions projected into the sovereign-domain view ───────────────
  const companionUnits = computed<CompanionUnit[]>(() => {
    const t = snapshot.value?.active_turn
    const m = memory.value
    const activeRealm = m?.active_realm_id
    return companions.value.map((c) => {
      const devs = devices.value.filter((d) => d.companion_id === c.companion_id)
      const cJobs = jobs.value.filter((j) => j.companion_id === c.companion_id)
      const turn = t && t.companion_id === c.companion_id ? t : null
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
        turn,
        jobs: cJobs,
        isPrimary: c.companion_id === primaryCompanionId.value,
      }
    })
  })
  const boundIds = computed(() => new Set(companions.value.map((c) => c.companion_id)))
  const unboundDevices = computed(() =>
    devices.value.filter((d) => !d.companion_id || !boundIds.value.has(d.companion_id)),
  )

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
  const busSpine = computed(() => BUS_SPINE.map((id) => infraNodes.value.find((n) => n.id === id)).filter(Boolean) as InfraNode[])
  const busAux = computed(() => BUS_AUX.map((id) => infraNodes.value.find((n) => n.id === id)).filter(Boolean) as InfraNode[])

  // Which bus node the active turn's current stage lights (signal flow).
  const hotService = computed(() => {
    const t = snapshot.value?.active_turn
    if (!t) return ''
    const stages = t.stages || []
    const running = stages.find((s) => ['running', 'pending', 'active'].includes(String(s.status || '').toLowerCase()))
    const last = [...stages].reverse().find((s) => ['done', 'ok', 'succeeded'].includes(String(s.status || '').toLowerCase()))
    const key = (running || last)?.key
    return key ? STAGE_SVC[key] || '' : ''
  })

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
      snapshot.value = await getMissionControlSnapshot(ownerId.value || undefined)
      error.value = ''
    } catch (e: any) {
      error.value = e?.response?.data?.detail || e?.message || 'LINK FAULT // MCC OFFLINE'
    } finally {
      loading.value = false
    }
  }

  onMounted(async () => {
    await loadOwners()
    await refresh()
    openStream()
    pollTimer = window.setInterval(refresh, POLL_MS)
    clockTimer = window.setInterval(() => (now.value = Date.now()), 1000)
  })
  onBeforeUnmount(() => {
    if (pollTimer) window.clearInterval(pollTimer)
    if (clockTimer) window.clearInterval(clockTimer)
    es.close()
  })
  watch(ownerId, async () => {
    liveEvents.value = []
    await refresh()
    openStream()
  })

  return {
    // state
    owners, ownerId, snapshot, liveEvents, loading, error, streamState, now, replay,
    // primitives
    experience, memory, devices, services, jobs, companions,
    ownerName, onlineDevices, onlineServices, activeJobs, degradedSources,
    activeTurn, pipelineActive, completion, primaryCompanionId, privacyMode, deviceRatio,
    recentEvents, traceId,
    // sovereign-domain view
    companionUnits, unboundDevices,
    // infra rail
    infraNodes, busSpine, busAux, hotService,
    // header chrome
    clock, streamLabelText, systemStateText,
    // actions
    refresh,
  }
}

export type MissionControlStream = ReturnType<typeof useMissionControlStream>
