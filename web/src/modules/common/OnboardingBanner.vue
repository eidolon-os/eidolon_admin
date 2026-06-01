<script setup lang="ts">
/**
 * First-run onboarding banner.
 *
 * Polls /api/bootstrap/state on mount; if any catalog step is empty,
 * renders a strip with the 5-entity progress and a CTA that jumps to
 * the next empty step. Auto-hides once everything is ``ok``.
 *
 * The probe is deliberately silent on failure — a NATS blip during
 * boot shouldn't paint the page red. We only show the banner when
 * we're confident there's actionable empty state.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  getBootstrapState,
  type BootstrapState,
  type BootstrapStepName,
} from '@/api/bootstrap'

const state = ref<BootstrapState | null>(null)
const dismissed = ref<boolean>(
  // Session-scoped dismissal — operators can hide it for the current
  // tab, but on next reload it comes back if onboarding still incomplete.
  sessionStorage.getItem('onboarding-dismissed') === '1',
)
let timer: ReturnType<typeof setInterval> | null = null

const STEPS: { name: BootstrapStepName; label: string; route: string; hint: string }[] = [
  { name: 'tenants', label: 'Tenant', route: 'tenants', hint: '默认租户已自动创建' },
  { name: 'templates', label: 'Template', route: 'templates', hint: '从 agent 内置模板挑一个或新建' },
  { name: 'users', label: 'User', route: 'users', hint: '为这个 tenant 创建至少一个 user' },
  { name: 'agents', label: 'Agent', route: 'agents', hint: '把 user 和 template 拼成一个 agent' },
  { name: 'devices', label: 'Device', route: 'devices', hint: '让设备发现 hub 后批准并绑定 agent' },
]

async function refresh() {
  try {
    state.value = await getBootstrapState()
  } catch {
    // Probe is best-effort. Leave state as whatever it was.
  }
}

onMounted(async () => {
  await refresh()
  // Poll every 30s — the operator will trigger most updates by
  // clicking around, so a slow poll is fine to catch out-of-band
  // changes (e.g. someone curling the API in another window).
  timer = setInterval(refresh, 30_000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

const router = useRouter()

const visible = computed(() => {
  if (dismissed.value) return false
  if (!state.value) return false
  // Don't pester operators when the system is healthy.
  if (state.value.ready) return false
  // Also don't show when everything is "unknown" — that means we
  // probably can't reach NATS / sub-projects, and a "you should do X"
  // banner would just be noise.
  const hasEmpty = STEPS.some((s) => state.value![s.name]?.status === 'empty')
  return hasEmpty
})

const nextStep = computed(() => {
  if (!state.value?.next_step) return null
  return STEPS.find((s) => s.name === state.value!.next_step) || null
})

function stepStatusClass(name: BootstrapStepName): string {
  const status = state.value?.[name]?.status
  if (status === 'ok') return 'step-ok'
  if (status === 'empty') return 'step-empty'
  return 'step-unknown'
}

function go(routeName: string) {
  router.push({ name: routeName })
}

function dismiss() {
  dismissed.value = true
  sessionStorage.setItem('onboarding-dismissed', '1')
}
</script>

<template>
  <div v-if="visible" class="banner">
    <div class="banner-head">
      <div>
        <h4>👋 还差一点就能跑起来</h4>
        <p class="hint">
          按下面顺序补齐 5 个实体后,设备就可以开始对话了。
        </p>
      </div>
      <div class="head-actions">
        <el-button v-if="nextStep" type="primary" size="small" @click="go(nextStep.route)">
          去补 {{ nextStep.label }} →
        </el-button>
        <el-button size="small" link @click="dismiss">本次隐藏</el-button>
      </div>
    </div>
    <ol class="steps">
      <li v-for="(s, i) in STEPS" :key="s.name" :class="stepStatusClass(s.name)">
        <span class="num">{{ i + 1 }}</span>
        <span class="label">{{ s.label }}</span>
        <span class="step-hint">{{ s.hint }}</span>
        <el-button
          v-if="state?.[s.name]?.status === 'empty'"
          size="small"
          link
          type="primary"
          @click="go(s.route)"
        >
          打开
        </el-button>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.banner {
  background: var(--eid-bg-panel);
  border: 1px solid var(--eid-border);
  border-left: 3px solid var(--eid-accent);
  border-radius: var(--eid-radius);
  padding: 14px 16px;
  margin-bottom: 16px;
}
.banner-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 10px;
}
.banner-head h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--eid-text-primary);
}
.hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--eid-text-secondary);
}
.head-actions { display: flex; gap: 8px; align-items: center; }
.steps {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
}
.steps li {
  display: flex;
  flex-direction: column;
  padding: 8px 10px;
  border-radius: var(--eid-radius-sm);
  background: var(--eid-bg-canvas);
  font-size: 12px;
  gap: 4px;
  min-height: 90px;
  border: 1px solid var(--eid-border);
}
.steps .num {
  display: inline-flex;
  width: 18px;
  height: 18px;
  border-radius: 9px;
  align-items: center;
  justify-content: center;
  background: var(--eid-border);
  color: var(--eid-text-muted);
  font-size: 11px;
  font-weight: 600;
}
.steps .label {
  font-weight: 600;
  color: var(--eid-text-primary);
}
.steps .step-hint {
  color: var(--eid-text-muted);
  font-size: 11px;
  line-height: 1.4;
}
.step-ok { border-color: var(--eid-success, #1bb47e); }
.step-ok .num { background: var(--eid-success, #1bb47e); color: white; }
.step-empty { border-color: var(--eid-accent); }
.step-empty .num { background: var(--eid-accent); color: white; }
.step-unknown { opacity: 0.6; }
</style>
