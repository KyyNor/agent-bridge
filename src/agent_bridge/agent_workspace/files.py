"""工作台文件领域服务：通过统一 launcher 降权，helper 内约束目录与文件类型。"""
from __future__ import annotations

import json
import logging
import sys
import time

from agent_bridge.agent_workspace.directory import WorkspaceDirectory
from agent_bridge.agent_workspace.launcher import LinuxIdentity, run_identity_command
from agent_bridge.core.domain import AccessDenied, ConflictError, NotFound, ValidationError

logger = logging.getLogger(__name__)


class WorkspaceFileService:
    def execute(self, identity: LinuxIdentity, payload: dict, data: bytes = b'') -> dict | bytes:
        started = time.monotonic()
        result = run_identity_command(
            command=[sys.executable, '-m', 'agent_bridge.agent_workspace.file_worker', json.dumps(payload)],
            identity=identity, data=data,
        )
        if result.returncode:
            logger.warning('工作台文件操作失败 user=%s action=%s 状态=%s 耗时=%.2fs',
                           identity.user, payload['action'], result.returncode, time.monotonic() - started)
            try:
                error = json.loads(result.stderr)
            except (ValueError, UnicodeError) as exc:
                raise ValidationError('文件操作失败，请检查服务日志') from exc
            errors = {'denied': AccessDenied, 'not_found': NotFound, 'conflict': ConflictError}
            raise errors.get(error.get('kind'), ValidationError)(error.get('error') or '文件操作失败')
        logger.info('工作台文件操作完成 user=%s action=%s 状态=成功 耗时=%.2fs',
                    identity.user, payload['action'], time.monotonic() - started)
        if payload['action'] == 'download':
            return result.stdout
        return json.loads(result.stdout)

    def browse(self, identity: LinuxIdentity, directory: str | None = None) -> dict:
        return self.execute(identity, {'action': 'browse', 'directory': directory or str(identity.home)})

    def selected(self, identity: LinuxIdentity, root: WorkspaceDirectory, action: str,
                 path: str = '', name: str = '', data: bytes = b'') -> dict | bytes:
        return self.execute(identity, {'action': action, 'root': root.payload(), 'path': path, 'name': name}, data)
