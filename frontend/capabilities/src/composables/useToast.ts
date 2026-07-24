import { readonly, ref } from 'vue'

export type ToastVariant = 'default' | 'success' | 'error' | 'warning'

export interface ToastOptions {
  title: string
  description?: string
  variant?: ToastVariant
  duration?: number
}

export interface ToastItem extends Required<Omit<ToastOptions, 'duration'>> {
  id: number
}

const items = ref<ToastItem[]>([])
let nextId = 1

export function useToast() {
  function dismiss(id: number) {
    items.value = items.value.filter(item => item.id !== id)
  }

  function toast(options: ToastOptions) {
    const id = nextId++
    const item: ToastItem = {
      id,
      title: options.title,
      description: options.description || '',
      variant: options.variant || 'default',
    }
    items.value = [...items.value, item]
    const duration = options.duration ?? (item.variant === 'error' ? 7000 : 4500)
    if (duration > 0) window.setTimeout(() => dismiss(id), duration)
    return id
  }

  return { items: readonly(items), toast, dismiss }
}
