<script setup lang="ts">
/**
 * Shared dropdown for picking an admin-registered ``user_id`` on legacy
 * registry and memory pages. Backed by ``/api/users``,
 * which means: only users known to admin (and therefore to memory)
 * show up here.
 *
 * Why a shared component instead of inline el-select per page:
 *   - The "free-text input + default 'tester' string" pattern silently
 *     burned anyone trying to test against a real user — memory had
 *     no palace for ``tester``, so recall always missed. Forcing a
 *     dropdown of known users removes that whole failure mode.
 *   - Each callsite would otherwise reimplement the load / error /
 *     unhealthy-user badge logic. One component keeps it consistent.
 *
 * Surface: ``v-model`` binds the chosen user_id (or null when nothing
 * picked yet). ``onPick`` fires once a real user is chosen — useful
 * for forms that want to reset other dependent fields. ``size`` and
 * ``placeholder`` mirror Element Plus conventions so this drops into
 * existing layouts cleanly.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listUsers, type UserView } from '@/api/users'
import { extractErrorMessage } from '@/utils/format'
import { userHealthSuffix } from '@/utils/userHealth'

const props = withDefaults(
  defineProps<{
    modelValue: string | null
    size?: 'small' | 'default' | 'large'
    placeholder?: string
    width?: string
    /** Auto-select the first user once loaded (default: true). For
     *  forms where "no selection" is a meaningful state, pass false. */
    autoSelectFirst?: boolean
  }>(),
  {
    size: 'small',
    placeholder: '选择一个 user',
    width: '220px',
    autoSelectFirst: true,
  },
)
const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
  (e: 'pick', user: UserView): void
}>()

const users = ref<UserView[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await listUsers()
    users.value = r.users
    // Default-pick the first user only when caller asked AND nothing's
    // already selected — avoids stomping a value passed in via parent.
    if (
      props.autoSelectFirst &&
      !props.modelValue &&
      r.users.length > 0
    ) {
      const first = r.users[0]
      emit('update:modelValue', first.spec.user_id)
      emit('pick', first)
    }
  } catch (e: any) {
    ElMessage.error(`加载用户列表失败: ${extractErrorMessage(e)}`)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const value = computed({
  get: () => props.modelValue,
  set: (v: string | null) => emit('update:modelValue', v),
})

function onChange(v: string | null) {
  if (!v) return
  const picked = users.value.find((u) => u.spec.user_id === v)
  if (picked) emit('pick', picked)
}

// If parent clears the value (e.g. picked user got deleted), don't
// resurrect anything — but if it sets a non-existent id, warn so the
// operator knows the dropdown can't show it.
watch(
  () => props.modelValue,
  (v) => {
    if (!v || users.value.length === 0) return
    const exists = users.value.some((u) => u.spec.user_id === v)
    if (!exists) {
      ElMessage.warning(`user "${v}" 不在已注册用户列表中`)
    }
  },
)

function healthBadge(u: UserView): string {
  return userHealthSuffix(u.health)
}
</script>

<template>
  <el-select
    v-model="value"
    :placeholder="placeholder"
    :size="size"
    :loading="loading"
    :style="{ width }"
    clearable
    filterable
    @change="onChange"
  >
    <el-option
      v-for="u in users"
      :key="u.spec.user_id"
      :label="`${u.spec.display_name} (${u.spec.user_id})${healthBadge(u)}`"
      :value="u.spec.user_id"
    />
  </el-select>
</template>
