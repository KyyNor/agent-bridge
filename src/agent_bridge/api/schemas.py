from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

class CreateKbRequest(BaseModel):
    slug: str
    name: str
    description: str = ""


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


class ProjectProfileRequest(BaseModel):
    profile_key: str
    name: str
    description: str = ""
    status: str = "active"


class ProfileSourceRuleRequest(BaseModel):
    source_type: str
    source_key: str
    effect: str


class ProfileRulesRequest(BaseModel):
    rules: list[ProfileSourceRuleRequest] = Field(default_factory=list)


class ProfilePinRuleRequest(BaseModel):
    service_key: str
    tool_type: str


class ProfilePinsRequest(BaseModel):
    pins: list[ProfilePinRuleRequest] = Field(default_factory=list)


class ProfileManualNotesRequest(BaseModel):
    manual_notes: str = ""


class ProfilePinSettingsRequest(BaseModel):
    mode: str
    ratio_percent: int | None = None
    count: int | None = None


class ProfileResourceRuleRequest(BaseModel):
    resource_type: str
    resource_key: str


class ProfileResourcesRequest(BaseModel):
    resources: list[ProfileResourceRuleRequest] = Field(default_factory=list)


class ResourceProfilesRequest(BaseModel):
    profile_keys: list[str] = Field(default_factory=list)
    overrides: dict[str, dict[str, str | None]] | None = None


class UpdateKbDefaultsRequest(BaseModel):
    default_backend_slug: str | None = None
    default_agent_id: str | None = None


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


class CodeRepoCategoryRequest(BaseModel):
    category_key: str
    name: str
    description: str = ""


class KnowledgeSyncConfigRequest(BaseModel):
    code_sync_cron: str = "0 * * * *"
    ua_git_url: str = ""
    understand_cron: str = "0 2 * * *"
    doc_sync_cron: str = "*/30 * * * *"
    workflow_start_time: str = "22:00"
    workflow_stop_time: str = "07:00"
    workflow_max_runs: int = 0
    workflow_max_runtime_minutes: int = 30
    workflow_task_rerun_days: int = 30
    understand_timeout_minutes: int = 120


class WorkflowDefinitionRequest(BaseModel):
    workflow_key: str
    name: str
    description: str = ""
    profile_key: str
    workflow_js: str = ""
    status: str = "active"


class SkillPromptRequest(BaseModel):
    prompt: str


class ScriptRequest(BaseModel):
    script_key: str
    name: str
    description: str = ""
    language: str = "python"
    code: str
    status: str = "active"
    owner_type: str = "system"
    owner_key: str = ""


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


class UpdateBackendRequest(BaseModel):
    backend_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout: int | None = None
    embedding_model_id: str | None = None
    summary_model_id: str | None = None
