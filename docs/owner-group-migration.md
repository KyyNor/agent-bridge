# 历史数据归属小组迁移命令

适用场景：组织结构调整后，把已有资源的归属小组（`owner_group_key`）从旧组批量迁移到新组。数据落在三个 SQLite 库文件中，主库承载资源本体，日志库与台账库各有一组带归属字段的表。

迁移必须按"小组定义 → 资源本体 → 从属记录"的顺序执行；从属表（运行记录、产物、日志）的归属字段参与独立过滤，启动迁移只回填空值（`owner_group_key = ''`），不会同步已写入的旧组值，因此必须与本命令一并更新。

## 涉及的表

| 库文件 | 表 | 层级 |
| --- | --- | --- |
| `data/agent-bridge.db` | `access_groups`、`user_group_memberships` | 小组定义 |
| `data/agent-bridge.db` | `knowledge_bases`、`mcp_services`、`openapi_services`、`code_repositories`、`project_profiles`、`memory_blocks`、`workflow_definitions`、`workflow_artifacts`、`scripts` | 资源本体（含 `visibility`） |
| `data/agent-bridge.db` | `documents`、`workflow_runs`、`script_runs`、`model_evaluation_runs` | 从属记录 |
| `data/agent-bridge-logs.db` | `tool_call_logs`、`agent_runs` | 从属记录（日志） |
| `data/agent-bridge-ledgers.db` | `business_ledgers` | 资源本体（台账，含 `visibility`） |

## 前置条件

1. 停止 Agent Bridge 服务，避免迁移期间写入与 WAL 并发冲突。
2. 用 `sqlite3 ... ".backup ..."` 备份三个库，不要直接 `cp`（主库运行在 WAL 模式，直接复制可能丢 `-wal` 中的数据）。

```bash
cd /path/to/agent-bridge
mkdir -p data/backup
sqlite3 data/agent-bridge.db ".backup data/backup/agent-bridge.db"
sqlite3 data/agent-bridge-logs.db ".backup data/backup/agent-bridge-logs.db"
sqlite3 data/agent-bridge-ledgers.db ".backup data/backup/agent-bridge-ledgers.db"
```

## 迁移命令

以下变量按实际环境替换：`OLD_GROUP` 是旧组 `group_key`，`NEW_GROUP` 是新组 `group_key`，库路径按部署位置调整。

```bash
OLD_GROUP='old-group-key'
NEW_GROUP='new-group-key'
```

### 第 1 步：小组定义（仅重组时需要）

新组不存在时先创建（`name`、`created_by` 为无默认值的 NOT NULL 列，必须显式给值，否则 `OR IGNORE` 会把约束违反静默吞掉、组建不出来），再把旧组成员整体迁到新组。展示名 `name` 按需替换：

```bash
sqlite3 data/agent-bridge.db "
BEGIN;
INSERT OR IGNORE INTO access_groups (group_key, name, created_by) VALUES ('$NEW_GROUP', '$NEW_GROUP', 'migration');
UPDATE user_group_memberships SET group_key = '$NEW_GROUP' WHERE group_key = '$OLD_GROUP';
COMMIT;"
```

如果只是给资源换组、不动成员关系，跳过本步。

### 第 2 步：主库资源本体与从属记录

```bash
sqlite3 data/agent-bridge.db "
BEGIN;
UPDATE knowledge_bases      SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE mcp_services         SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE openapi_services     SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE code_repositories    SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE project_profiles     SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE memory_blocks        SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE workflow_definitions SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE workflow_artifacts   SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE scripts              SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE documents            SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE workflow_runs        SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE script_runs          SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE model_evaluation_runs SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
COMMIT;"
```

### 第 3 步：台账库

```bash
sqlite3 data/agent-bridge-ledgers.db "
BEGIN;
UPDATE business_ledgers SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
COMMIT;"
```

### 第 4 步：日志库

```bash
sqlite3 data/agent-bridge-logs.db "
BEGIN;
UPDATE tool_call_logs SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
UPDATE agent_runs     SET owner_group_key = '$NEW_GROUP' WHERE owner_group_key = '$OLD_GROUP';
COMMIT;"
```

## 验证

迁移后确认三个库中不再残留旧组值（输出应全为 0）：

```bash
sqlite3 data/agent-bridge.db "
SELECT 'knowledge_bases', COUNT(*) FROM knowledge_bases WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'mcp_services', COUNT(*) FROM mcp_services WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'openapi_services', COUNT(*) FROM openapi_services WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'code_repositories', COUNT(*) FROM code_repositories WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'project_profiles', COUNT(*) FROM project_profiles WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'memory_blocks', COUNT(*) FROM memory_blocks WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'workflow_definitions', COUNT(*) FROM workflow_definitions WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'workflow_artifacts', COUNT(*) FROM workflow_artifacts WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'scripts', COUNT(*) FROM scripts WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'documents', COUNT(*) FROM documents WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'workflow_runs', COUNT(*) FROM workflow_runs WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'script_runs', COUNT(*) FROM script_runs WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'model_evaluation_runs', COUNT(*) FROM model_evaluation_runs WHERE owner_group_key = '$OLD_GROUP';"

sqlite3 data/agent-bridge-logs.db "
SELECT 'tool_call_logs', COUNT(*) FROM tool_call_logs WHERE owner_group_key = '$OLD_GROUP'
UNION ALL SELECT 'agent_runs', COUNT(*) FROM agent_runs WHERE owner_group_key = '$OLD_GROUP';"

sqlite3 data/agent-bridge-ledgers.db "
SELECT 'business_ledgers', COUNT(*) FROM business_ledgers WHERE owner_group_key = '$OLD_GROUP';"
```

## 注意事项

- `visibility = 'shared'` 的资源对全组可见，但 `owner_group_key` 仍代表归属，重组时同样会被上述命令迁移，属预期行为。
- 归属相关索引（`idx_tool_call_logs_owner_group`、`idx_agent_runs_owner_group`、`idx_tool_call_logs_stats` 等）在值更新时自动维护，无需重建。
- 各步骤每库一个事务，失败时该库内自动回滚；跨库不保证原子性，按顺序执行完并用验证命令核对。
- 旧组在资源全部迁走后若不再使用，可另行删除 `access_groups` 中对应行；`user_group_memberships` 有外键引用，先清成员再删组。
