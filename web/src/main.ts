import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'

// Order matters: theme.css declares our :root tokens; element-overrides.css
// remaps Element Plus dark vars to those tokens.
import './styles/theme.css'
import './styles/element-overrides.css'
import './styles/admin-layout.css'

// Activate Element Plus dark mode by tagging the root element.
document.documentElement.classList.add('dark')

const app = createApp(App)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component as any)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
