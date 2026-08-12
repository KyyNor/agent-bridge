export type TourPopover = {
  title: string
  description: string
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
}

export type ProductTourStep = {
  element: string
  popover: TourPopover
}

/** 导览脚本与用户完成状态解耦：变更 version 即可向已完成用户重新展示。 */
export type ProductTourDefinition = {
  key: string
  version: number
  name: string
  steps: ProductTourStep[]
}

export const workflowFirstUseTour: ProductTourDefinition = {
  key: 'workflow-first-use',
  version: 1,
  name: '工作流新手指南',
  steps: [
    {
      element: '[data-tour="workflow-create"]',
      popover: {
        title: '从新建工作流开始',
        description: '新建后可在画布中组合任务、Agent、脚本与输出节点，再保存为可重复运行的流程。',
        side: 'bottom',
        align: 'end',
      },
    },
    {
      element: '[data-tour="workflow-import"]',
      popover: {
        title: '导入已有定义',
        description: '已有的 agent-bridge.workflow 文件可以先预览，再按需新建或覆盖导入。',
        side: 'bottom',
        align: 'end',
      },
    },
    {
      element: '[data-tour="workflow-list"]',
      popover: {
        title: '在这里管理与运行',
        description: '打开工作流详情可查看任务、产物、运行记录和版本历史，并从详情页发起运行。',
        side: 'top',
        align: 'start',
      },
    },
  ],
}
