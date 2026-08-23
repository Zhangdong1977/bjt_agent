/**
 * 文档上传格式校验工具。
 *
 * 后端使用 mammoth 解析 Word 文档，mammoth 仅支持基于 Office Open XML 的
 * .docx 格式，无法解析 Word 97-2003 的二进制 .doc 格式（解析会失败）。
 * Excel 侧同理：后端使用 openpyxl 解析，仅支持 .xlsx，无法解析
 * Excel 97-2003 的二进制 .xls 格式。
 * 因此在前端提前拦截 .doc / .xls，引导用户转换格式后再上传。
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

/** 判断文件是否为旧版 .xls（Excel 97-2003）二进制格式 */
export function isLegacyXlsFile(file: File): boolean {
  return getFileExtension(file.name) === 'xls'
}

/** 单文件上限：1 GiB。与后端 Settings.max_upload_size_bytes 保持一致。 */
export const MAX_UPLOAD_SIZE_BYTES = 1024 * 1024 * 1024

/** Linux/NFS 单个 UTF-8 文件名组件上限；需包含后端追加的时间戳。 */
export const MAX_STORAGE_FILENAME_BYTES = 255

const INVALID_FILENAME_CHARS = /[<>:"/\\|?*\u0000-\u001f\u007f]/
const WINDOWS_RESERVED_FILENAME = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i
const STORAGE_TIMESTAMP_PLACEHOLDER = '0'.repeat(14)

/** 生成 .doc 旧格式被拦截时的友好提示文案 */
export function legacyDocWarning(filename: string): string {
  return `「${filename}」是 .doc（Word 97-2003）旧版格式，暂不支持解析。请用 Word「另存为 .docx」或「导出为 PDF」后重新上传。`
}

/** 生成 .xls 旧格式被拦截时的友好提示文案 */
export function legacyXlsWarning(filename: string): string {
  return `「${filename}」是 .xls（Excel 97-2003）旧版格式，暂不支持解析。请用 Excel「另存为 .xlsx」后重新上传。`
}

/** 超过 1 GiB 时返回友好提示；大小合法时返回 null。 */
export function uploadSizeWarning(file: File): string | null {
  if (file.size <= MAX_UPLOAD_SIZE_BYTES) return null
  const sizeGiB = file.size / MAX_UPLOAD_SIZE_BYTES
  return `「${file.name}」大小为 ${sizeGiB.toFixed(2)} GB，单个文件最大支持 1 GB。请压缩或拆分后重新上传。`
}

/** 文件名无法安全落盘时返回提示；正常中文及全角标点不会被拦截。 */
export function uploadFilenameWarning(filename: string): string | null {
  const lastDot = filename.lastIndexOf('.')
  const hasExtension = lastDot > 0
  const stem = hasExtension ? filename.slice(0, lastDot) : filename
  const suffix = hasExtension ? filename.slice(lastDot) : ''

  if (
    !filename ||
    filename === '.' ||
    filename === '..' ||
    INVALID_FILENAME_CHARS.test(filename) ||
    WINDOWS_RESERVED_FILENAME.test(stem) ||
    filename.endsWith(' ') ||
    filename.endsWith('.') ||
    stem.endsWith(' ') ||
    stem.endsWith('.')
  ) {
    return '文件名包含系统不支持的特殊字符，请修改文件名后重新上传'
  }

  const storageName = `${stem}_${STORAGE_TIMESTAMP_PLACEHOLDER}${suffix}`
  if (new TextEncoder().encode(storageName).length > MAX_STORAGE_FILENAME_BYTES) {
    return '文件名过长，请缩短文件名后重新上传'
  }
  return null
}

/** 审查/查重文档上传前的完整客户端校验，顺序与后端保持一致。 */
export function uploadDocumentWarning(file: File): string | null {
  return uploadSizeWarning(file) ?? uploadFilenameWarning(file.name)
}
