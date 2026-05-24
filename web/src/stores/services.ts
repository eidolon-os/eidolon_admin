import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listServices, type ServiceEntry } from '@/api/services'

export const useServicesStore = defineStore('services', () => {
  const services = ref<ServiceEntry[]>([])
  const loaded = ref(false)
  const loading = ref(false)

  async function load(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      services.value = await listServices()
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function findService(id: string): ServiceEntry | undefined {
    return services.value.find((s) => s.id === id)
  }

  return { services, loaded, loading, load, findService }
})
