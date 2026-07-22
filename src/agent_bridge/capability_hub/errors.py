"""能力执行错误及其审计上下文。

错误分类是能力执行协议的一部分，不应通过 ``setattr`` 临时挂到任意异常对象上。
本模块用不可变元数据和明确的异常类型保存审计信息，同时保留原有领域异常的
HTTP 语义（400/404/500）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage
from agent_bridge.core.domain import AgentBridgeError, NotFound, ValidationError


@dataclass(frozen=True)
class CapabilityFailure:
    """一次能力失败的稳定审计字段。"""

    status: str = CallLogStatus.error.value
    stage: str = FailureStage.internal.value
    owner: str = FailureOwner.platform.value
    error_type: str = "internal_error"
    resource_type: str | None = None
    resource_key: str | None = None
    log_id: str | None = None


class _CapabilityErrorMixin:
    """为领域异常增加类型化失败上下文，并支持不可变式派生。"""

    failure: CapabilityFailure
    base_message: str

    def __init__(self, message: str, *, failure: CapabilityFailure | None = None) -> None:
        self.base_message = message
        self.failure = failure or CapabilityFailure()
        rendered = message
        if self.failure.log_id:
            rendered = f"{message} (log_id: {self.failure.log_id})"
        super().__init__(rendered)

    def with_failure(self, **changes: str | None) -> "_CapabilityErrorMixin":
        return type(self)(self.base_message, failure=replace(self.failure, **changes))


class CapabilityValidationError(_CapabilityErrorMixin, ValidationError):
    """带审计上下文的能力参数、策略或上游执行错误。"""


class CapabilityNotFoundError(_CapabilityErrorMixin, NotFound):
    """带审计上下文的能力目录不存在错误。"""


class CapabilityInternalError(_CapabilityErrorMixin, AgentBridgeError):
    """带审计上下文的能力平台内部错误。"""


CapabilityError = CapabilityValidationError | CapabilityNotFoundError | CapabilityInternalError


def _error_class(exc: Exception) -> type[CapabilityError]:
    if isinstance(exc, NotFound):
        return CapabilityNotFoundError
    if isinstance(exc, ValidationError):
        return CapabilityValidationError
    return CapabilityInternalError


def capability_failure(
    exc: Exception,
    *,
    status: str | None = None,
    stage: str | None = None,
    owner: str | None = None,
    error_type: str | None = None,
    resource_type: str | None = None,
    resource_key: str | None = None,
) -> CapabilityError:
    """返回带类型化审计上下文的新异常，不修改传入异常。"""

    if isinstance(exc, _CapabilityErrorMixin):
        current = exc.failure
        error_cls = type(exc)
        message = exc.base_message
    else:
        current = CapabilityFailure()
        error_cls = _error_class(exc)
        message = str(exc)
    return error_cls(
        message,
        failure=replace(
            current,
            status=status if status is not None else current.status,
            stage=stage if stage is not None else current.stage,
            owner=owner if owner is not None else current.owner,
            error_type=error_type if error_type is not None else current.error_type,
            resource_type=resource_type if resource_type is not None else current.resource_type,
            resource_key=resource_key if resource_key is not None else current.resource_key,
        ),
    )


def with_log_id(exc: Exception, log_id: str) -> CapabilityError:
    """为待抛出的异常创建带日志编号的新实例。"""

    typed = capability_failure(exc)
    return type(typed)(typed.base_message, failure=replace(typed.failure, log_id=log_id))


def failure_metadata(exc: Exception) -> CapabilityFailure:
    """读取稳定失败元数据；普通异常使用平台内部错误默认值。"""

    if isinstance(exc, _CapabilityErrorMixin):
        return exc.failure
    return CapabilityFailure()
