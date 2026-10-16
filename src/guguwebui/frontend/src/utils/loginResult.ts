/** 登录接口结果解析（含两步验证挑战），供 AuthContext 与登录页共用 */

export interface LoginResult {
  success: boolean
  message?: string
  /** 账号已启用两步验证：第一步已通过，凭此票据提交验证码 */
  totpTicket?: string
  /** 票据失效（超时 / 错误次数过多），需要重新从第一步登录 */
  ticketExpired?: boolean
}

type ErrorBody = { message?: string; code?: string; data?: { ticket?: string } }

/** 从登录接口的错误响应中解析结果（含两步验证挑战） */
export const parseLoginError = (error: unknown): LoginResult => {
  const data = (error as { response?: { data?: ErrorBody } })?.response?.data
  if (data?.code === 'totp_required' && data.data?.ticket) {
    return { success: false, message: data.message, totpTicket: data.data.ticket }
  }
  return {
    success: false,
    message: data?.message || undefined,
    ticketExpired: data?.code === 'totp_ticket_invalid',
  }
}
