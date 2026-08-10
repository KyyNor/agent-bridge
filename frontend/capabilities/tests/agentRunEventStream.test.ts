import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

import { readSseFrames } from '../src/composables/useAgentRunEventStream.ts'

const root = resolve(import.meta.dirname, '..')

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

test('SSE parser preserves chunked, CRLF, id and multiline data frames', async () => {
  const frames: Array<{ event: string, id: number | null, data: unknown }> = []
  await readSseFrames(
    streamFromChunks([
      'id: 7\r\nevent: agent_event\r\ndata: {"kind":"agent_',
      'message","message":"hello"}\r\n\r\n',
      'event: heartbeat\ndata: {}\n\n',
    ]),
    frame => { frames.push(frame) },
  )

  assert.deepEqual(frames, [
    { event: 'agent_event', id: 7, data: { kind: 'agent_message', message: 'hello' } },
    { event: 'heartbeat', id: null, data: {} },
  ])
})

test('agent event stream uses session cookie and a resumable event id', () => {
  const client = readFileSync(resolve(root, 'src/api/client.ts'), 'utf8')
  const stream = readFileSync(resolve(root, 'src/composables/useAgentRunEventStream.ts'), 'utf8')

  assert.doesNotMatch(client, /'X-Agent-Bridge-User'/)
  assert.match(client, /'Last-Event-ID'/)
  assert.match(client, /Accept: 'text\/event-stream'/)
  assert.match(stream, /AbortController/)
  assert.match(stream, /retryDelay/)
})
