/**
 * Pure-unit tests for activity-driven companion circulation and event pulses.
 *
 * These are deterministic functions over their arguments — no admin gateway,
 * no DOM, no SMIL. They pin the three decisions the design calls out:
 *   1. which companions circulate (focused + current activity),
 *   2. which legs light up (memory_hits / devices.online),
 *   3. how the loop path + timing are generated (reusing DURATION tokens).
 */
import { describe, expect, it } from 'vitest'
import {
  currentStageKey,
  demoFlowDevice,
  demoFlowTurn,
  directedLegPath,
  eventBelongsToTurn,
  eventToPulse,
  eventTone,
  flowDur,
  flowEventDur,
  flowLegs,
  flowPath,
  flowStagger,
  isDemoFlowTarget,
  loopSegments,
  PULSE_MIN_GAP_MS,
  pulseInScope,
  pulseThrottled,
  shouldFlow,
  spineReached,
  stageMoon,
  type FlowLegs,
  type Pt,
} from '../src/modules/mission-control/flow'
import { DURATION } from '../src/modules/mission-control/motion'
import { compactEventSummary, compactId, deviceShort, genomeStateLabel, memoryRealmStateLabel, statusClass } from '../src/modules/mission-control/format'
import { activityBadgeLabel, activityPhases, activityServiceId, activityStatusLabel, isActiveActivity, summarizeActivityBadges, traceSpansForTurn } from '../src/modules/mission-control/activity'
import type { CompanionUnit } from '../src/modules/mission-control/types'
import type { RuntimeActivity, RuntimeDevice, RuntimeEvent, RuntimeTurn, RuntimeTurnStage } from '../src/api/missionControl'

// ── fixtures ────────────────────────────────────────────────────────────
function device(over: Partial<RuntimeDevice> = {}): RuntimeDevice {
  return {
    device_id: 'd1', name: 'dev', role: '', kind: 'esp32', status: 'ok',
    online: true, approved: true, owner_id: 'o1', companion_id: 'c1',
    interaction_mode: null, room_name: '', participant_sid: '',
    last_seen_at: null, capabilities: [], signals: {}, ...over,
  }
}
function turn(over: Partial<RuntimeTurn> = {}): RuntimeTurn {
  return {
    turn_id: 't1', conversation_id: 'cv1', owner_id: 'o1', companion_id: 'c1',
    trace_id: 't1', channel_turn_id: 't1', agent_turn_id: null,
    device_id: 'd1', status: 'running', trigger: 'voice', started_at: null,
    finished_at: null, latency_ms: 120, memory_hits: 0, tool_names: [],
    privacy_mode: null, phase: 'user_speech_open', outcome: 'deferred',
    terminal_reason: '', event_ids: [], missing_milestones: [], stages: [], ...over,
  }
}
function companion(over: Partial<CompanionUnit> = {}): CompanionUnit {
  return {
    id: 'c1', name: 'Aria', kind: 'companion', status: 'active', genome: '',
    realm: '', isActiveRealm: false, recall: null, runners: '', write: '',
    devices: [], activities: [], activeActivity: null,
    activeVoiceTurn: null, turn: null, turns: [], jobs: [], isDefault: false, ...over,
  }
}
function activity(over: Partial<RuntimeActivity> = {}): RuntimeActivity {
  return {
    activity_id: 'voice:t1', kind: 'voice_turn', owner_id: 'o1', companion_id: 'c1',
    trace_id: 't1', turn_id: 't1', job_id: null, origin_device_id: 'd1',
    target_device_ids: [], status: 'running', outcome: 'deferred', summary: 'thinking',
    current_hop_id: 'h2', started_at: '2026-07-16T10:00:00Z', updated_at: '2026-07-16T10:00:01Z',
    finished_at: null, event_ids: [],
    route: [
      { hop_id: 'h1', node_type: 'service', node_id: 'channel', label: 'Channel', stage: 'speech', status: 'done', direction: 'in', ts: null, latency_ms: 20 },
      { hop_id: 'h2', node_type: 'service', node_id: 'agent', label: 'Agent', stage: 'brain', status: 'running', direction: 'internal', ts: null, latency_ms: null },
    ],
    ...over,
  }
}

describe('runtime identifier presentation', () => {
  it('leaves readable IDs intact and compacts opaque long IDs without losing both ends', () => {
    expect(compactId('02fde968f29042aa')).toBe('02fde968f29042aa')
    expect(compactId('g_guard_62042b427925d7ca0bcc7685e4708e421496b1a1')).toBe('g_guard_62042b4…1496b1a1')
  })

  it('only compacts structured IDs embedded in event summaries', () => {
    const turnId = '7e361bd7669f4b31bbb7e558e0fbce9a'
    const runtimeEvent: RuntimeEvent = {
      event_id: 'evt-1', ts: '2026-07-16T10:00:00Z', source: 'memory',
      type: 'memory.fanout', severity: 'info', outcome: 'success', privacy: 'safe',
      event_origin: 'polling', trace_id: turnId, owner_id: 'o1', companion_id: 'c1',
      device_id: null, conversation_id: null, turn_id: turnId, job_id: null,
      summary: `memory fanout absorbed · turn:${turnId}`, payload: {},
    }
    expect(compactEventSummary(runtimeEvent)).toBe(`memory fanout absorbed · turn:${compactId(turnId)}`)
    expect(compactEventSummary({ ...runtimeEvent, turn_id: null, trace_id: null })).toContain(turnId)
  })

  it('uses asset state and device tail labels outside diagnostic views', () => {
    expect(genomeStateLabel('g_opaque')).toBe('已绑定')
    expect(genomeStateLabel('')).toBe('未绑定')
    expect(memoryRealmStateLabel('r_opaque')).toBe('已配置')
    expect(memoryRealmStateLabel(null)).toBe('未开通')
    expect(deviceShort(device({
      device_id: '24:ec:4a:52:f3:54',
      name: '24:ec:4a:52:f3:54',
    }))).toBe('尾号 F3:54')
  })

  it('replaces a structured device ID in event text with its readable label', () => {
    const deviceId = '24:ec:4a:52:f3:54'
    const runtimeEvent: RuntimeEvent = {
      event_id: 'evt-2', ts: '2026-07-16T10:00:00Z', source: 'hub',
      type: 'device.command', severity: 'info', outcome: 'success', privacy: 'safe',
      event_origin: 'polling', trace_id: null, owner_id: 'o1', companion_id: null,
      device_id: deviceId, conversation_id: null, turn_id: null, job_id: null,
      summary: `device command acked · device:${deviceId}`, payload: {},
    }
    expect(compactEventSummary(runtimeEvent, {
      deviceNames: { [deviceId]: '尾号 F3:54' },
    })).toBe('device command acked · device:尾号 F3:54')
  })
})

it('renders a session-reconciled orphan turn as a failure', () => {
  expect(statusClass('orphaned')).toBe('bad')
  expect(statusClass('interrupted')).toBe('warn')
})

describe('runtime activity projection helpers', () => {
  it('compresses voice hops into semantic phases while preserving current state and facts', () => {
    const voice = activity()
    voice.route.push({
      hop_id: 'h3', node_type: 'device', node_id: 'd1', label: '播放完成',
      stage: 'playback', status: 'done', direction: 'out', ts: null, latency_ms: 420,
    })
    const phases = activityPhases(voice)
    expect(phases.map((phase) => phase.key)).toEqual(['input', 'brain', 'output'])
    expect(phases[0]?.hops.map((hop) => hop.hop_id)).toEqual(['h1'])
    expect(phases[1]?.current).toBe(true)
    expect(phases[1]?.label).toBe('思考')
    expect(phases[2]?.hops.map((hop) => hop.hop_id)).toEqual(['h3'])
    expect(phases[2]?.latency_ms).toBe(420)
  })

  it('keeps non-voice routes explicit and translates status labels', () => {
    const command = activity({ kind: 'device_command' })
    expect(activityPhases(command).map((phase) => phase.label)).toEqual(['Channel', 'Agent'])
    expect(activityStatusLabel('completed')).toBe('已完成')
    expect(activityStatusLabel('interrupted')).toBe('已打断')
    expect(activityStatusLabel('timeout')).toBe('已超时')
  })

  it('recognises independent active lanes and resolves their substrate playhead', () => {
    const voice = activity()
    const guard = activity({
      activity_id: 'guard:g1', kind: 'guard_event', companion_id: 'guard',
      current_hop_id: 'guard-hub',
      route: [{ hop_id: 'guard-hub', node_type: 'service', node_id: 'hub', label: 'Hub', stage: 'observe', status: 'running', direction: 'in', ts: null, latency_ms: null }],
    })
    expect(isActiveActivity(voice)).toBe(true)
    expect(isActiveActivity(guard)).toBe(true)
    expect(activityServiceId(voice)).toBe('agent')
    expect(activityServiceId(guard)).toBe('hub')
  })

  it('uses meaningful state/time labels for independent activities', () => {
    expect(activityBadgeLabel(activity())).toBe('当前')
    expect(activityBadgeLabel(activity({
      status: 'completed', outcome: 'success',
      finished_at: '2026-07-16T11:58:30Z', updated_at: '2026-07-16T11:58:30Z',
    }), Date.parse('2026-07-16T12:00:00Z'))).toBe('1分')
    expect(activityBadgeLabel(activity({ kind: 'guard_event', status: 'completed', outcome: 'success' }))).toBe('守护')
    expect(activityBadgeLabel(activity({ status: 'failed', outcome: 'failure' }))).toBe('失败')
  })

  it('keeps Agent spans scoped to exactly one selected/focused voice turn', () => {
    const spans = [
      { span_id: 's1', turn_id: 't1', name: 'one', kind: 'model', status: 'done', latency_ms: 10, detail: '' },
      { span_id: 's2', turn_id: 't2', name: 'two', kind: 'model', status: 'done', latency_ms: 20, detail: '' },
    ]
    expect(traceSpansForTurn(spans, turn({ turn_id: 't2' })).map((span) => span.span_id)).toEqual(['s2'])
    expect(traceSpansForTurn(spans, null)).toEqual([])
  })

  it('groups repeated Guard/device facts without hiding distinct voice turns', () => {
    const rows = summarizeActivityBadges([
      activity({ activity_id: 'g1', kind: 'guard_event', status: 'completed', outcome: 'success' }),
      activity({ activity_id: 'g2', kind: 'guard_event', status: 'completed', outcome: 'success' }),
      activity({ activity_id: 'v1', kind: 'voice_turn', status: 'completed', outcome: 'success' }),
      activity({ activity_id: 'v2', kind: 'voice_turn', status: 'completed', outcome: 'success' }),
    ], Date.parse('2026-07-16T12:00:00Z'))
    expect(rows.map((row) => row.label)).toContain('守护×2')
    expect(rows.filter((row) => row.activity.kind === 'voice_turn')).toHaveLength(2)
  })
})

// ── shouldFlow: which companions circulate ────────────────────────────────
describe('shouldFlow', () => {
  it('flows the focused companion with a current activity, at any active count', () => {
    expect(shouldFlow('c1', 'c1', true, 9)).toBe(true)
  })
  it('never flows without a current activity', () => {
    expect(shouldFlow('c1', 'c1', false, 1)).toBe(false)
    expect(shouldFlow('c1', '', false, 1)).toBe(false)
  })
  it('flows an unfocused active companion while few are active', () => {
    expect(shouldFlow('c1', '', true, 1)).toBe(true)
    expect(shouldFlow('c1', '', true, 2)).toBe(true)
    expect(shouldFlow('c1', undefined, true, 2)).toBe(true)
  })
  it('drops unfocused flow once the active count exceeds AUTO_FLOW_MAX', () => {
    // a non-focused companion in a busy scene keeps only the lighter node pulse
    expect(shouldFlow('c2', 'c1', true, 3)).toBe(false)
    expect(shouldFlow('c1', '', true, 3)).toBe(false)
  })
})

// ── flowLegs: leg-lit determination ───────────────────────────────────────
describe('flowLegs', () => {
  it('lights the body leg when any device is online', () => {
    const c = companion({ devices: [device({ online: false }), device({ online: true })] })
    expect(flowLegs(c).body).toBe(true)
  })
  it('darkens the body leg when every device is offline', () => {
    const c = companion({ devices: [device({ online: false })] })
    expect(flowLegs(c).body).toBe(false)
  })
  it('darkens the body leg when there are no devices', () => {
    expect(flowLegs(companion({ devices: [] })).body).toBe(false)
  })
  it('lights the memory leg only when the turn recalled hits', () => {
    expect(flowLegs(companion({ turn: turn({ memory_hits: 0 }) })).mem).toBe(false)
    expect(flowLegs(companion({ turn: turn({ memory_hits: 3 }) })).mem).toBe(true)
  })
  it('leaves the memory leg dark with no scoped voice turn', () => {
    const legs = flowLegs(companion({ turn: null }))
    expect(legs.mem).toBe(false)
    expect(legs.memBright).toBe(0)
  })
  it('scales memory brightness with hit count (floor .45 → full 1)', () => {
    const one = flowLegs(companion({ turn: turn({ memory_hits: 1 }) })).memBright
    const many = flowLegs(companion({ turn: turn({ memory_hits: 99 }) })).memBright
    expect(one).toBeGreaterThanOrEqual(0.45)
    expect(one).toBeLessThan(1)
    expect(many).toBe(1)
    // monotonic: more hits never darker
    expect(flowLegs(companion({ turn: turn({ memory_hits: 4 }) })).memBright).toBeGreaterThan(one)
  })
})

// ── flowPath: loop-path generation ─────────────────────────────────────────
describe('flowPath', () => {
  const brain: Pt = { x: 100, y: 100 }
  const body: Pt = { x: 40, y: 160 }
  const mem: Pt = { x: 160, y: 160 }
  const act: Pt = { x: 100, y: 20 }
  const legs = (over: Partial<FlowLegs>): FlowLegs => ({ body: false, mem: false, memBright: 0, act: false, ...over })

  it('threads a closed body→brain→memory→brain→body loop when both legs lit', () => {
    const d = flowPath(brain, body, mem, act, legs({ body: true, mem: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L160.0 160.0 L100.0 100.0 L40.0 160.0')
    // closed: starts and ends on the same point (seamless repeat, no teleport)
    expect(d.startsWith('M40.0 160.0')).toBe(true)
    expect(d.endsWith('L40.0 160.0')).toBe(true)
    expect(loopSegments(d)).toBe(4)
  })
  it('bounces body↔brain when only the body leg is lit', () => {
    const d = flowPath(brain, body, mem, act, legs({ body: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L40.0 160.0')
    expect(loopSegments(d)).toBe(2)
  })
  it('bounces brain↔memory when only the memory leg is lit', () => {
    const d = flowPath(brain, body, mem, act, legs({ mem: true }))
    expect(d).toBe('M160.0 160.0 L100.0 100.0 L160.0 160.0')
    expect(loopSegments(d)).toBe(2)
  })
  it('returns empty when no leg is lit (node pulse alone signals the brain)', () => {
    expect(flowPath(brain, body, mem, act, legs({}))).toBe('')
  })
  it('includes the activity moon while a turn is live', () => {
    const d = flowPath(brain, body, mem, act, legs({ body: true, act: true }))
    expect(d).toBe('M40.0 160.0 L100.0 100.0 L100.0 20.0 L100.0 100.0 L40.0 160.0')
  })
})

// ── flowDur / flowStagger: timing from DURATION tokens ─────────────────────
describe('flow timing', () => {
  const four = 'M0 0 L1 1 L2 2 L3 3 L0 0'
  const two = 'M0 0 L1 1 L0 0'

  it('derives dur from DURATION.ambient × leg count (never hardcoded)', () => {
    expect(flowDur(four)).toBe(`${((DURATION.ambient * 4) / 1000).toFixed(2)}s`)
    expect(flowDur(two)).toBe(`${((DURATION.ambient * 2) / 1000).toFixed(2)}s`)
    // a 4-leg loop takes exactly twice as long as a 2-leg loop (constant speed)
    expect(parseFloat(flowDur(four))).toBeCloseTo(2 * parseFloat(flowDur(two)))
  })
  it('never yields a zero duration, even for a degenerate path', () => {
    expect(parseFloat(flowDur(''))).toBeGreaterThan(0)
  })
  it('staggers the trailing dot by a negative half-loop (starts mid-path)', () => {
    const s = flowStagger(four)
    expect(s.startsWith('-')).toBe(true)
    // half of the loop duration
    expect(Math.abs(parseFloat(s))).toBeCloseTo(parseFloat(flowDur(four)) / 2)
  })
})

// ── dev-only demo hook: ?demoFlow ─────────────────────────────────────────
describe('demo flow hook', () => {
  it('targets an exact companion id, or the first when the param is empty', () => {
    expect(isDemoFlowTarget('c2', 1, 'c2')).toBe(true)
    expect(isDemoFlowTarget('c1', 0, 'c2')).toBe(false)
    // bare `?demoFlow` (empty string) → first companion only
    expect(isDemoFlowTarget('c1', 0, '')).toBe(true)
    expect(isDemoFlowTarget('c2', 1, '')).toBe(false)
  })
  it('never targets anything when the param is absent (undefined)', () => {
    expect(isDemoFlowTarget('c1', 0, undefined)).toBe(false)
  })
  it('synthesises a turn + body that light BOTH flow legs', () => {
    const c = companion({
      turn: demoFlowTurn('c1'),
      devices: [demoFlowDevice('c1')],
    })
    const legs = flowLegs(c)
    expect(legs.body).toBe(true)
    expect(legs.mem).toBe(true)
    expect(legs.memBright).toBeGreaterThan(0)
    expect(demoFlowTurn('c1').memory_hits).toBeGreaterThan(0)
    expect(demoFlowDevice('c1').online).toBe(true)
  })
})

// ── event-driven directed pulses ──────────────────────────────────────────
describe('eventToPulse', () => {
  const event = (source: RuntimeEvent['source'], type = 'runtime.event', payload: Record<string, any> = {}) => ({ source, type, payload })
  it('maps device sources to an inbound body pulse', () => {
    expect(eventToPulse(event('channel', 'channel.turn.phase_changed', { phase: 'user_speech_open' }))).toEqual({ leg: 'body', dir: 'in' })
    expect(eventToPulse(event('hub'))).toEqual({ leg: 'body', dir: 'in' })
  })
  it('maps memory to an outbound memory pulse (brain→mem)', () => {
    expect(eventToPulse(event('memory'))).toEqual({ leg: 'mem', dir: 'out' })
  })
  it('maps agent activity to the activity moon', () => {
    expect(eventToPulse(event('agent'))).toEqual({ leg: 'act', dir: 'in' })
  })
  it('uses Channel milestone semantics for brain and playback direction', () => {
    expect(eventToPulse(event('channel', 'channel.turn.milestone', { milestone: 'generating' }))).toEqual({ leg: 'act', dir: 'out' })
    expect(eventToPulse(event('channel', 'channel.turn.milestone', { milestone: 'brain_first_delta' }))).toEqual({ leg: 'act', dir: 'in' })
    expect(eventToPulse(event('channel', 'channel.turn.milestone', { milestone: 'first_audio' }))).toEqual({ leg: 'body', dir: 'out' })
  })
  it('returns null for sources that touch no leg', () => {
    expect(eventToPulse(event('data'))).toBeNull()
    expect(eventToPulse(event('admin'))).toBeNull()
    expect(eventToPulse(event('mission_control'))).toBeNull()
  })
})

describe('eventBelongsToTurn', () => {
  it('never treats two missing trace ids as a correlation match', () => {
    const legacy = turn({
      turn_id: 'agent-1', trace_id: null, channel_turn_id: null,
      agent_turn_id: 'agent-1', event_ids: [],
    })
    expect(eventBelongsToTurn({
      event_id: 'hub-1', trace_id: null, turn_id: null, payload: {},
    }, legacy)).toBe(false)
  })

  it('matches the explicit event list, non-empty trace, and either hop id', () => {
    const unified = turn({
      turn_id: 'channel-1', trace_id: 'channel-1', channel_turn_id: 'channel-1',
      agent_turn_id: 'agent-1', event_ids: ['phase-1'],
    })
    expect(eventBelongsToTurn({
      event_id: 'phase-1', trace_id: null, turn_id: null, payload: {},
    }, unified)).toBe(true)
    expect(eventBelongsToTurn({
      event_id: 'memory-1', trace_id: 'channel-1', turn_id: null, payload: {},
    }, unified)).toBe(true)
    expect(eventBelongsToTurn({
      event_id: 'agent-event', trace_id: null, turn_id: 'agent-1', payload: {},
    }, unified)).toBe(true)
    expect(eventBelongsToTurn({
      event_id: 'channel-event', trace_id: null, turn_id: null,
      payload: { channel_turn_id: 'channel-1' },
    }, unified)).toBe(true)
  })
})

describe('directedLegPath', () => {
  const brain: Pt = { x: 100, y: 100 }
  const moon: Pt = { x: 40, y: 160 }
  it("'in' travels moon → brain", () => {
    expect(directedLegPath(brain, moon, 'in')).toBe('M40.0 160.0 L100.0 100.0')
  })
  it("'out' travels brain → moon (reverse of 'in')", () => {
    expect(directedLegPath(brain, moon, 'out')).toBe('M100.0 100.0 L40.0 160.0')
  })
  it('derives the dart duration from a motion token, non-zero', () => {
    expect(flowEventDur()).toMatch(/^\d+\.\d{2}s$/)
    expect(parseFloat(flowEventDur())).toBeGreaterThan(0)
  })
})

describe('eventTone', () => {
  it("escalates errors and failed outcomes to 'bad'", () => {
    expect(eventTone('error')).toBe('bad')
    expect(eventTone('info', 'failure')).toBe('bad')
    // a failed outcome outranks an otherwise-info severity
    expect(eventTone(undefined, 'failure')).toBe('bad')
  })
  it("maps warnings and denied outcomes to 'warn'", () => {
    expect(eventTone('warn')).toBe('warn')
    expect(eventTone('info', 'denied')).toBe('warn')
  })
  it("treats info / success / deferred / missing signals as 'normal'", () => {
    expect(eventTone('info')).toBe('normal')
    expect(eventTone(undefined, 'success')).toBe('normal')
    expect(eventTone('info', 'deferred')).toBe('normal')
    expect(eventTone()).toBe('normal')
  })
  it("'bad' wins over 'warn' when both signals disagree", () => {
    expect(eventTone('warn', 'failure')).toBe('bad')
  })
})

describe('pulseInScope', () => {
  it("'focused' scope emits only for the focused companion", () => {
    expect(pulseInScope('c1', 'c1', 'focused')).toBe(true)
    expect(pulseInScope('c2', 'c1', 'focused')).toBe(false)
  })
  it("'focused' scope emits nothing when no companion is focused", () => {
    expect(pulseInScope('c1', '', 'focused')).toBe(false)
    expect(pulseInScope('c1', undefined, 'focused')).toBe(false)
  })
  it("'all' scope emits for every companion (even unfocused / no focus)", () => {
    expect(pulseInScope('c2', 'c1', 'all')).toBe(true)
    expect(pulseInScope('c9', '', 'all')).toBe(true)
    expect(pulseInScope('c9', undefined, 'all')).toBe(true)
  })
})

describe('pulseThrottled', () => {
  it('drops a pulse that arrives inside the min gap', () => {
    expect(pulseThrottled(1000, 1000 + PULSE_MIN_GAP_MS - 1)).toBe(true)
  })
  it('allows a pulse once the min gap has elapsed', () => {
    expect(pulseThrottled(1000, 1000 + PULSE_MIN_GAP_MS)).toBe(false)
    expect(pulseThrottled(1000, 5000)).toBe(false)
  })
  it('never throttles the first pulse on a leg (lastMs 0)', () => {
    expect(pulseThrottled(0, 1_000_000)).toBe(false)
  })
})

// ── currentStageKey: legacy voice-detail fallback ─────────────────────────
describe('currentStageKey', () => {
  const stage = (key: string, status: string): RuntimeTurnStage => ({ key, label: key, status, latency_ms: null })
  it('is empty for a null/undefined turn or one with no stages', () => {
    expect(currentStageKey(null)).toBe('')
    expect(currentStageKey(undefined)).toBe('')
    expect(currentStageKey(turn({ stages: [] }))).toBe('')
  })
  it('points at the running/pending stage when one is in flight', () => {
    expect(currentStageKey(turn({ stages: [stage('input', 'done'), stage('agent_turn', 'running')] }))).toBe('agent_turn')
  })
  it('falls back to the last completed stage when nothing runs', () => {
    expect(currentStageKey(turn({ stages: [stage('input', 'done'), stage('memory_recall', 'done')] }))).toBe('memory_recall')
  })
  it('prefers a running stage over an already-completed later one', () => {
    expect(currentStageKey(turn({ stages: [stage('input', 'running'), stage('memory_recall', 'done')] }))).toBe('input')
  })
})

// ── spineReached: the bus wavefront ────────────────────────────────────────
describe('spineReached', () => {
  it('flows the whole spine when there is no current stage (graceful default)', () => {
    expect(spineReached('', 'memory')).toBe(true)
    expect(spineReached('', 'livekit')).toBe(true)
  })
  it('flows only edges up to and including the current service', () => {
    // hot = agent → hub/livekit/channel/agent reached; memory (later) not yet
    expect(spineReached('agent', 'channel')).toBe(true)
    expect(spineReached('agent', 'agent')).toBe(true)
    expect(spineReached('agent', 'memory')).toBe(false)
  })
  it('reaches the whole spine once memory (the tail) is current', () => {
    expect(spineReached('memory', 'memory')).toBe(true)
    expect(spineReached('memory', 'livekit')).toBe(true)
  })
  it('flows the whole spine for an off-spine hot service rather than going dark', () => {
    expect(spineReached('mementos', 'agent')).toBe(true)
  })
})

// ── stageMoon: constellation moon for the current stage ────────────────────
describe('stageMoon', () => {
  it('maps input to the body moon', () => {
    expect(stageMoon('input')).toBe('body')
  })
  it('maps recall and write to the memory moon', () => {
    expect(stageMoon('memory_recall')).toBe('mem')
    expect(stageMoon('memory_write')).toBe('mem')
  })
  it('maps the agent turn and its tools to the activity moon', () => {
    expect(stageMoon('agent_turn')).toBe('act')
    expect(stageMoon('tools')).toBe('act')
  })
  it('is empty for an unknown or empty stage', () => {
    expect(stageMoon('')).toBe('')
    expect(stageMoon('nonsense')).toBe('')
  })
})
