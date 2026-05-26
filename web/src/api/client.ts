import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

/**
 * Shared axios instance.
 *
 * The response interceptor pops an ElMessage toast on every error by
 * default — useful for one-shot calls where there's no specific UI to
 * surface the error in. But components that DO have their own error
 * surface (a banner, inline form errors, etc.) get double-notifications:
 * the interceptor's generic toast plus the component's specific message.
 *
 * Set ``config.suppressToast = true`` on a per-request basis to opt out:
 *
 *     await client.get('/some/path', { suppressToast: true })
 *     // ↑ caller handles error UI; no interceptor toast
 *
 * Polling code (Overview pages, SystemHealthPanel) should use this so
 * a backend hiccup during periodic refresh doesn't spam the user with
 * a toast every 5s.
 */
declare module 'axios' {
  export interface AxiosRequestConfig {
    /** When true, the response error interceptor stays silent. */
    suppressToast?: boolean
  }
}

const client = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

client.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    const cfg = error.config as InternalAxiosRequestConfig | undefined
    if (cfg?.suppressToast) {
      return Promise.reject(error)
    }
    const detail = (error.response?.data as any)?.upstream_error
      || (error.response?.data as any)?.detail
      || error.message
    ElMessage.error(`请求失败：${detail}`)
    return Promise.reject(error)
  },
)

export default client
