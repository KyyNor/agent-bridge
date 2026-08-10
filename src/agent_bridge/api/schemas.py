from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


class CreateMemoryBlockRequest(BaseModel):
    block_key: str
    name: str
    description: str = ""


class UpsertAccessGroupRequest(BaseModel):
    group_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)


class SetUserGroupRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=255)
    group_key: str = Field(min_length=1, max_length=64)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class ChangeAdminPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class UpdateMemoryBlockStatusRequest(BaseModel):
    status: str


class ProfileMemoryBindingRequest(BaseModel):
    block_key: str | None = None
    enabled: bool = True
    expected_edit_token: str | None = None


class ClaudeCodeHookRequest(BaseModel):
    profile_key: str
    event_name: str | None = None
    matcher: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    hook_timeout_seconds: int = Field(default=60, ge=1, le=300)


class CreateKbRequest(BaseModel):
    slug: str
    name: str
    description: str = ""
    visibility: Literal["group", "shared"] | None = None


class CreateFolderRequest(BaseModel):
    parent_folder_id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=255)


class UpdateFolderRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_folder_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_change(self) -> "UpdateFolderRequest":
        if not self.model_fields_set.intersection({"name", "parent_folder_id"}):
            raise ValueError("at least one folder field must be provided")
        return self


class DeleteFolderRequest(BaseModel):
    confirm: bool = False


class KnowledgeBrowseContext(BaseModel):
    kind: Literal["folder", "zip"]
    id: int
    name: str
    relative_path: str = ""
    parent_id: int | None = None
    parent_folder_id: int | None = None
    archive_entry_id: int | None = None


class KnowledgeBrowseFolderEntry(BaseModel):
    kind: Literal["folder"] = "folder"
    id: int
    name: str
    relative_path: str
    parent_id: int | None = None
    parent_folder_id: int | None = None
    child_count: int = 0


class KnowledgeBrowseZipEntry(BaseModel):
    kind: Literal["zip"] = "zip"
    id: int
    name: str
    relative_path: str
    parent_id: int | None = None
    parent_folder_id: int | None = None
    archive_entry_id: int
    child_count: int = 0


class KnowledgeBrowseDocumentEntry(BaseModel):
    kind: Literal["document"] = "document"
    id: int
    doc_id: int
    name: str
    relative_path: str
    parent_id: int | None = None
    parent_folder_id: int | None = None
    slug: str
    title: str
    original_filename: str
    version: int
    version_no: int
    sync_status: str
    archive_entry_id: int | None = None
    status: str


KnowledgeBrowseEntry = Annotated[
    KnowledgeBrowseFolderEntry
    | KnowledgeBrowseZipEntry
    | KnowledgeBrowseDocumentEntry,
    Field(discriminator="kind"),
]


class KnowledgeBrowseResponse(BaseModel):
    context: KnowledgeBrowseContext
    parent: KnowledgeBrowseContext | None = None
    entries: list[KnowledgeBrowseEntry]


class DocumentPlacementRequest(BaseModel):
    kb: str = Field(min_length=1, max_length=255)
    folder_id: int = Field(ge=1)


class AttachDocumentRequest(BaseModel):
    kb: str = Field(min_length=1, max_length=255)
    folder_id: int = Field(ge=1)


class SyncRequest(BaseModel):
    all_users: bool = False


class AskRequest(BaseModel):
    kb: str
    question: str
    backend: str | None = None
    session_id: str | None = None
    profile_key: str | None = None


class PurgeRequest(BaseModel):
    confirm: bool = False


class RegisterMcpServiceRequest(BaseModel):
    service_key: str
    name: str
    endpoint_url: str
    headers: dict[str, Any] | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["group", "shared"] | None = None
    expected_edit_token: str | None = None


class RegisterOpenApiServiceRequest(BaseModel):
    service_key: str
    name: str
    base_url: str
    spec_url: str = ""
    spec_content: str = ""
    auth_config: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["group", "shared"] | None = None
    expected_edit_token: str | None = None


class ImportOpenApiOperationsRequest(BaseModel):
    spec_content: str | None = None


class UpsertOpenApiToolRequest(BaseModel):
    tool_name: str
    operation_id: str = ""
    method: str
    path: str
    display_name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    request_mapping: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    tool_type: str = "unconfigured"
    tags: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)


class UpdateMcpServiceStatusRequest(BaseModel):
    status: str


class UpdateMcpToolTypeRequest(BaseModel):
    tool_type: str


class ExecuteCapabilityRequest(BaseModel):
    service: str
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    profile_key: str | None = None


class BusinessLedgerFieldRequest(BaseModel):
    field_key: str
    name: str
    field_type: Literal["text", "number", "enum", "date", "datetime"]
    required: bool = False
    query_modes: list[str] = Field(default_factory=list)
    sortable: bool = False
    agent_readable: bool = True
    enum_values: list[str] = Field(default_factory=list)


class BusinessLedgerRequest(BaseModel):
    ledger_key: str
    name: str
    description: str = ""
    fields: list[BusinessLedgerFieldRequest]
    visibility: Literal["group", "shared"] = "group"
    expected_edit_token: str | None = None


class BusinessLedgerUpdateRequest(BaseModel):
    name: str
    description: str = ""
    fields: list[BusinessLedgerFieldRequest]
    visibility: Literal["group", "shared"] | None = None
    expected_edit_token: str | None = None


class BusinessLedgerRecordRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class BusinessLedgerSortRequest(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "asc"


class BusinessLedgerQueryRequest(BaseModel):
    filters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    keyword: str | None = None
    sort: list[BusinessLedgerSortRequest] | BusinessLedgerSortRequest | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkflowArtifactVisibilityRequest(BaseModel):
    visibility: Literal["group", "shared"]


class ProjectProfileRequest(BaseModel):
    profile_key: str
    name: str
    description: str = ""
    status: str = "active"
    expected_edit_token: str | None = None


class ProfileSourceRuleRequest(BaseModel):
    source_type: str
    source_key: str
    effect: str


class ProfileRulesRequest(BaseModel):
    rules: list[ProfileSourceRuleRequest] = Field(default_factory=list)
    expected_edit_token: str | None = None


class ProfilePinRuleRequest(BaseModel):
    service_key: str
    tool_type: str


class ProfilePinsRequest(BaseModel):
    pins: list[ProfilePinRuleRequest] = Field(default_factory=list)
    expected_edit_token: str | None = None


class ProfileManualNotesRequest(BaseModel):
    manual_notes: str = ""
    expected_edit_token: str | None = None


class ProfilePinSettingsRequest(BaseModel):
    mode: str
    ratio_percent: int | None = None
    count: int | None = None
    expected_edit_token: str | None = None


class ProfileResourceRuleRequest(BaseModel):
    resource_type: str
    resource_key: str


class ProfileResourcesRequest(BaseModel):
    resources: list[ProfileResourceRuleRequest] = Field(default_factory=list)
    expected_edit_token: str | None = None


class ResourceProfilesRequest(BaseModel):
    profile_keys: list[str] = Field(default_factory=list)
    overrides: dict[str, dict[str, str | None]] | None = None


class UpdateKbDefaultsRequest(BaseModel):
    default_backend_slug: str | None = None
    default_agent_id: str | None = None
    expected_edit_token: str | None = None


class CreateAgentRequest(BaseModel):
    name: str
    preset_id: str


class CodeRepositoryRequest(BaseModel):
    repo_key: str
    name: str
    git_url: str
    branch: str = "main"
    auth_ref: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category_key: str = ""
    sync_interval_minutes: int = 60
    auto_understand: bool = False
    status: str = "active"
    visibility: Literal["group", "shared"] | None = None
    expected_edit_token: str | None = None


class KbRepoSourceRequest(BaseModel):
    repo_key: str
    include_suffixes: list[str] = Field(default_factory=lambda: [".md", ".txt"])


class CodeRepoCategoryRequest(BaseModel):
    category_key: str
    name: str
    description: str = ""
    expected_edit_token: str | None = None


class KnowledgeSyncConfigRequest(BaseModel):
    code_sync_cron: str = "0 * * * *"
    ua_git_url: str = ""
    ua_plugin_update_cron: str = "0 3 * * 0"
    claude_mem_git_url: str = ""
    claude_mem_plugin_update_cron: str = "30 3 * * 0"
    understand_cron: str = "0 2 * * *"
    doc_sync_cron: str = "*/30 * * * *"
    workflow_start_time: str = "22:00"
    workflow_stop_time: str = "07:00"
    workflow_max_runs: int = 0
    workflow_max_concurrent_runs: int = Field(default=4, ge=1)
    workflow_max_concurrent_runs_per_workflow: int = Field(default=2, ge=1)
    workflow_max_runtime_minutes: int = 30
    workflow_task_rerun_days: int = 30
    log_retention_days: int = Field(default=180, ge=1)
    mcp_timeout_seconds: int = 150
    understand_timeout_minutes: int = 120
    artifact_search_cache_ttl_hours: int = Field(default=8, ge=1, le=168)
    expected_edit_token: str | None = None


class ClaudeMemConfigRequest(BaseModel):
    base_url: str | None = None
    auth_token: str | None = None
    api_key: str | None = None
    model: str | None = None
    clear_auth_token: bool = False
    clear_api_key: bool = False
    expected_edit_token: str | None = None


class RetrievalProbeLlmConfigRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    expected_edit_token: str | None = None


class ModelEvaluationStartRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=512)
    datasets: list[str] = Field(min_length=1, max_length=7)
    max_samples: int = Field(default=64, ge=1, le=1000)
    sampling_mode: Literal["head", "random"] = "head"
    sample_seed: int = Field(default=42, ge=0, le=2_147_483_647)
    base_url: str = Field(default="", max_length=2048)
    api_key: str = Field(default="", max_length=4096)


class AgentBackendConfigRequest(BaseModel):
    slug: str
    type: str
    command: str | None = None
    model: str | None = None


class AgentRuntimeConfigRequest(BaseModel):
    default_backend: str = "claude"
    backends: list[AgentBackendConfigRequest] = Field(default_factory=list)
    expected_edit_token: str | None = None


class WorkflowDefinitionRequest(BaseModel):
    workflow_key: str
    name: str
    description: str = ""
    profile_key: str
    # Graph parsing belongs to WorkflowValidator so save and validation routes
    # produce the same structured issue contract for malformed definitions.
    definition: dict[str, Any]
    status: str = "active"
    workflow_type: str = "operation"
    # 0 表示客户端确认目标尚不存在；正整数用于并发编辑的乐观锁。
    # 省略时兼容旧客户端，由服务端继续执行原有 upsert 语义。
    expected_edit_version: int | None = Field(default=None, ge=0)
    # 执行语义发生变化时，决定是否立即把已有完成任务放入刷新队列。
    task_refresh_policy: Literal["auto", "defer"] = "auto"


class WorkflowTaskRefreshItem(BaseModel):
    task_key: str = Field(min_length=1, max_length=1024)
    task_version: str = Field(default="", max_length=512)


class WorkflowTaskRefreshRequest(BaseModel):
    """显式请求将任务放入当前工作流版本的增量刷新队列。"""

    tasks: list[WorkflowTaskRefreshItem] | None = Field(default=None, max_length=500)


class WorkflowTaskImportConfirmRequest(BaseModel):
    import_id: str


class WorkflowImportConfirmRequest(BaseModel):
    import_id: str


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    task_key: str | None = None
    task_version: str | None = None
    execution_mode: Literal["normal", "incremental", "force_full"] = "normal"


class WorkflowRunPreviewRequest(WorkflowRunRequest):
    """A run-shaped request which only calculates its execution plan."""


class WorkflowValidationRequest(BaseModel):
    workflow: dict[str, Any]


class SkillPromptRequest(BaseModel):
    prompt: str
    expected_edit_token: str | None = None


class EditTokenRequest(BaseModel):
    expected_edit_token: str | None = None


class ScriptRequest(BaseModel):
    script_key: str
    name: str
    description: str = ""
    language: str = "python"
    code: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    status: str = "active"
    owner_type: str = "system"
    owner_key: str = ""
    expected_edit_token: str | None = None


class ScriptValidateRequest(BaseModel):
    code: str


class ScriptTestRunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_script_params(cls, value: Any) -> Any:
        if isinstance(value, dict) and "params" not in value and "script_params" in value:
            value = dict(value)
            value["params"] = value.get("script_params") or {}
        return value


class DesignAgentRequest(BaseModel):
    mode: str = "modify"
    prompt: str
    current: dict[str, Any] = Field(default_factory=dict)
    profile_key: str | None = None
    run_key: str | None = None


class RuntimeWorkflowSetTaskRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeWorkflowRunLogRequest(BaseModel):
    level: str = "info"
    stage: str = ""
    message: str = ""
    task_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class UpsertBackendRequest(BaseModel):
    slug: str
    backend_type: str
    base_url: str | None = None
    api_key: str | None = None
    timeout: int = 120
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
    rerank_model_id: str | None = None
    expected_edit_token: str | None = None


class UpdateBackendRequest(BaseModel):
    backend_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout: int | None = None
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
    rerank_model_id: str | None = None
    expected_edit_token: str | None = None
