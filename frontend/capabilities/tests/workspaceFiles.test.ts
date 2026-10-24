import assert from 'node:assert/strict'
import { test } from 'node:test'
import { childPath, parentPath, fileSizeLabel, MAX_TERMINAL_FILE_BYTES } from '../src/lib/workspaceFiles'

test('目录选择使用绝对路径，侧栏保持根目录内的相对路径', () => {
  assert.equal(childPath('/home/zhangsan', '项目'), '/home/zhangsan/项目')
  assert.equal(childPath('/', 'home'), '/home')
  assert.equal(childPath('', '文档.txt'), '文档.txt')
  assert.equal(childPath('docs', 'a.txt'), 'docs/a.txt')
  assert.equal(parentPath('/home/zhangsan'), '/home')
  assert.equal(parentPath('/'), '/')
  assert.equal(parentPath('docs/subdir'), 'docs')
  assert.equal(parentPath('docs'), '')
  assert.equal(parentPath(''), '')
})

test('上传大小上限与文件大小展示一致', () => {
  assert.equal(MAX_TERMINAL_FILE_BYTES, 33554432)
  assert.equal(fileSizeLabel(0), '0 B')
  assert.equal(fileSizeLabel(1024), '1.0 KiB')
  assert.equal(fileSizeLabel(MAX_TERMINAL_FILE_BYTES), '32.0 MiB')
})
