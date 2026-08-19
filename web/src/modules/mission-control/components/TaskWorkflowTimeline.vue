<script setup lang="ts">
// Region 5 · Task Workflow Timeline: long-tasks / coworker jobs and their
// lifecycle (queued → running → artifact → report-back).
import type { RuntimeJob } from '@/api/missionControl'

defineProps<{ jobs: RuntimeJob[] }>()

const LIFECYCLE = ['queued', 'running', 'succeeded', 'report'] as const

function reached(status: string, stage: string): boolean {
  const s = (status || '').toLowerCase()
  const order = ['queued', 'accepted', 'pending', 'running', 'active', 'succeeded', 'done', 'completed']
  const rank = (v: string) =>
    v === 'queued' ? 0 : v === 'running' ? 3 : v === 'succeeded' ? 5 : v === 'report' ? 5 : -1
  if (['failed', 'errored', 'timed_out', 'cancelled'].includes(s)) return stage === 'queued'
  return order.indexOf(s) >= rank(stage)
}
function tone(status: string) {
  const s = (status || '').toLowerCase()
  if (['succeeded', 'done', 'completed'].includes(s)) return 'ok'
  if (['running', 'queued', 'accepted', 'pending', 'active'].includes(s)) return 'warn'
  if (['failed', 'errored', 'timed_out', 'cancelled'].includes(s)) return 'bad'
  return 'idle'
}
</script>

<template>
  <div class="lane task-lane">
    <span class="lane-cap"><i class="led" :class="jobs.length ? 'warn' : 'idle'" />任务链路 · COWORKER<em v-if="jobs.length" class="lane-n">{{ jobs.length }}</em></span>
    <ul v-if="jobs.length" class="task-list">
      <li v-for="j in jobs.slice(0, 5)" :key="j.job_id" class="task-row">
        <b :class="tone(j.status)">{{ j.kind || j.provider || 'job' }}</b>
        <span class="task-dots">
          <i v-for="st in LIFECYCLE" :key="st" :class="{ on: reached(j.status, st) }" />
        </span>
        <em>{{ j.status }}</em>
      </li>
    </ul>
    <p v-else class="lane-idle">暂无后台任务</p>
  </div>
</template>

<style scoped>
.lane { display: flex; flex-direction: column; gap: 6px; padding: 8px 14px; border: 1px solid var(--cy-hair); background: var(--cy-panel); }
.lane-cap { display: inline-flex; align-items: center; gap: 6px; font: 700 9px/1 var(--cy-mono); letter-spacing: 0.1em; color: var(--cy-txt-dim); }
.lane-cap .led { width: 6px; height: 6px; }
.lane-n { font-style: normal; color: var(--cy-yellow); }
.task-list { display: grid; gap: 5px; margin: 0; padding: 0; list-style: none; }
.task-row { display: flex; align-items: center; gap: 8px; font: 600 10.5px/1.3 var(--cy-mono); color: var(--cy-txt); }
.task-row b { font-family: var(--cy-sans); font-weight: 700; flex: 0 0 auto; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-dots { display: inline-flex; gap: 3px; margin-left: auto; }
.task-dots i { width: 6px; height: 6px; border-radius: 50%; background: rgba(0, 234, 255, 0.15); }
.task-dots i.on { background: var(--cy-cyan); box-shadow: 0 0 6px var(--cy-cyan); }
.task-row em { color: var(--cy-txt-dim); font-style: normal; flex: 0 0 auto; }
.lane-idle { margin: 0; font: 400 11px/1 var(--cy-sans); color: var(--cy-txt-dim); }
</style>
