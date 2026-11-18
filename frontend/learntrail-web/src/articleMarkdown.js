import MarkdownIt from 'markdown-it'
import createDOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import sql from 'highlight.js/lib/languages/sql'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import css from 'highlight.js/lib/languages/css'
import { ARTICLE_LIMIT, MARKDOWN_FILE_LIMIT, safeSourceUrl } from './articleMeta.js'

for (const [name, grammar] of Object.entries({ java, javascript, python, json, bash, sql, xml, yaml, css })) {
  hljs.registerLanguage(name, grammar)
}
const md = new MarkdownIt({
  html: false,
  linkify: true,
  maxNesting: 20,
  highlight(code, language) {
    if (code.length > 20000 || !language || !hljs.getLanguage(language)) return ''
    try { return hljs.highlight(code, { language, ignoreIllegals: true }).value } catch { return '' }
  }
})
const defaultValidate = md.validateLink.bind(md)
md.validateLink = value => defaultValidate(value) && (value.startsWith('#') || /^\/(?!\/)/.test(value) || !!safeSourceUrl(value))
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  const token = tokens[index]
  if (/^https?:\/\//i.test(token.attrGet('href') || '')) {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
  }
  return self.renderToken(tokens, index, options)
}
const imageRule = md.renderer.rules.image
md.renderer.rules.image = (tokens, index, options, env, self) => {
  tokens[index].attrSet('loading', 'lazy')
  tokens[index].attrSet('decoding', 'async')
  tokens[index].attrSet('referrerpolicy', 'no-referrer')
  return imageRule(tokens, index, options, env, self)
}
const purifiers = new WeakMap()
const textOf = inline => (inline.children || []).map(token =>
  token.type === 'softbreak' || token.type === 'hardbreak' ? ' ' : token.content || '').join('').trim()
const slugOf = text => text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N} _-]/gu, '')
  .trim().replace(/\s+/g, '-').slice(0, 100) || 'section'

export function renderMarkdown(source, ownerWindow, { prefix = 'article', title = '', images = true } = {}) {
  if (source.length > ARTICLE_LIMIT) throw new Error('正文最多 20 万字符')
  const env = {}
  const tokens = md.parse(source, env)
  // 文件中的同名标题仍保留在源码中，阅读时由页面标题展示一次。
  if (tokens[0]?.type === 'heading_open' && tokens[0].tag === 'h1' && textOf(tokens[1]) === title.trim()) tokens.splice(0, 3)
  const minLevel = tokens.reduce((min, token) => token.type === 'heading_open' ? Math.min(min, Number(token.tag.slice(1))) : min, 6)
  const toc = []
  const used = new Set()
  const namespace = prefix.replace(/[^a-z0-9_-]/gi, '-')
  tokens.forEach((token, index) => {
    if (token.type !== 'heading_open') return
    const text = textOf(tokens[index + 1]) || '章节 ' + (toc.length + 1)
    const base = slugOf(text)
    let slug = base
    let occurrence = 2
    while (used.has(slug)) slug = base + '-' + occurrence++
    used.add(slug)
    const id = namespace + '-' + slug
    const level = Math.min(6, 2 + Number(token.tag.slice(1)) - minLevel)
    token.tag = tokens[index + 2].tag = 'h' + level
    token.attrSet('id', id)
    toc.push({ id, slug, text, level })
  })
  let purifier = purifiers.get(ownerWindow)
  if (!purifier) { purifier = createDOMPurify(ownerWindow); purifiers.set(ownerWindow, purifier) }
  const html = purifier.sanitize(md.renderer.render(tokens, md.options, env), {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 's', 'blockquote', 'ul', 'ol', 'li', 'a', 'img', 'pre', 'code', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span'],
    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'id', 'target', 'rel', 'loading', 'decoding', 'referrerpolicy', 'start', 'align'],
    ADD_URI_SAFE_ATTR: ['target', 'rel', 'loading', 'decoding', 'referrerpolicy', 'start', 'align'],
    ALLOWED_URI_REGEXP: /^(?:https?:\/\/|\/(?!\/)|#)/i,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: images ? [] : ['img'],
    FORBID_ATTR: ['style']
  })
  return { html, toc }
}

export async function readMarkdownFile(file) {
  if (!/\.(md|markdown)$/i.test(file.name)) throw new Error('请选择 .md 或 .markdown 文件')
  if (file.size > MARKDOWN_FILE_LIMIT) throw new Error('Markdown 文件不能超过 1 MiB')
  let bytes
  try { bytes = await file.arrayBuffer() } catch { throw new Error('文件读取失败，请重新选择') }
  if (bytes.byteLength > MARKDOWN_FILE_LIMIT) throw new Error('Markdown 文件不能超过 1 MiB')
  let body
  try { body = new TextDecoder('utf-8', { fatal: true }).decode(bytes).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n') }
  catch { throw new Error('请使用 UTF-8 编码的 Markdown 文件') }
  if (!body.trim()) throw new Error('文件正文为空')
  if (body.length > ARTICLE_LIMIT) throw new Error('正文最多 20 万字符')
  const tokens = md.parse(body, {})
  const firstHeading = tokens.findIndex(token => token.type === 'heading_open' && token.tag === 'h1')
  return { body, title: firstHeading < 0 ? '' : textOf(tokens[firstHeading + 1]).slice(0, 100), filename: file.name }
}
