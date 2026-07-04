/**
 * Shared markdown rendering helper.
 *
 * Centralises `marked.parse` so every view that renders agent / workflow output
 * (event messages, artifacts, results) does it identically. Mirrors the
 * `renderMarkdown` previously inlined in WorkflowView.vue.
 */
import { marked } from 'marked'

/** Render a markdown string to HTML (synchronous configuration). */
export function renderMarkdown(content: string): string {
  return marked.parse(content, { async: false }) as string
}
