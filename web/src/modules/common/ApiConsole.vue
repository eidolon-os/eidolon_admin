<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { gatewayCall } from '@/api/services'

const route = useRoute()

const method = ref<'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'>('GET')
const path = ref('')
const body = ref('')
const authorization = ref('')
const loading = ref(false)
const status = ref<number | null>(null)
const response = ref<any>(null)
const error = ref<string>('')

async function send() {
  loading.value = true
  error.value = ''
  status.value = null
  try {
    const parsedBody = body.value.trim() ? JSON.parse(body.value) : undefined
    const resp = await gatewayCall(
      route.params.serviceId as string,
      path.value,
      {
        method: method.value,
        data: parsedBody,
        headers: authorization.value.trim()
          ? { Authorization: authorization.value.trim() }
          : undefined,
      },
    )
    status.value = resp.status
    response.value = resp.data
  } catch (e: any) {
    status.value = e?.response?.status || null
    error.value = e?.response?.data ? JSON.stringify(e.response.data, null, 2) : String(e)
    response.value = null
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-card>
    <template #header>
      <span>API Console — {{ route.params.serviceId }}</span>
      <span style="color: var(--eid-text-secondary); margin-left: 8px; font-size: 12px">
        实际请求：/api/services/{{ route.params.serviceId }}/&lt;path&gt;
      </span>
    </template>
    <el-form inline>
      <el-form-item label="方法">
        <el-select v-model="method" style="width: 110px">
          <el-option v-for="m in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']" :key="m" :label="m" :value="m" />
        </el-select>
      </el-form-item>
      <el-form-item label="路径" style="flex: 1">
        <el-input v-model="path" placeholder="如：personas/templates" style="width: 360px" @keyup.enter="send" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="send">发送</el-button>
      </el-form-item>
    </el-form>
    <el-form-item label="Authorization（仅 passthrough 服务）">
      <el-input v-model="authorization" type="password" show-password placeholder="Bearer ey..." />
    </el-form-item>
    <el-form-item label="Body (JSON)" v-if="['POST', 'PUT', 'PATCH'].includes(method)">
      <el-input v-model="body" type="textarea" :rows="5" placeholder='{"key": "value"}' />
    </el-form-item>

    <div v-if="status !== null" class="result">
      <div class="meta">
        <el-tag :type="status < 300 ? 'success' : status < 500 ? 'warning' : 'danger'">
          HTTP {{ status }}
        </el-tag>
      </div>
      <pre v-if="response">{{ JSON.stringify(response, null, 2) }}</pre>
      <pre v-if="error" class="error">{{ error }}</pre>
    </div>
  </el-card>
</template>

<style scoped>
.result {
  margin-top: 16px;
}
.meta {
  margin-bottom: 8px;
}
pre {
  background: var(--eid-bg-inset);
  color: var(--eid-text-primary);
  padding: 12px 16px;
  border-radius: 6px;
  overflow: auto;
  max-height: 480px;
}
pre.error {
  background: rgba(239, 68, 68, 0.12);
}
</style>
