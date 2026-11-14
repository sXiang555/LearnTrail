import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { readMarkdownFile, renderMarkdown } from '../src/articleMarkdown.js'
import { safeSourceUrl } from '../src/articleMeta.js'

const dom = new JSDOM('')
after(() => dom.window.close())
function parsed(source, options) {
  const result = renderMarkdown(source, dom.window, options)
  const root = dom.window.document.createElement('div')
  root.innerHTML = result.html
  return { ...result, root }
}

test('长文章保留完整正文和多级目录，同名页面标题只展示一次', () => {
  const { root, toc } = parsed('# 学习指南\n\n## 第一章\n\n' + '长文章'.repeat(20000) + '\n\n### 方法\n\n结束', { title: '学习指南' })
  assert.equal(root.querySelector('h1'), null)
  assert.equal(root.querySelector('h2').textContent, '第一章')
  assert.equal(root.querySelector('h3').textContent, '方法')
  assert.equal(toc.length, 2)
  assert.ok(root.textContent.endsWith('结束\n'))
  assert.ok(root.textContent.length > 50000)
})

test('重复标题和带数字后缀的标题不会生成重复目录 ID', () => {
  const { toc, root } = parsed('## A\n\n## A\n\n## A-2\n\n## 中文标题\n\n## 中文标题')
  assert.equal(new Set(toc.map(item => item.id)).size, 5)
  for (const item of toc) assert.equal(root.querySelectorAll('[id="' + item.id + '"]').length, 1)
})

test('代码高亮保持代码原义，不把代码中的 HTML 当作节点执行', () => {
  const source = '```html\n<img src="x" onerror="alert(1)">\n```\n\n```java\nclass Main { int value = 1; }\n```'
  const { root } = parsed(source)
  assert.equal(root.querySelector('img'), null)
  assert.ok(root.querySelector('pre code').textContent.includes('<img'))
  assert.ok(root.querySelector('code .hljs-keyword'))
})

test('表格、列表和行内代码保留语义结构', () => {
  const { root } = parsed('| 字段 | 值 |\n| --- | --- |\n| 积分 | 20 |\n\n1. 使用 `id`\n2. 继续阅读')
  assert.equal(root.querySelectorAll('th').length, 2)
  assert.equal(root.querySelectorAll('td').length, 2)
  assert.equal(root.querySelectorAll('ol li').length, 2)
  assert.equal(root.querySelector('li code').textContent, 'id')
})

test('外链使用新窗口与隔离属性，图片延迟加载并带替代文本', () => {
  const { root } = parsed('[文档](https://example.com/docs)\n\n![示意图](/favicon.svg)')
  const anchor = root.querySelector('a')
  assert.equal(anchor.target, '_blank')
  assert.equal(anchor.rel, 'noopener noreferrer')
  const image = root.querySelector('img')
  assert.equal(image.alt, '示意图')
  assert.equal(image.getAttribute('loading'), 'lazy')
  assert.equal(image.getAttribute('referrerpolicy'), 'no-referrer')
})

test('原始 HTML、事件属性和嵌入式脚本不会变成可执行节点', () => {
  const { root } = parsed('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n<iframe src="https://example.com"></iframe>\n\n<svg onload=alert(1)></svg>')
  assert.equal(root.querySelector('script,img,iframe,svg,style,form'), null)
  assert.ok(root.textContent.includes('<script>'))
})

test('危险协议和内嵌 SVG 图片不会产生链接或图片', () => {
  for (const source of ['[x](javascript:alert(1))', '[x](java&#x73;cript:alert(1))', '[x](data:text/html;base64,eA==)', '![x](data:image/svg+xml;base64,PHN2Zz4=)', '[x](file:///etc/passwd)']) {
    assert.equal(parsed(source).root.querySelector('a,img'), null)
  }
})

test('文内锚点保留为目录可处理的片段，超长代码仍完整展示', () => {
  const { root, toc } = parsed('## 第二章\n\n[跳转](#第二章)\n\n```unknown\n' + 'x'.repeat(25000) + '\n```')
  assert.equal(decodeURIComponent(root.querySelector('a').getAttribute('href')), '#第二章')
  assert.equal(toc[0].slug, '第二章')
  assert.ok(root.querySelector('code').textContent.length >= 25000)
})

test('导入 UTF-8 Markdown 保留正文，从真实一级标题提取标题', async () => {
  const file = new File(['\uFEFF# 导入标题\r\n\r\n## 小节\r\n正文'], '知识.MD')
  const result = await readMarkdownFile(file)
  assert.equal(result.title, '导入标题')
  assert.equal(result.body, '# 导入标题\n\n## 小节\n正文')
})

test('导入不从代码块里提取伪标题', async () => {
  const result = await readMarkdownFile(new File(['```sh\n# 代码注释\n```\n\n# 真实标题'], 'guide.md'))
  assert.equal(result.title, '真实标题')
})

test('拒绝错误文件类型、过大文件、空正文和无效 UTF-8', async () => {
  await assert.rejects(() => readMarkdownFile(new File(['正文'], 'guide.html')), /请选择/)
  await assert.rejects(() => readMarkdownFile(new File([new Uint8Array(1024 * 1024 + 1)], 'large.md')), /1 MiB/)
  await assert.rejects(() => readMarkdownFile(new File(['  \n'], 'empty.md')), /为空/)
  await assert.rejects(() => readMarkdownFile(new File([new Uint8Array([255])], 'bad.md')), /UTF-8/)
})

test('前端正文和文件导入统一限制为 20 万字符', async () => {
  assert.throws(() => parsed('x'.repeat(200001)), /20 万/)
  await assert.rejects(() => readMarkdownFile(new File(['x'.repeat(200001)], 'long.md')), /20 万/)
  assert.equal((await readMarkdownFile(new File(['x'.repeat(60000)], 'valid.md'))).body.length, 60000)
})

test('原文入口仅接受 HTTP(S)，拒绝脚本和带凭据地址', () => {
  assert.equal(safeSourceUrl('https://example.com/docs'), 'https://example.com/docs')
  for (const value of ['javascript:alert(1)', 'data:text/html,x', 'https://user:pass@example.com', '//example.com']) assert.equal(safeSourceUrl(value), '')
})
