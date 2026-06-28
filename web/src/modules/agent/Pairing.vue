<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { issuePairingCode, pairingQrUrl, type PairingCode } from '@/api/agentRuntime'
import AgentScopeSelector from './components/AgentScopeSelector.vue'
import { useOwnersStore } from '@/stores/owners'

const ownersStore = useOwnersStore()
const ownerId = ref(ownersStore.currentId)
const companionId = ref('')
const issuing = ref(false)
const code = ref<PairingCode | null>(null)

async function issue() {
  if (!ownerId.value || !companionId.value) {
    ElMessage.warning('请选择 owner 和 companion')
    return
  }
  issuing.value = true
  try {
    code.value = await issuePairingCode({
      owner_id: ownerId.value,
      companion_id: companionId.value,
    })
    ElMessage.success(`配对码：${code.value.code}（${code.value.expires_at} 失效）`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e.message)
  } finally {
    issuing.value = false
  }
}
</script>

<template>
  <div class="page">
    <h2 class="title">Device Pairing</h2>

    <el-card>
      <template #header>发起配对</template>
      <el-form>
        <el-form-item label="Runtime identity" required>
          <AgentScopeSelector v-model:owner-id="ownerId" v-model:companion-id="companionId" />
        </el-form-item>
        <el-button type="primary" :loading="issuing" @click="issue">生成配对码</el-button>
      </el-form>
    </el-card>

    <el-card v-if="code" style="margin-top: 16px" class="result-card">
      <template #header>配对信息</template>
      <div class="result">
        <div class="info">
          <div class="row">
            <span class="label">CODE</span>
            <span class="code">{{ code.code }}</span>
          </div>
          <div class="row">
            <span class="label">EXPIRES</span>
            <span class="mono">{{ code.expires_at }}</span>
          </div>
          <div v-if="code.pair_url" class="row">
            <span class="label">PAIR URL</span>
            <span class="mono">{{ code.pair_url }}</span>
          </div>
          <div v-if="code.memory" class="row">
            <span class="label">MEMORY</span>
            <span class="mono">{{ code.memory.memory_realm_id }} · {{ code.memory.mcp_http_url || 'route ready' }}</span>
          </div>
        </div>
        <img :src="pairingQrUrl(code.code)" alt="QR" class="qr" />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.title { margin: 0 0 16px 0; font-size: 18px; font-weight: 600; }
.result-card .result {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 32px;
  align-items: center;
}
.info .row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 10px;
}
.label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--eid-text-muted);
  min-width: 90px;
}
.code {
  font-family: var(--eid-font-mono);
  font-size: 28px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--eid-accent);
}
.mono { font-family: var(--eid-font-mono); font-size: 13px; }
.qr {
  display: block;
  width: 220px;
  height: 220px;
  background: #fff;
  border-radius: 6px;
  padding: 12px;
}
</style>
