/**
 * 提取后端错误响应里面向用户的文案。
 *
 * 后端错误体两种形态：detail 为字符串（如"项目不存在或无权访问"），
 * 或为对象且含 message 字段（402 余额不足、409 已有检查任务在执行等计费闸门）。
 * 取不到时返回空串，由调用方回退到各自的兜底文案。
 */
export function apiErrorText(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null | undefined)?.response
    ?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === 'string' && msg.trim()) return msg
  }
  return ''
}
