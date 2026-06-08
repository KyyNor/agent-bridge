from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_bridge.core.domain import KbRole


class CreateKbRequest(BaseModel):
    slug: str
    name: str
    description: str = ""


class GrantMemberRequest(BaseModel):
    linux_user: str
    role: KbRole


class SyncRequest(BaseModel):
    all_users: bool = False


class AskRequest(BaseModel):
    kb: str
    question: str
    backend: str | None = None
    session_id: str | None = None


class PurgeRequest(BaseModel):
    confirm: bool = False


class RegisterMcpServiceRequest(BaseModel):
    service_key: str
    name: str
    endpoint_url: str
    headers: dict[str, Any] | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class UpdateMcpServiceStatusRequest(BaseModel):
    status: str


class UpdateMcpToolTypeRequest(BaseModel):
    tool_type: str


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


class ProfileResourceRuleRequest(BaseModel):
    resource_type: str
    resource_key: str


class ProfileResourcesRequest(BaseModel):
    resources: list[ProfileResourceRuleRequest] = Field(default_factory=list)


class ResourceProfilesRequest(BaseModel):
    profile_keys: list[str] = Field(default_factory=list)


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
