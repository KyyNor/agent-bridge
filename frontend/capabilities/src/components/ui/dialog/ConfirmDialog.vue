<script setup lang="ts">
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './index'
import { Button } from '@/components/ui/button'
import { AlertTriangle } from 'lucide-vue-next'
import { resolveConfirm, useConfirmState } from '@/composables/useConfirm'

const state = useConfirmState()

function onCancel() {
  resolveConfirm(false)
}

function onConfirm() {
  resolveConfirm(true)
}
</script>

<template>
  <Dialog :open="state.open" @update:open="(v: boolean) => { if (!v) resolveConfirm(state.singleButton) }">
    <DialogContent class="sm:max-w-[420px]" :show-close-button="false">
      <DialogHeader>
        <div class="flex items-start gap-3">
          <div
            v-if="state.destructive && !state.singleButton"
            class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive"
          >
            <AlertTriangle :size="16" />
          </div>
          <div class="min-w-0 flex-1">
            <DialogTitle>{{ state.title }}</DialogTitle>
            <DialogDescription v-if="state.description" class="mt-1.5 whitespace-pre-wrap">
              {{ state.description }}
            </DialogDescription>
          </div>
        </div>
      </DialogHeader>
      <DialogFooter class="gap-2 sm:gap-2">
        <Button v-if="!state.singleButton" variant="outline" type="button" @click="onCancel">
          {{ state.cancelText }}
        </Button>
        <Button
          :variant="state.destructive ? 'destructive' : 'default'"
          type="button"
          @click="onConfirm"
        >
          {{ state.confirmText }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
