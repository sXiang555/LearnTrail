export const ARTICLE_LIMIT = 200000
export const EXCERPT_LIMIT = 5000
export const MARKDOWN_FILE_LIMIT = 1024 * 1024
export const sourceLabels = { original: '原创文章', reprint: '转载文章', link: '外部资料' }
export const sourceType = article => article?.sourceType || 'original'
export const sourceLabel = article => sourceLabels[sourceType(article)] || '文章'
export const isReference = article => sourceType(article) === 'link'

export function safeSourceUrl(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''
  } catch { return '' }
}
