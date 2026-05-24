import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'

const client = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

client.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    const detail = (error.response?.data as any)?.upstream_error
      || (error.response?.data as any)?.detail
      || error.message
    ElMessage.error(`请求失败：${detail}`)
    return Promise.reject(error)
  },
)

export default client
