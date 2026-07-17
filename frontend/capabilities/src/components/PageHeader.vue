<script setup lang="ts">
/**
 * 统一页头 —— 收敛「标题 + 主操作 + 搜索/筛选」为单页头区，
 * 消灭全局 banner 与各视图内部工具栏的 chrome 冗余。
 *
 * 内容区只剩内容；行内操作（详情/删除）仍留表格行，不进页头。
 *
 * 挂载约定：
 *   各视图通过 <Teleport to="#ph-actions"> 投放按钮（新建/刷新/导出…）
 *             通过 <Teleport to="#ph-filters"> 投放搜索/筛选/排序
 *   两个目标均带 empty:hidden —— 无投放内容时自动折叠，不占行、不出空 border。
 *   使用 <Teleport defer> 规避视图异步挂载的时序问题。
 *
 * 色彩纪律：单主色蓝仅出现在 primary 按钮 / active Tab / 焦点 ring；
 *           状态色仅用软底深字（*-soft）；其余一律中性 token。
 */
withDefaults(
  defineProps<{
    title: string
    description?: string
    /** 长表格页开启吸顶 */
    sticky?: boolean
  }>(),
  { description: '', sticky: false },
)
</script>

<template>
  <header
    class="bg-card px-7"
    :class="[
      'border-b border-border',
      sticky && 'sticky top-0 z-30',
    ]"
  >
    <!-- 主行：标题左 / 操作右 -->
    <div class="flex items-start justify-between gap-4 py-3.5">
      <div class="min-w-0">
        <h1 class="text-base font-semibold text-foreground">{{ title }}</h1>
        <p v-if="description" class="mt-0.5 text-[13px] text-muted-foreground">{{ description }}</p>
      </div>
      <!-- Teleport 目标：主操作按钮（primary 固定最右） -->
      <div id="ph-actions" class="flex min-h-9 shrink-0 flex-wrap items-center justify-end gap-2 empty:hidden" />
    </div>
    <!-- Teleport 目标：搜索 / 筛选 / 排序；无内容时整行隐藏（empty:hidden 同时去掉 padding） -->
    <div id="ph-filters" class="flex min-h-9 flex-wrap items-center gap-2 pb-3.5 empty:hidden empty:pb-0" />
  </header>
</template>
