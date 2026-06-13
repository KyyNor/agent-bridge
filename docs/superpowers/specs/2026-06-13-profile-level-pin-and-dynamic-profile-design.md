# Profile Level Pin And Dynamic Profile Design

## Goal

Add two profile-centered capabilities to Agent Bridge:

1. Let each profile expose high-frequency MCP tools directly by pinning `service + tool_type` groups.
2. Generate a small external profile markdown file that agents can read for the active profile's usage guidance and available resources.

Workflow management and skill management are intentionally out of scope for this spec.

## Current Context

Agent Bridge currently exposes a MetaMCP gateway with two base tools: `search` and `execute`. External MCP services are registered in `mcp_services`, tools are synced into `mcp_tools`, and each synced tool has a configured `tool_type`: `overview`, `search`, `detail`, `action`, or `unconfigured`.

Profiles already control which MCP services are visible and executable through allow/deny source rules. Tool calls are logged with `profile_key`, `source_key`, `tool_name`, status, error classification, and duration. Existing stats APIs can aggregate by dimensions, but they do not currently expose a `tool_type` dimension.

The profile CLI currently writes an MCP server entry named `agent-capability-hub`. The new name should be `agent-bridge`.

## Scope

This spec includes:

- Profile-scoped manual pin rules for `service + level`.
- Profile-scoped automatic pin strategy by ratio or fixed count.
- Direct MCP tools generated from pinned groups.
- UI support in the Profile configuration dialog.
- Stats support for service, service + level, and tool call trends.
- Profile dynamic markdown generation and `CLAUDE.md` / `AGENTS.md` pointer injection.

This spec excludes:

- Workflow execution or Claude Code workflow management.
- Skill registry or skill installation.
- Pinning built-in Wiki or CodeGraph tools.
- Pinning action tools.
- Changing profile allow/deny policy semantics.

## Terminology

- **Level**: the existing `mcp_tools.tool_type` value. For pinning, only executable read-only levels are eligible: `overview`, `search`, and `detail`.
- **Pinned group**: a `profile_key + service_key + tool_type` tuple.
- **Manual pin**: an explicit pinned group selected by a user.
- **Auto pin**: pinned groups selected from recent usage statistics.
- **Direct pinned tool**: a concrete MCP tool exposed to the agent from a pinned group, named `pin_<service_key>_<tool_name>`.

## Data Model

Add profile-scoped pin storage.

### Manual Pin Rules

Store one row per pinned group:

- `profile_key`
- `service_key`
- `tool_type`
- `created_by`
- timestamps

The tuple `profile_key + service_key + tool_type` is unique.

Validation:

- `profile_key` must exist and be active for use.
- `service_key` must refer to an external MCP service.
- `tool_type` must be one of `overview`, `search`, `detail`.
- The service must be allowed by the profile to become effective. A rule may remain stored if the service is later denied, but it becomes inactive in computed output.

### Auto Pin Settings

Store one settings row per profile:

- `profile_key`
- `mode`: `disabled`, `ratio`, or `count`
- `ratio_percent`: integer or null
- `count`: integer or null
- `auto_cache_json`: cached computed groups and metadata
- `auto_cache_computed_at`
- timestamps

Validation:

- `disabled` ignores `ratio_percent` and `count`.
- `ratio` requires `ratio_percent` between 1 and 100.
- `count` requires a positive integer.
- Ratio and count are mutually exclusive.

### Profile Dynamic Content Cache

Store profile markdown generation state:

- `profile_key`
- `manual_notes`: user-maintained markdown text.
- `auto_summary_json`: structured source summary used for rendering.
- `auto_summary_hash`: hash of computed automatic content.
- `rendered_hash`: hash of final automatic + manual markdown.
- `last_rendered_markdown`: latest rendered markdown.
- `last_written_at`
- timestamps

The rendered file is only rewritten when the newly computed content hash differs from cached state, or when the target file does not exist.

## Pin Candidate Rules

Pinning is a presentation optimization. It never grants access.

Candidates are computed from:

- Current profile allow/deny source rules.
- Enabled external MCP services.
- Active tools under those services.
- Tool types in `overview`, `search`, and `detail`.

Candidates exclude:

- Services not allowed by the current profile.
- Disabled or error services.
- Inactive tools.
- `action` tools.
- `unconfigured` tools.
- Built-in providers such as Wiki and CodeGraph.

The ratio denominator is the number of eligible `service + level` candidate groups, not the number of tools.

## Manual And Auto Merge Rules

Final pinned groups are computed in this order:

1. Load effective manual pinned groups.
2. If auto mode is disabled, return manual groups.
3. Compute target group count:
   - `ratio`: `ceil(candidate_group_count * ratio_percent / 100)`.
   - `count`: configured count.
4. If target group count is less than or equal to manual group count, return manual groups.
5. Otherwise, select additional groups from auto rankings until final count reaches the target or candidates run out.

Manual pins are a lower bound. Auto pin can add groups, but it never removes or replaces manual groups.

Automatic rankings use successful `metamcp_execute` calls from the most recent 30 days. The ranking unit is `service_key + tool_type`, calculated by joining tool call logs to the current `mcp_tools` table by `source_key + tool_name`.

If there is no usage data, automatic pin adds nothing.

## Auto Pin Caching

Automatic pin is a usage snapshot, not a live leaderboard.

Default auto pin TTL is 24 hours. When a profile has auto pin enabled, its computed auto groups may be reused until TTL expiry.

Immediate cache invalidation happens when configuration changes:

- Profile source rules change.
- Manual pin rules change.
- Auto pin mode or value changes.
- MCP service status changes.
- MCP service tools are synced or tool types are changed.

Tool call volume alone does not immediately invalidate the cache. A profile page and CLI command should provide an explicit "recalculate auto pin" action that clears the profile's auto cache.

## MCP Exposure

The base MCP gateway keeps the `search` and `execute` tools.

For each final pinned group, expand current active tools matching the pinned `service_key + tool_type`. Each concrete tool becomes a direct MCP tool:

```text
pin_<service_key>_<tool_name>
```

Example:

```text
pin_mysql_query_users
pin_mysql_list_tables
pin_mysql_find_orders
```

The direct tool's input schema is the original upstream MCP tool schema. The agent does not pass `{ tool, arguments }` for direct pinned tools.

Execution of a direct pinned tool maps to the existing service execution path:

```text
CapabilityService.execute(
  service=<service_key>,
  tool=<tool_name>,
  arguments=<direct tool arguments>,
  profile_key=<current request profile>
)
```

This keeps existing policy checks, read-only enforcement, call logging, error classification, and `log_id` behavior.

The call log should keep `source_key` as the original service and `tool_name` as the original upstream tool. Request metadata may include the direct entrypoint tool name, such as `pin_mysql_query_users`, so operators can distinguish direct pin calls from generic `execute` calls.

## MCP Server Cache

MCP tool lists vary by profile.

The implementation should cache profile-specific MCP server/tool-list state using:

- `profile_key`
- profile pin/config version
- service/tool catalog version
- auto pin cache timestamp or computed hash

Config changes should invalidate the relevant profile's cached tool list immediately. Auto pin usage changes should not cause live cache churn. Auto pin results refresh by TTL expiry or explicit recalculate action.

If implementation complexity with cached `FastMCP` instances is high, the acceptable alternative is to rebuild the profile-specific MCP server for initialization/list-tools requests while keeping auto pin computation cached. The design requirement is stable behavior, not a specific cache class.

## Tool Name Safety

Direct tool names use the prefix `pin_` to avoid collisions with base tools such as `search` and `execute`.

`service_key` and `tool_name` should be normalized to MCP-safe identifiers if needed. The generated description must preserve the original service and tool names.

If normalization still produces a conflict inside one profile's generated tool list, fail the pin preview and expose a clear admin error in the Profile UI. Do not silently generate numbered names.

## Direct Tool Description

Each direct pinned tool description should include:

- Original service name and key.
- Original tool name.
- Level.
- Pin source: manual or auto.
- A short instruction that the full service directory remains available through `search(path="<service_key>")`.

The description should not include the entire profile configuration.

## Profile UI

Add the pin configuration inside the Profile configuration dialog.

The dialog should have a Pinned Tools section with:

- Manual pin table: service, level, matching tool count, status, actions.
- Add manual pin control: choose an allowed service and eligible level.
- Auto pin settings: disabled, ratio, or count.
- Fixed time window label: recent 30 days.
- Last auto calculation timestamp.
- Recalculate auto pin button.
- Current direct tool preview showing generated `pin_*` tools and their manual/auto source.

The UI should show inactive rule reasons:

- Service is no longer allowed by the profile.
- Service is disabled or errored.
- No active executable tools exist for this level.
- Tool type no longer participates in pinning.

The Tools page can show passive metadata later, but pin editing belongs in Profile configuration.

## Stats UI

Extend call statistics with three views:

- By service: `source_key`.
- By service + level: `source_key + tool_type`.
- By tool: `source_key + tool_name`.

The service + level view is the primary explanation for automatic pin selection.

The stats API should support `tool_type` as a dimension by joining `tool_call_logs` with `mcp_tools`. First version can render tables with daily buckets for the last 30 days. A complex chart is optional.

## Dynamic Profile Markdown

Profile markdown is for agent guidance, not configuration export.

Generated content should include only:

- Profile name.
- How to use agent-bridge:
  - `search`
  - `execute`
  - code repository query through existing CodeGraph capabilities
  - knowledge base query through existing Wiki/knowledge capabilities
- A short note that high-frequency capabilities may be exposed directly as `pin_*` tools by the active profile.
- Available resource list:
  - MCP service names.
  - Code repository names.
  - Knowledge base names.
- Manual supplement area maintained by the user.

Generated content should not include:

- MCP URL.
- Pin configuration details.
- Generated direct pinned tool list.
- Raw profile allow/deny rules.

The final profile markdown is:

```text
automatic summary
manual supplement
```

## Profile File Paths

Each scope has one active profile pointer.

Project scope:

- Dynamic file: `<project>/.agent-bridge/profiles/<profile>.md`
- Project `CLAUDE.md` points to the absolute path of that dynamic file.
- Project `AGENTS.md` points to the same absolute path in plain language.

User scope:

- Dynamic file: `~/.agent-bridge/profiles/<profile>.md`
- `~/.claude/CLAUDE.md` points to the absolute path of that dynamic file.
- `~/.codex/AGENTS.md` points to the same dynamic profile file in plain language.

The `CLAUDE.md` pointer should use Claude Code import syntax where appropriate:

```text
@/absolute/path/to/.agent-bridge/profiles/<profile>.md
```

The `AGENTS.md` pointer should say that the agent must read the profile file before using agent-bridge capabilities.

If project scope exists, it is authoritative. User scope is only a fallback explanation for projects without project scope.

Running `profile use <new-profile>` again in the same scope overwrites that scope's pointer to the new profile file.

## MCP Server Name

The MCP server entry name is now:

```text
agent-bridge
```

Do not use the old `agent-capability-hub` name for new writes.

The server name is fixed across scopes. Claude Code scope precedence is expected to make project scope override user scope for the same server name. The entries are not merged.

## Regeneration Rules

The automatic profile markdown summary should be recomputed when any of these source data changes:

- Profile name or status.
- Profile source rules.
- Profile resource rules.
- MCP service name, description, status, or tags.
- MCP tool sync or tool type changes if they affect usage guidance.
- Code repository name, status, or visibility for the profile.
- Knowledge base name, status, or visibility for the profile.
- Manual supplement text changes.

After recomputing, compare the new hash with cached hashes. Rewrite installed profile files only when content differs.

## API And CLI Surface

Backend APIs should support:

- Get profile pin config and computed preview.
- Replace manual pin groups.
- Update auto pin mode/value.
- Recalculate auto pin for a profile.
- Get service + level stats.
- Get and update manual supplement text.
- Render or refresh dynamic profile markdown.

CLI should support:

- `profile use <profile> --scope project|user`: writes MCP config using server name `agent-bridge`, writes profile pointer files, and renders dynamic profile markdown.
- `profile refresh <profile> --scope project|user`: recomputes and rewrites the dynamic profile markdown for that scope when content differs.
- `profile pins refresh <profile>`: clears the profile's automatic pin cache so the next preview or MCP list-tools request recalculates it.

## Error Handling

Policy failures from direct pinned tools behave like `execute` failures and return the existing error style with `log_id`.

If a direct pinned tool maps to a missing or inactive upstream tool, return a capability registry error and log it.

If a manual pin becomes ineffective, keep the rule stored but show inactive status in the UI and exclude it from MCP exposure.

If auto pin settings are invalid, reject the update with a validation error.

If profile markdown cannot be written to the target file path, keep the database cache and show a clear CLI or API error.

## Testing

Backend tests:

- Manual pin CRUD and validation.
- Auto pin mode validation.
- Candidate group filtering by profile allow rules.
- Manual + auto merge behavior.
- Ratio calculation and fixed count calculation.
- Auto pin cache TTL and explicit recalculation.
- Direct tool generation from `service + level` groups.
- Direct `pin_*` execution maps to original `execute`.
- No action or unconfigured tools are exposed.
- Stats by `source_key + tool_type`.
- Dynamic profile markdown rendering with automatic and manual content.
- Regeneration only when hashes differ.

CLI tests:

- `profile use` writes `agent-bridge` MCP server entry.
- Project scope writes project pointer files and project dynamic profile file.
- User scope writes user fallback pointer files and user dynamic profile file.
- Same-scope profile switch overwrites the pointer.
- Existing unrelated MCP servers and unrelated markdown content are preserved.

Frontend tests or focused component checks:

- Profile configuration shows Pinned Tools section.
- Manual pin add/remove flow.
- Auto mode switching between disabled, ratio, and count.
- Recalculate auto pin button state.
- Current direct tool preview.
- Inactive rule reasons.

## Acceptance Criteria

- A profile can manually pin `mysql + search`; agents connected through that profile see concrete `pin_mysql_<tool>` tools for active search-level mysql tools.
- Automatic pin can be configured by ratio or count, but not both.
- Manual pins always remain the minimum final set; auto pin only adds more groups.
- Automatic pin uses recent 30-day usage, cached for 24 hours by default, with explicit recalculation available.
- Pinning never grants access to services outside the profile's allow policy.
- Logs and stats can explain calls by service, service + level, and tool.
- `profile use` writes MCP server name `agent-bridge`.
- Dynamic profile markdown contains only profile name, agent-bridge usage guidance, available service/repo/KB names, and manual supplement.
- `CLAUDE.md` and `AGENTS.md` point to the dynamic profile file instead of embedding generated details.
