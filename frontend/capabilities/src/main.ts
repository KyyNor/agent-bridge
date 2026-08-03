import { createApp } from 'vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import './styles/base.css'
import { queryClientConfig } from './lib/query'

const app = createApp(App)
const queryClient = new QueryClient(queryClientConfig)

app.use(VueQueryPlugin, { queryClient })
app.mount('#app')
