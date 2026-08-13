import { ref } from 'vue'

/** 全站认证失败状态：任一业务请求返回 401 后，统一替换当前页面内容。 */
export const authenticationRequired = ref(false)

export const authenticationRequiredPresentation = {
  title: '需要先完成登录',
  description: '当前浏览器没有有效的 Agent Bridge 登录信息，因此暂时无法加载此页面。请从统一登录入口重新进入 Agent Bridge；如果仍无法访问，请联系管理员确认账号已开通。',
}

export const accessDeniedPresentation = {
  title: '暂无页面访问权限',
  description: '已确认你的登录身份，但账号还没有访问此页面所需的小组权限。请联系管理员开通权限后重试。',
}

export function reportAuthenticationRequired(status: number): void {
  if (status === 401) authenticationRequired.value = true
}
