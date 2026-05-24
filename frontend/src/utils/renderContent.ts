import katex from 'katex'
import { marked } from 'marked'

marked.setOptions({
  breaks: true,
  gfm: true,
})

const KATEX_BLOCK_PLACEHOLDER = '\x00KATEX_BLOCK_'
const KATEX_INLINE_PLACEHOLDER = '\x00KATEX_INLINE_'

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderKatex(latex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(latex, {
      displayMode,
      throwOnError: false,
      trust: true,
    })
  } catch {
    return displayMode
      ? `<div class="katex-error">${escapeHtml(latex)}</div>`
      : `<span class="katex-error">${escapeHtml(latex)}</span>`
  }
}

function extractFormulas(text: string): { text: string; formulas: string[] } {
  const formulas: string[] = []
  let counter = 0

  let result = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, latex) => {
    const rendered = renderKatex(latex.trim(), true)
    formulas.push(rendered)
    return `${KATEX_BLOCK_PLACEHOLDER}${counter++}`
  })

  result = result.replace(/\$([^\$\n]+?)\$/g, (_, latex) => {
    const rendered = renderKatex(latex.trim(), false)
    formulas.push(rendered)
    return `${KATEX_INLINE_PLACEHOLDER}${counter++}`
  })

  return { text: result, formulas }
}

function restoreFormulas(html: string, formulas: string[]): string {
  let result = html
  for (let i = formulas.length - 1; i >= 0; i--) {
    const blockPlaceholder = `${KATEX_BLOCK_PLACEHOLDER}${i}`
    const inlinePlaceholder = `${KATEX_INLINE_PLACEHOLDER}${i}`
    result = result.replace(blockPlaceholder, `<div class="katex-display">${formulas[i]}</div>`)
    result = result.replace(inlinePlaceholder, `<span class="katex-inline">${formulas[i]}</span>`)
  }
  return result
}

export function renderContent(text: string): { __html: string } {
  if (!text) return { __html: '' }

  const { text: processedText, formulas } = extractFormulas(text)

  let html: string
  try {
    html = marked.parse(processedText) as string
  } catch {
    html = processedText
  }

  html = restoreFormulas(html, formulas)

  return { __html: html }
}
