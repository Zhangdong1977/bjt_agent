/**
 * 文档上传格式校验工具。
 *
 * 后端使用 mammoth 解析 Word 文档，mammoth 仅支持基于 Office Open XML 的
 * .docx 格式，无法解析 Word 97-2003 的二进制 .doc 格式（解析会失败）。
 * 因此在前端提前拦截 .doc，引导用户转换为 .docx 或 PDF 再上传。
 */

/** 提取文件扩展名（小写，不含点）。例如 "report.DOCX" -> "docx" */
export function getFileExtension(filename: string): string {
  const idx = filename.lastIndexOf('.')
  return idx === -1 ? '' : filename.slice(idx + 1).toLowerCase()
}

/** 判断文件是否为旧版 .doc（Word 97-2003）二进制格式 */
export function isLegacyDocFile(file: File): boolean {
  return getFileExtension(file.name) === 'doc'
}

/** 生成 .doc 旧格式被拦截时的友好提示文案 */
export function legacyDocWarning(filename: string): string {
  return `「${filename}」是 .doc（Word 97-2003）旧版格式，暂不支持解析。请用 Word「另存为 .docx」或「导出为 PDF」后重新上传。`
}
