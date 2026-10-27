/**
 * 网页终端的断线策略：终端是 Agent 访问端，不是通用终端。
 *
 * 会话真的结束后必须停止自动重连并提示关闭页面；只对网络抖动保留少量重试，
 * 且“连上即断”不得把重试计数刷回，否则会退化成无限重连。
 */

/** 自动重试次数上限；超过后只能由用户手动重新连接。 */
export const TERMINAL_MAX_RETRIES = 2

/** 连接稳定存活该时长后才重置重试计数。 */
export const TERMINAL_STABLE_RESET_MS = 10_000

export type TerminalCloseAction = 'ended' | 'stop' | 'retry'

/** Agent 结束（4000）或终端被主动结束（4001）：不再重连。 */
const ENDED_CODES = new Set([4000, 4001])
/** 业务性拒绝：占用、鉴权/能力平面失效、输出过快。 */
const STOP_CODES = new Set([4009, 1008, 1013])

export function terminalCloseAction(
  code: number,
  attempts: number,
  hasConnected: boolean,
): TerminalCloseAction {
  if (ENDED_CODES.has(code)) return 'ended'
  if (STOP_CODES.has(code)) return 'stop'
  if (!hasConnected || attempts >= TERMINAL_MAX_RETRIES) return 'stop'
  return 'retry'
}

export function terminalCloseMessage(code: number, exitCode: number | null = null): string {
  if (code === 4000 || code === 4001) {
    const suffix = exitCode === null ? '' : `（${exitCode}）`
    return `Agent 已退出${suffix}，建议关闭页面后重新进入工作台`
  }
  if (code === 4009) return '该终端已在其他页面连接，请先关闭另一页面'
  if (code === 1008) return '无法连接终端，请检查登录状态并重新进入'
  if (code === 1013) return '终端输出过快，已停止自动重连；可手动重新连接'
  return '连接已断开，已停止自动重连；可手动重新连接'
}

export function terminalRetryDelay(attempts: number): number {
  return Math.min(500 * 2 ** attempts, 4000)
}
