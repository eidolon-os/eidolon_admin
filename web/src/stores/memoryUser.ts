import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listMemoryUsers, type MemoryUserDetail } from '@/api/memory'

const STORAGE_KEY = 'eidolon-admin.memory.user_id'

/**
 * Single source of truth for "which memory user am I operating on".
 *
 * - Selection persisted in localStorage so refresh keeps the chosen user.
 * - Loaded eagerly when the first memory page mounts; the selector in the
 *   AdminLayout header drives changes.
 */
export const useMemoryUserStore = defineStore('memoryUser', () => {
  const users = ref<MemoryUserDetail[]>([])
  const currentId = ref<string>(localStorage.getItem(STORAGE_KEY) || '')
  const loading = ref(false)
  const loaded = ref(false)

  async function load(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      const data = await listMemoryUsers()
      users.value = data.users
      // Pick a sensible default: persisted choice if still valid, else first enabled.
      if (!currentId.value || !data.users.some((u) => u.user_id === currentId.value)) {
        const enabled = data.users.find((u) => u.enabled)
        currentId.value = (enabled || data.users[0])?.user_id || ''
        if (currentId.value) localStorage.setItem(STORAGE_KEY, currentId.value)
      }
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function setCurrent(userId: string) {
    currentId.value = userId
    if (userId) localStorage.setItem(STORAGE_KEY, userId)
    else localStorage.removeItem(STORAGE_KEY)
  }

  function findCurrent(): MemoryUserDetail | undefined {
    return users.value.find((u) => u.user_id === currentId.value)
  }

  return { users, currentId, loading, loaded, load, setCurrent, findCurrent }
})
