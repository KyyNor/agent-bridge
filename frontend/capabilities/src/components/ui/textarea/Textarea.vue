<script setup lang="ts">
import { nextTick, ref } from "vue"
import type { HTMLAttributes } from "vue"
import { useVModel } from "@vueuse/core"
import { cn } from "@/lib/utils"

const props = defineProps<{
  class?: HTMLAttributes["class"]
  defaultValue?: string | number
  modelValue?: string | number
}>()

const emits = defineEmits<{
  (e: "update:modelValue", payload: string | number): void
}>()

const modelValue = useVModel(props, "modelValue", emits, {
  passive: true,
  defaultValue: props.defaultValue,
})

const textareaRef = ref<HTMLTextAreaElement>()

function focus() {
  textareaRef.value?.focus()
}

function insertText(value: string) {
  const textarea = textareaRef.value
  if (!textarea) return
  const current = String(modelValue.value ?? "")
  const start = textarea.selectionStart ?? current.length
  const end = textarea.selectionEnd ?? start
  modelValue.value = `${current.slice(0, start)}${value}${current.slice(end)}`
  void nextTick(() => {
    textarea.focus()
    textarea.setSelectionRange(start + value.length, start + value.length)
  })
}

defineExpose({ focus, insertText })
</script>

<template>
  <textarea
    ref="textareaRef"
    v-model="modelValue"
    data-slot="textarea"
    :class="cn('border-input dark:bg-input/30 focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:aria-invalid:border-destructive/50 disabled:bg-input/50 dark:disabled:bg-input/80 rounded-md border bg-transparent px-2.5 py-2 text-base transition-colors focus-visible:ring-3 aria-invalid:ring-3 md:text-sm flex min-h-16 w-full outline-none placeholder:text-placeholder disabled:cursor-not-allowed disabled:opacity-50', props.class)"
  />
</template>
