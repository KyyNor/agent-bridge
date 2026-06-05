import { ref, type Ref } from 'vue'

export function useApi<T>(fetcher: () => Promise<T>): { data: Ref<T | null>; loading: Ref<boolean>; error: Ref<string | null>; load: () => Promise<void> } {
  const data = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    loading.value = true
    error.value = null
    try {
      data.value = await fetcher()
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { data, loading, error, load }
}
