<script setup lang="ts">
// Unified runtime cockpit (formerly MissionControl.vue + MissionControlCyber.vue).
// Full-bleed, god's-eye observatory — reached from the sidebar launcher, with a
// return-to-console affordance. This shell owns only layout + drawer routing;
// all data/state lives in useMissionControlStream, all visuals in child comps.
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CockpitHeader from './components/CockpitHeader.vue'
import DrilldownDrawer from './components/DrilldownDrawer.vue'
import LiveTraceTimeline from './components/LiveTraceTimeline.vue'
import RuntimeBusRail from './components/RuntimeBusRail.vue'
import RecentEventsPanel from './components/RecentEventsPanel.vue'
import SovereignConstellation from './components/SovereignConstellation.vue'
import OrbitField from './primitives/OrbitField.vue'
import { useMissionControlStream } from './useMissionControlStream'
import { prefersReducedMotion } from './motion'
import type { CompanionUnit, DrawerTarget, InfraNode, Sat } from './types'
import './cockpit.tokens.css'

const route = useRoute()
const router = useRouter()
const mode = route.query.mode === 'replay' ? 'replay' : 'live'
const mc = useMissionControlStream({ mode })

const {
  pipelineActive, error,
  infraNodes, hotService,
  scopedTurn, companionEvents, focusedCompanion, focusedCompanionId,
} = mc

const drawer = ref<DrawerTarget | null>(null)
// Clicking a companion (planet or its moon) focuses it — re-scoping the live
// trace + event stream — and opens the deep-dive drawer, which carries that
// companion's evidence (memory / tasks / permissions). Focus persists after the
// drawer closes; the owner sun clears it back to owner scope.
function openOwner() { focusedCompanionId.value = ''; drawer.value = { type: 'owner' } }
function openComp(c: CompanionUnit) { focusedCompanionId.value = c.id; drawer.value = { type: 'companion', c } }
function openMoon(s: Sat) { focusedCompanionId.value = s.c.id; drawer.value = { type: 'moon', s } }
function openSvc(n: InfraNode) { drawer.value = { type: 'service', n } }
function openTrace() { drawer.value = { type: 'trace' } }
function closeDrawer() { drawer.value = null }

// Pointer parallax (A3.2): depth via subtle background offset. rAF-throttled,
// off under reduced-motion. Drives --px/--py consumed by the ambient layers.
const px = ref(0)
const py = ref(0)
let pRaf = 0
let tx = 0
let ty = 0
function applyParallax() {
  px.value = tx
  py.value = ty
  pRaf = 0
}
function onPointerMove(e: PointerEvent) {
  if (prefersReducedMotion()) return
  tx = (e.clientX / window.innerWidth - 0.5) * 2
  ty = (e.clientY / window.innerHeight - 0.5) * 2
  if (!pRaf) pRaf = requestAnimationFrame(applyParallax)
}

function returnToConsole() { router.push({ name: 'owners' }) }
</script>

<template>
  <main
    class="cockpit cy"
    :class="{ live: pipelineActive }"
    :style="{ '--px': px, '--py': py }"
    @pointermove="onPointerMove"
  >
    <OrbitField class="cy-orbit" />
    <div class="cy-grid" aria-hidden="true" />
    <div class="cy-glow" aria-hidden="true" />
    <div class="cy-scan" aria-hidden="true" />
    <div class="cy-flicker" aria-hidden="true" />

    <CockpitHeader :mc="mc" @return-console="returnToConsole" />
    <p v-if="error" class="cy-error">// {{ error }}</p>

    <SovereignConstellation :mc="mc" :focused-id="focusedCompanionId" @open-owner="openOwner" @open-companion="openComp" @open-moon="openMoon" />

    <LiveTraceTimeline :turn="scopedTurn" :scope="focusedCompanion?.name || ''" @open="openTrace" />

    <RuntimeBusRail :nodes="infraNodes" :hot-service="hotService" :pipeline-active="pipelineActive" @open-service="openSvc" />
    <RecentEventsPanel :events="companionEvents" :scope="focusedCompanion?.name || ''" />

    <DrilldownDrawer :mc="mc" :target="drawer" @open-companion="openComp" @close="closeDrawer" />
  </main>
</template>

<style scoped>
.cy {
  position: relative; margin: 0; padding: 20px 22px 14px; min-height: 100vh;
  display: flex; flex-direction: column; gap: 10px; overflow: hidden;
  color: var(--cy-txt); font-family: var(--cy-mono);
  background:
    radial-gradient(circle at 20% 0%, rgba(255, 46, 136, 0.12), transparent 40%),
    radial-gradient(circle at 82% 8%, rgba(0, 234, 255, 0.12), transparent 42%),
    var(--cy-bg);
  isolation: isolate;
}
.cy-orbit { position: absolute; inset: -24px; z-index: -4; pointer-events: none; transform: translate(calc(var(--px, 0) * -16px), calc(var(--py, 0) * -12px)); transition: transform 0.35s var(--ease-out); }
.cy-grid { position: absolute; inset: 0; z-index: -3; pointer-events: none; background-image: linear-gradient(rgba(0, 234, 255, 0.2) 1px, transparent 1px), linear-gradient(90deg, rgba(255, 46, 136, 0.16) 1px, transparent 1px); background-size: 46px 46px; transform: perspective(440px) rotateX(70deg); transform-origin: bottom; mask-image: linear-gradient(to top, #000, transparent 60%); animation: gridrun 5s linear infinite; opacity: 0.45; }
.cy-glow { position: absolute; inset: -30px; z-index: -2; pointer-events: none; background: radial-gradient(circle at 50% 46%, rgba(164, 75, 255, 0.16), transparent 52%); transform: translate(calc(var(--px, 0) * 20px), calc(var(--py, 0) * 14px)); transition: transform 0.4s var(--ease-out); }
.cy-scan { position: absolute; inset: 0; z-index: 5; pointer-events: none; background: repeating-linear-gradient(transparent 0 2px, rgba(0, 0, 0, 0.22) 3px 4px); mix-blend-mode: multiply; opacity: 0.5; }
.cy-flicker { position: absolute; inset: 0; z-index: 4; pointer-events: none; background: rgba(0, 234, 255, 0.02); animation: flicker 5s steps(30) infinite; }

.cy-error { position: relative; z-index: 1; padding: 9px 13px; color: var(--cy-mag); border: 1px solid var(--cy-mag); background: rgba(255, 46, 136, 0.08); }

@keyframes gridrun { from { background-position: 0 0; } to { background-position: 0 46px; } }
@keyframes flicker { 0%, 96%, 100% { opacity: 0.4; } 97% { opacity: 0.05; } 98% { opacity: 0.7; } }
@media (prefers-reduced-motion: reduce) { .cy-grid, .cy-flicker { animation: none !important; } }
</style>
