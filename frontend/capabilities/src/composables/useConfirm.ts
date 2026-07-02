import { reactive } from 'vue'

export interface ConfirmOptions {
  /** 主标题，默认「确认操作」。 */
  title?: string
  /** 正文描述（可多行）。 */
  description?: string
  /** 确认按钮文案，默认「确认」。 */
  confirmText?: string
  /** 取消按钮文案，默认「取消」。alert 模式下无效。 */
  cancelText?: string
  /** 危险操作：确认按钮使用 destructive 红色样式。 */
  destructive?: boolean
}

export interface ConfirmState extends Required<Omit<ConfirmOptions, 'description'>> {
  open: boolean
  description: string
  /** alert 模式：仅显示单个确认按钮，不返回取消。 */
  singleButton: boolean
}

const state = reactive<ConfirmState>({
  open: false,
  title: '确认操作',
  description: '',
  confirmText: '确认',
  cancelText: '取消',
  destructive: false,
  singleButton: false,
})

let resolver: ((value: boolean) => void) | null = null

function apply(opts: string | ConfirmOptions, singleButton: boolean) {
  const o = typeof opts === 'string' ? { description: opts } : opts
  state.open = true
  state.title = o.title ?? (singleButton ? '提示' : '确认操作')
  state.description = o.description ?? ''
  state.confirmText = o.confirmText ?? (singleButton ? '知道了' : '确认')
  state.cancelText = o.cancelText ?? '取消'
  state.destructive = o.destructive ?? false
  state.singleButton = singleButton
}

/** 命令式确认弹窗，返回是否点击确认。 */
export function confirm(opts: string | ConfirmOptions): Promise<boolean> {
  apply(opts, false)
  return new Promise<boolean>(resolve => {
    resolver = resolve
  })
}

/** 命令式提示弹窗（单按钮），resolve 于关闭时。 */
export function alert(opts: string | ConfirmOptions): Promise<void> {
  apply(opts, true)
  return new Promise<void>(resolve => {
    resolver = () => resolve()
  })
}

/** 供 ConfirmHost 调用：用户点击按钮后回填结果。 */
export function resolveConfirm(value: boolean): void {
  if (!state.open) return
  state.open = false
  const r = resolver
  resolver = null
  if (r) r(value)
}

export function useConfirmState(): ConfirmState {
  return state
}
