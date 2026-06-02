<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { issuePairingCode, pairingQrUrl, type PairingCode } from '@/api/agentLegacyProxy'

const tenantId = ref('default')
const userId = ref('')
const templateId = ref('')
const issuing = ref(false)
const code = ref<PairingCode | null>(null)

async function issue() {
  if (!userId.value) {
    ElMessage.warning('请填写 user_id')
    return
  }
  issuing.value = true
  try {
    code.value = await issuePairingCode({
      tenant_id: tenantId.value,
      user_id: userId.value,
      default_template_id: templateId.value || undefined,
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
        <div class="form-grid">
          <el-form-item label="Tenant ID">
            <el-input v-model="tenantId" />
          </el-form-item>
          <el-form-item label="User ID" required>
            <el-input v-model="userId" placeholder="如：alice" />
          </el-form-item>
          <el-form-item label="Default template (可选)">
            <el-input v-model="templateId" placeholder="如：companion-base" />
          </el-form-item>
        </div>
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
        </div>
        <img :src="pairingQrUrl(code.code)" alt="QR" class="qr" />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; }
.title { margin: 0 0 16px 0; font-size: 18px; font-weight: 600; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
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
