import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  archiveOwner,
  createOwner,
  deleteOwner,
  listOwners,
  updateOwner,
  type OwnerCreateRequest,
  type OwnerUpdateRequest,
  type OwnerView,
} from '@/api/eidolonData'

const STORAGE_KEY = 'eidolon-admin.owner_id'

export const useOwnersStore = defineStore('owners', () => {
  const owners = ref<OwnerView[]>([])
  const currentId = ref<string>(localStorage.getItem(STORAGE_KEY) || '')
  const loaded = ref(false)
  const loading = ref(false)

  const currentOwner = computed(() => owners.value.find((owner) => owner.owner_id === currentId.value) || null)

  async function load(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      owners.value = await listOwners()
      const currentValid = owners.value.some((owner) => owner.owner_id === currentId.value)
      if (!currentId.value || !currentValid) {
        currentId.value = owners.value[0]?.owner_id || ''
        persist()
      }
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function setCurrent(ownerId: string) {
    currentId.value = ownerId
    persist()
  }

  async function createAndSelect(payload: OwnerCreateRequest): Promise<OwnerView> {
    const owner = await createOwner(payload)
    owners.value = [owner, ...owners.value.filter((item) => item.owner_id !== owner.owner_id)]
    setCurrent(owner.owner_id)
    loaded.value = true
    return owner
  }

  async function updateLocal(ownerId: string, payload: OwnerUpdateRequest): Promise<OwnerView> {
    const owner = await updateOwner(ownerId, payload)
    owners.value = owners.value.map((item) => item.owner_id === owner.owner_id ? owner : item)
    return owner
  }

  async function archiveLocal(ownerId: string): Promise<OwnerView> {
    const owner = await archiveOwner(ownerId)
    owners.value = owners.value.map((item) => item.owner_id === owner.owner_id ? owner : item)
    return owner
  }

  async function deleteLocal(ownerId: string, confirmOwnerId: string) {
    const result = await deleteOwner(ownerId, confirmOwnerId)
    owners.value = owners.value.filter((item) => item.owner_id !== ownerId)
    if (currentId.value === ownerId) {
      currentId.value = owners.value[0]?.owner_id || ''
      persist()
    }
    loaded.value = true
    return result
  }

  function persist() {
    if (currentId.value) localStorage.setItem(STORAGE_KEY, currentId.value)
    else localStorage.removeItem(STORAGE_KEY)
  }

  return {
    owners,
    currentId,
    currentOwner,
    loaded,
    loading,
    load,
    setCurrent,
    createAndSelect,
    updateLocal,
    archiveLocal,
    deleteLocal,
  }
})
