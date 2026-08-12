import { createApp } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import './styles/base.css'
import 'driver.js/dist/driver.css'
import { queryClient } from './lib/query'

const app = createApp(App)

app.use(VueQueryPlugin, { queryClient })
app.mount('#app')
