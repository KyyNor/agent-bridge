/**
 * Shared markdown rendering helper.
 *
 * Centralises `marked.parse` so every view that renders agent / workflow output
 * (event messages, artifacts, results) does it identically. Mirrors the
 * `renderMarkdown` previously inlined in WorkflowView.vue.
 */
import { marked, Renderer, type Tokens } from 'marked'

/** Render a markdown string to HTML (synchronous configuration). */
export function renderMarkdown(content: string): string {
  return marked.parse(content, { async: false }) as string
}

function escapeHtml(content: string): string {
  return content.replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char] as string)
}

function isUnsafeUrl(url: string): boolean {
  const normalized = url.replace(/[\u0000-\u0020\u007f]/g, '').toLowerCase()
  return /^(?:javascript|vbscript|data):/.test(normalized)
}

const safeMarkdownRenderer = new Renderer()
safeMarkdownRenderer.html = ({ text }: Tokens.HTML | Tokens.Tag) => escapeHtml(text)

/**
 * Renders untrusted Markdown for insertion with `v-html`.
 * Raw HTML is displayed as text and executable URL schemes are removed.
 */
export function renderSafeMarkdown(content: string): string {
  return marked.parse(content, {
    async: false,
    renderer: safeMarkdownRenderer,
    walkTokens(token) {
      if ((token.type === 'link' || token.type === 'image') && isUnsafeUrl(token.href)) {
        token.href = ''
      }
    },
  }) as string
}
