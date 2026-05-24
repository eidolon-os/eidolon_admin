<script setup lang="ts">
import { onMounted, ref, watch, computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getHierarchy } from '@/api/memory'
import { useMemoryUserStore } from '@/stores/memoryUser'
import MemoryPageShell from './components/MemoryPageShell.vue'

interface DrawerNode { label: string; preview?: string; truncated?: boolean }
interface RoomNode { label: string; drawer_count?: number; drawers?: DrawerNode[]; preview_truncated?: boolean }
interface WingNode { label: string; display_name?: string; room_count?: number; drawer_count?: number; rooms?: RoomNode[] }

const store = useMemoryUserStore()
const maxRecords = ref(8000)
const maxDrawersPerRoom = ref(48)
const loading = ref(false)
const data = ref<Record<string, any>>({})

async function load() {
  if (!store.currentId) return
  loading.value = true
  try {
    const r = await getHierarchy(store.currentId, maxRecords.value, maxDrawersPerRoom.value)
    data.value = r.data
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => store.currentId, load)

function buildTree() {
  const wings = (data.value.configured_wings || data.value.wings || []) as WingNode[]
  return wings.map((w) => ({
    label: w.display_name || w.label || '(wing)',
    rooms: (w.rooms || []) as RoomNode[],
    room_count: w.room_count,
    drawer_count: w.drawer_count,
  }))
}

const tree = computed(() => buildTree())
const palacePath = computed(() => data.value.palace_path || '')
const stewardMode = computed(() => data.value.steward_mode || '')
const totalRecordsScanned = computed(() => data.value.total_records_scanned || 0)
const cappedByMaxRecords = computed(() => data.value.capped_by_max_records || false)
</script>

<template>
  <MemoryPageShell title="Memory Palace Hierarchy">
    <template #default>
      <el-card>
        <template #header>
          <div class="bar">
            <el-form inline>
              <el-form-item label="Max records">
                <el-input-number v-model="maxRecords" :min="1" :max="200000" size="small" />
              </el-form-item>
              <el-form-item label="Max drawers / room">
                <el-input-number v-model="maxDrawersPerRoom" :min="1" :max="1000" size="small" />
              </el-form-item>
            </el-form>
            <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          </div>
        </template>

        <div class="meta-row">
          <div><span class="meta-label">Palace</span><span class="meta-val mono">{{ palacePath || '—' }}</span></div>
          <div><span class="meta-label">Steward</span><span class="meta-val">{{ stewardMode || '—' }}</span></div>
          <div><span class="meta-label">Records scanned</span><span class="meta-val">{{ totalRecordsScanned }}</span></div>
          <el-tag v-if="cappedByMaxRecords" type="warning" size="small">capped</el-tag>
        </div>

        <el-collapse style="margin-top: 12px" v-loading="loading">
          <el-collapse-item
            v-for="(wing, i) in tree" :key="i"
          >
            <template #title>
              <span class="wing-title">{{ wing.label }}</span>
              <el-tag size="small" effect="plain" style="margin-left: 8px">
                {{ wing.room_count || (wing.rooms?.length || 0) }} rooms
                · {{ wing.drawer_count || 0 }} drawers
              </el-tag>
            </template>
            <el-collapse>
              <el-collapse-item
                v-for="(room, j) in wing.rooms" :key="j"
                :title="`${room.label}  (${room.drawer_count || (room.drawers?.length || 0)} drawers)`"
              >
                <ul class="drawer-list">
                  <li v-for="(d, k) in room.drawers" :key="k">
                    <span class="mono">{{ d.label }}</span>
                    <span v-if="d.preview" class="muted">: {{ d.preview }}</span>
                  </li>
                </ul>
                <div v-if="room.preview_truncated" class="muted">…preview truncated</div>
              </el-collapse-item>
            </el-collapse>
          </el-collapse-item>
        </el-collapse>

        <el-empty v-if="!loading && tree.length === 0" description="无 hierarchy 数据" />
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; }
.meta-row { display: flex; align-items: center; gap: 24px; font-size: 13px; flex-wrap: wrap; }
.meta-label { display: block; font-size: 11px; color: var(--eid-text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.meta-val { display: block; color: var(--eid-text-primary); margin-top: 2px; }
.mono { font-family: var(--eid-font-mono); }
.wing-title { font-weight: 600; }
.drawer-list { margin: 0; padding-left: 20px; font-size: 12.5px; }
.drawer-list li { padding: 2px 0; }
.muted { color: var(--eid-text-muted); font-size: 12px; }
</style>
