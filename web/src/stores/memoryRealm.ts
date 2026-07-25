import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listMemoryRealms, type MemoryRealmDetail } from '@/api/memory'

const STORAGE_KEY = 'eidolon-admin.memory.memory_realm_id'

export const useMemoryRealmStore = defineStore('memoryRealm', () => {
  const realms = ref<MemoryRealmDetail[]>([])
  const currentId = ref<string>(localStorage.getItem(STORAGE_KEY) || '')
  const loading = ref(false)
  const loaded = ref(false)
  const requestedRealmId = ref('')
  const requestedCompanionId = ref('')
  const currentRealm = computed(() =>
    realms.value.find((r) => r.memory_realm_id === currentId.value),
  )

  async function load(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      const data = await listMemoryRealms()
      realms.value = data.realms
      const scopedRealm = data.realms.find(
        (item) => item.memory_realm_id === requestedRealmId.value,
      ) || data.realms.find(
        (item) => item.companion_id === requestedCompanionId.value && item.enabled,
      ) || data.realms.find(
        (item) => item.companion_id === requestedCompanionId.value,
      )
      if (scopedRealm) setCurrent(scopedRealm.memory_realm_id)
      const currentValid = data.realms.some((r) => r.memory_realm_id === currentId.value)
      const backendDefault = data.realms.find(
        (r) => r.memory_realm_id === data.default_memory_realm_id && r.enabled,
      )
      const enabled = data.realms.find((r) => r.enabled)
      if (!currentId.value || !currentValid) {
        currentId.value = (backendDefault || enabled || data.realms[0])?.memory_realm_id || ''
        if (currentId.value) localStorage.setItem(STORAGE_KEY, currentId.value)
        else localStorage.removeItem(STORAGE_KEY)
      }
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function setCurrent(memoryRealmId: string) {
    currentId.value = memoryRealmId
    if (memoryRealmId) localStorage.setItem(STORAGE_KEY, memoryRealmId)
    else localStorage.removeItem(STORAGE_KEY)
  }

  function selectForCompanion(companionId: string): boolean {
    const realm = realms.value.find(
      (item) => item.companion_id === companionId && item.enabled,
    ) || realms.value.find((item) => item.companion_id === companionId)
    if (!realm) return false
    setCurrent(realm.memory_realm_id)
    return true
  }

  function setRouteScope(memoryRealmId: string, companionId: string) {
    requestedRealmId.value = memoryRealmId
    requestedCompanionId.value = companionId
    if (!loaded.value) return
    if (
      memoryRealmId
      && realms.value.some((item) => item.memory_realm_id === memoryRealmId)
    ) {
      setCurrent(memoryRealmId)
    } else if (companionId) {
      selectForCompanion(companionId)
    }
  }

  function findCurrent(): MemoryRealmDetail | undefined {
    return currentRealm.value
  }

  return {
    realms,
    currentId,
    currentRealm,
    loading,
    loaded,
    load,
    setCurrent,
    selectForCompanion,
    setRouteScope,
    findCurrent,
  }
})
