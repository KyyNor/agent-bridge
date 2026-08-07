from .code import CodeRunner
from .opencompass import OpenCompassRunner
from .protocol import EvaluationRunner, ExecutionRequest
from .swebench import SWEbenchRunner

RUNNERS: dict[str, EvaluationRunner] = {
    OpenCompassRunner.key: OpenCompassRunner(),
    CodeRunner.key: CodeRunner(),
    SWEbenchRunner.key: SWEbenchRunner(),
}

__all__ = ["CodeRunner", "EvaluationRunner", "ExecutionRequest", "OpenCompassRunner", "RUNNERS", "SWEbenchRunner"]
