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

export const knowledgeFirstUseTour: ProductTourDefinition = {
  key: 'knowledge-first-use', version: 1, name: '文档知识新手指南',
  steps: [
    { element: '[data-tour="knowledge-create"]', popover: { title: '创建文档知识', description: '先创建知识库，再进入详情选择默认检索后端与 Agent。', side: 'bottom', align: 'end' } },
    { element: '[data-tour="knowledge-list"]', popover: { title: '从详情管理资料', description: '打开详情后可上传文档、整理目录、查看同步状态，并在检索页验证资料是否可用。', side: 'top', align: 'start' } },
    { element: '[data-tour="knowledge-refresh"]', popover: { title: '按需刷新列表', description: '资料库或文档在其他页面变更后，可刷新此列表查看最新概览。', side: 'bottom', align: 'end' } },
  ],
}

export const servicesFirstUseTour: ProductTourDefinition = {
  key: 'services-first-use', version: 1, name: '能力接入新手指南',
  steps: [
    { element: '[data-tour="services-create"]', popover: { title: '接入 MCP 或 OpenAPI', description: '新建服务时选择来源类型，填写连接信息后保存。认证信息只在服务配置中保存。', side: 'bottom', align: 'end' } },
    { element: '[data-tour="services-list"]', popover: { title: '同步并检查工具', description: '服务保存后在此同步工具；状态和工具数用于判断连接及导入是否成功。', side: 'top', align: 'start' } },
    { element: '[data-tour="services-filter"]', popover: { title: '按状态和类型筛选', description: '用筛选快速定位异常、停用或某一来源的服务，再进入编辑处理。', side: 'bottom', align: 'start' } },
  ],
}

export const toolDebugFirstUseTour: ProductTourDefinition = {
  key: 'tool-debug-first-use', version: 1, name: '工具调试新手指南',
  steps: [
    { element: '[data-tour="tool-debug-selection"]', popover: { title: '选择调试上下文', description: '先选能力平面，再选该平面已允许的服务和工具；权限会按此上下文生效。', side: 'bottom', align: 'start' } },
    { element: '[data-tour="tool-debug-params"]', popover: { title: '填写参数', description: '系统会按输入 Schema 生成 JSON 模板；按实际接口要求修改后再执行。', side: 'right', align: 'start' } },
    { element: '[data-tour="tool-debug-run"]', popover: { title: '执行并核对结果', description: '调试结果会显示在下方，用于验证接入、参数和能力平面授权。', side: 'left', align: 'center' } },
  ],
}

export const profileConfigFirstUseTour: ProductTourDefinition = {
  key: 'profile-config-first-use', version: 1, name: '能力平面配置新手指南',
  steps: [
    { element: '[data-tour="profile-command"]', popover: { title: '先完成接入', description: '复制此命令并在目标项目执行，将该能力平面安装到 Claude Code。', side: 'bottom', align: 'start' } },
    { element: '[data-tour="profile-services"]', popover: { title: '授权服务', description: '仅选择这个能力平面可发现和调用的服务；工具调试也会遵循这里的授权。', side: 'bottom', align: 'start' } },
    { element: '[data-tour="profile-resources"]', popover: { title: '授权资料范围', description: '按需授予文档知识、代码仓库和业务台账，避免把不相关资源带入上下文。', side: 'bottom', align: 'start' } },
    { element: '[data-tour="profile-save"]', popover: { title: '确认保存', description: '所有修改在这里一次保存；高级 Pin 和提示词配置可按需展开。', side: 'top', align: 'end' } },
  ],
}

export const workflowEditorFirstUseTour: ProductTourDefinition = {
  key: 'workflow-editor-first-use', version: 1, name: '工作流编辑器新手指南',
  steps: [
    { element: '[data-tour="workflow-editor-palette"]', popover: { title: '从节点调色板开始', description: '将节点加入画布，再用连线表达依赖或条件分支。总结型工作流会保留其系统输出节点。', side: 'bottom', align: 'start' } },
    { element: '[data-tour="workflow-editor-canvas"]', popover: { title: '在画布编排依赖', description: '点击节点或连线可在右侧编辑其配置；画布空白处可返回工作流信息。', side: 'right', align: 'start' } },
    { element: '[data-tour="workflow-editor-actions"]', popover: { title: '验证后保存或测试', description: '可先用 AI 设计生成草案或测试运行；保存时可选择是否安排受影响任务增量刷新。', side: 'bottom', align: 'end' } },
  ],
}

export const memoryFirstUseTour: ProductTourDefinition = {
  key: 'memory-first-use', version: 1, name: '记忆区块新手指南',
  steps: [
    { element: '[data-tour="memory-create"]', popover: { title: '新建独立记忆区块', description: '每个区块有独立标识、数据目录和 worker，可在能力平面中绑定。', side: 'bottom', align: 'end' } },
    { element: '[data-tour="memory-list"]', popover: { title: '查看状态与绑定', description: '进入详情可以检查 worker 健康、搜索记忆、查看时间线或打开 worker 页面。', side: 'top', align: 'start' } },
    { element: '[data-tour="memory-refresh"]', popover: { title: '刷新运行状态', description: 'worker 状态变化后可刷新列表，再从详情进行进一步诊断。', side: 'bottom', align: 'end' } },
  ],
}

export const scriptsFirstUseTour: ProductTourDefinition = {
  key: 'scripts-first-use', version: 1, name: '脚本管理新手指南',
  steps: [
    { element: '[data-tour="scripts-create"]', popover: { title: '创建受控脚本', description: '脚本需要实现 main(envelope) 并返回 dict；可从新建页开始或编辑已有脚本。', side: 'bottom', align: 'end' } },
    { element: '[data-tour="scripts-list"]', popover: { title: '编辑或运行脚本', description: '从列表进入编辑页，可查看脚本来源、调整代码与契约，并进行测试运行。', side: 'top', align: 'start' } },
    { element: '[data-tour="scripts-editor-actions"]', popover: { title: '保存与测试运行', description: '编辑页提供 AI 设计、保存和运行操作；先保存可确保测试基于最新版本。', side: 'bottom', align: 'end' } },
  ],
}
