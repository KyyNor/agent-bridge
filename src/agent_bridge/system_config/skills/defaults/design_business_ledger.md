# design_business_ledger

你正在为 Agent Bridge 设计“业务台账”定义。业务台账用于管理员维护中小规模、通常由 Excel 导入的数据，并由 Agent 在获授权的能力平面内受控查询。

先理解用户希望管理的业务实体、维护方式和 Agent 需要回答的问题，再生成完整台账定义。

## 字段类型

只允许以下字段类型：

- `text`：普通文本，如名称、IP、说明。
- `number`：数值，如成本、数量、容量。
- `enum`：有限且稳定的选项；必须给出 `enum_values`。
- `date`：日期。
- `datetime`：日期时间。

不支持布尔类型；需要表达“是/否”时请设计为枚举，例如 `yes` / `no`。

## 查询和展示规则

- 所有字段默认支持等值查询和排序。
- `number`、`date`、`datetime` 自动支持大于、小于、大于等于、小于等于和范围筛选。
- 只有 `text` 字段可将 `fuzzy_match` 设为 `true`，表示允许字面包含检索；IP、编号、唯一标识通常应为 `false`。
- `agent_readable` 为 `true` 的字段可出现在 Agent 查询结果中。敏感字段应设为 `false`，但要注意 Agent 仍可能根据可查询字段得出有限结论。
- 非文本字段的 `fuzzy_match` 必须为 `false`；非枚举字段的 `enum_values` 必须为空数组。

## 命名与规模

- `ledger_key` 和 `field_key` 仅用小写字母、数字、下划线、连字符，长度不超过 80。
- 字段名使用清晰的中文业务名，字段标识使用稳定的英文标识。
- 单个台账上限为 100 个字段、50,000 行记录；优先设计少而稳定的字段，方便 Excel 维护。

## 修改已有台账

modify 模式必须保留用户未要求调整的字段和语义，尤其避免重命名 `ledger_key` 或已有 `field_key`。已有枚举值只能在用户明确要求时删除；任何类型调整都要考虑已有 Excel 数据的兼容性。

## 返回要求

返回完整定义，不要返回 JSON patch、SQL、表结构迁移或记录数据。每个字段必须带齐：`field_key`、`name`、`field_type`、`required`、`fuzzy_match`、`agent_readable`、`enum_values`。
