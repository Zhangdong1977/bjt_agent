/**
 * 工具调用显示工具函数
 * 将工具名称和参数转换为用户友好的中文描述
 */

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  search_tender_doc: '搜索文档',
  search_doc: '搜索文档',
  rag_search: '搜索知识库',
  comparator: '内容比对',
  compare_bid: '标书比对',
  decide_merge: '合并决策',
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  bash: '终端命令',
  bash_output: '命令输出',
  bash_kill: '终止命令',
  get_skill: '获取技能',
  record_note: '记录笔记',
  recall_notes: '回忆笔记',
  understand_image: '理解图片',
  web_search: '网络搜索',
}

function truncate(value: string, max: number): string {
  return value.length > max ? value.slice(0, max) + '...' : value
}

function getDocType(args: Record<string, any>): string {
  const docType = args['文档类型'] || args['doc_type'] || ''
  return docType === 'tender' ? '招标书' : '投标书'
}

function fallbackDescription(args: Record<string, any>): string {
  return Object.entries(args)
    .map(([key, value]) => {
      let displayValue: string
      if (typeof value === 'boolean') {
        displayValue = value ? '是' : '否'
      } else if (typeof value === 'object') {
        const json = JSON.stringify(value)
        displayValue = json.length > 50 ? json.slice(0, 50) + '...' : json
      } else if (typeof value === 'string' && value.length > 100) {
        displayValue = value.slice(0, 100) + '...'
      } else {
        displayValue = String(value)
      }
      return `${key}: ${displayValue}`
    })
    .join(', ')
}

export function getToolDisplayName(toolName: string): string {
  return TOOL_DISPLAY_NAMES[toolName] || toolName
}

export function formatToolCallDescription(
  toolName: string,
  args: Record<string, any>
): string {
  if (!args || Object.keys(args).length === 0) {
    return getToolDisplayName(toolName)
  }

  switch (toolName) {
    case 'search_tender_doc':
    case 'search_doc': {
      const docLabel = getDocType(args)
      const query = args['query'] || args['查询']
      if (query) {
        return `在${docLabel}中查询「${truncate(query, 30)}」`
      }
      if (args['full_content']) {
        return `读取${docLabel}全文`
      }
      if (args['chunk'] !== undefined) {
        return `读取${docLabel}（第${Number(args['chunk']) + 1}块）`
      }
      return `查询${docLabel}`
    }

    case 'compare_bid':
    case 'comparator':
      return '比对招标要求与投标内容'

    case 'rag_search': {
      const query = args['query']
      if (query) {
        return `在知识库中搜索「${truncate(query, 30)}」`
      }
      return '搜索知识库'
    }

    case 'decide_merge':
      return '合并审查结果'

    case 'understand_image':
      return '理解图片内容'

    case 'web_search': {
      const query = args['query']
      if (query) {
        return `网络搜索「${truncate(query, 30)}」`
      }
      return '网络搜索'
    }

    case 'write_file':
      return '写入文件'

    case 'read_file':
      return '读取文件'

    case 'edit_file':
      return '编辑文件'

    case 'bash':
      return '执行终端命令'

    default:
      return fallbackDescription(args)
  }
}
