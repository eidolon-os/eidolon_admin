<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createMemory } from '@/api/memory'
import { useMemoryRealmStore } from '@/stores/memoryRealm'
import MemoryPageShell from './components/MemoryPageShell.vue'

const store = useMemoryRealmStore()
const wing = ref('Wing_Profile')
const room = ref('profile_core')
const text = ref('')
const metadataJson = ref('{}')
const submitting = ref(false)
const lastResult = ref<{ ok: boolean; detail: string } | null>(null)

async function onSubmit() {
  if (!text.value.trim()) return
  let metadata: Record<string, any> = {}
  try {
    metadata = metadataJson.value.trim() ? JSON.parse(metadataJson.value) : {}
  } catch (e: any) {
    ElMessage.error(`metadata JSON 解析失败：${e.message}`)
    return
  }
  submitting.value = true
  lastResult.value = null
  try {
    const r = await createMemory({
      memory_realm_id: store.currentId,
      wing: wing.value,
      room: room.value,
      text: text.value,
      metadata,
    })
    lastResult.value = { ok: true, detail: r.detail || JSON.stringify(r) }
    ElMessage.success('已提交')
    text.value = ''
  } catch (e: any) {
    lastResult.value = { ok: false, detail: e?.response?.data?.detail || e.message }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <MemoryPageShell title="写入 Conversation Turn">
    <template #default>
      <el-card>
        <el-form label-position="top">
          <div class="grid">
            <el-form-item label="Wing">
              <el-input v-model="wing" />
            </el-form-item>
            <el-form-item label="Room">
              <el-input v-model="room" />
            </el-form-item>
          </div>
          <el-form-item label="Text" required>
            <el-input v-model="text" type="textarea" :rows="6" placeholder="user 这一轮说了什么…" />
          </el-form-item>
          <el-form-item label="Metadata (JSON)">
            <el-input v-model="metadataJson" type="textarea" :rows="3" placeholder='{"key": "value"}' />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              :disabled="!text.trim()"
              :loading="submitting"
              @click="onSubmit"
            >
              发布到 NATS
            </el-button>
            <span class="hint">
              → eidolon.memory.turn.&lt;encoded realm token&gt;
            </span>
          </el-form-item>
        </el-form>

        <el-alert
          v-if="lastResult"
          :type="lastResult.ok ? 'success' : 'error'"
          :closable="false"
          show-icon
        >
          <template #title>{{ lastResult.detail }}</template>
        </el-alert>
      </el-card>
    </template>
  </MemoryPageShell>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.hint {
  font-size: 12px;
  margin-left: 12px;
  color: var(--eid-text-muted);
  font-family: var(--eid-font-mono);
}
</style>
