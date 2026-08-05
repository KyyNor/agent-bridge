import assert from 'node:assert/strict'
import test from 'node:test'
import { businessLedgerRecordFormValues } from '../src/lib/businessLedger'

test('编辑台账数据时日期和日期时间回显为原生输入控件格式', () => {
  const values = businessLedgerRecordFormValues(
    [
      { field_key: 'date', name: '日期', field_type: 'date', required: false, query_modes: ['exact'], sortable: true, agent_readable: true, enum_values: [] },
      { field_key: 'datetime', name: '日期时间', field_type: 'datetime', required: false, query_modes: ['exact'], sortable: true, agent_readable: true, enum_values: [] },
    ],
    { date: '2026-08-05T00:00:00', datetime: '2026-08-05T12:34:56+00:00' },
  )

  assert.deepEqual(values, { date: '2026-08-05', datetime: '2026-08-05T12:34' })
})
