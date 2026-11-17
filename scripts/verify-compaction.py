#!/usr/bin/env python3
"""用本机 DSH 的真实 profile 合成器、Pi 的实际设置解析器验证托管预算。

PYTHONPATH=src python scripts/verify-compaction.py --pi-root <pi-coding-agent 包目录>
不调用模型，不修改用户配置。仅在临时 DSH_HOME 内合成配置。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from agent_bridge.agent_workspace.pi_injection import build_settings_payload
from agent_bridge.dsh.compaction import build_compaction_overlay
from agent_bridge.dsh.injection import dump_entry_list, load_entry_list


def compressors(entries):
    for row in entries:
        if row.get('disabled'):
            continue
        if row.get('name') == '@deepseek-ai/dsh-compaction-basic':
            yield row.get('config', {})
        config = row.get('config')
        if row.get('group') and isinstance(config, list):
            yield from compressors(config)
        if row.get('name') == '@deepseek-ai/dsh-agent-preset':
            yield from compressors(config.get('plugins', []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi-root', type=Path, required=True)
    args = parser.parse_args()
    dsh = shutil.which('dsh')
    if not dsh:
        parser.error('需要本机 dsh')
    with tempfile.TemporaryDirectory(prefix='agent-bridge-compaction-') as directory:
        root = Path(directory)
        env = {**os.environ, 'DSH_HOME': str(root)}
        for name in ('sessions', 'storages', 'change-ledger', 'task-board'):
            (root / name).mkdir()
        for profile in ('web', 'acp'):
            def dump(patch=None):
                command = [dsh, '--profile', profile, '--dump-config']
                if patch:
                    command += ['--patch', str(patch)]
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise RuntimeError(f'DSH {profile} 配置合成失败，退出码 {result.returncode}')
                return load_entry_list(result.stdout)

            original = dump()
            for window, output in ((200_000, 32_768), (1_000_000, 65_536)):
                patches = build_compaction_overlay(original, models=['verify-model'], context_window=window, max_tokens=output)
                patch = root / 'verify.patch.yml'
                patch.write_text(dump_entry_list(patches))
                effective = list(compressors(dump(patch)))
                assert effective
                for config in effective:
                    policy = next(p for p in config['modelPolicies'] if p['provider'] == 'agent-bridge' and p['model'] == 'verify-model')
                    assert window - output - policy['headroomTokens'] == window - max(35_000, window * 15 // 100)
                print(f'DSH {profile}: window={window}, active_compressors={len(effective)}, PASS')
        for window in (200_000, 1_000_000):
            settings = build_settings_payload({}, default_model='verify-model', models=['verify-model'], context_window=window)
            javascript = '''
import {SettingsManager} from SETTINGS;
import {shouldCompact} from COMPACTION;
const [payload, window] = JSON.parse(process.argv[1]);
const settings = SettingsManager.inMemory(payload).getCompactionSettings({provider:'agent-bridge',id:'verify-model'});
const boundary = window - settings.reserveTokens;
if (shouldCompact(boundary,window,settings) || !shouldCompact(boundary+1,window,settings)) throw Error('错误的压缩边界');
console.log(`Pi: window=${window}, reserve=${settings.reserveTokens}, PASS`);
'''.replace('SETTINGS', json.dumps((args.pi_root / 'dist/core/settings-manager.js').resolve().as_uri())).replace('COMPACTION', json.dumps((args.pi_root / 'dist/core/compaction/compaction.js').resolve().as_uri()))
            subprocess.run(['node', '--input-type=module', '-e', javascript, json.dumps([settings, window])], check=True, timeout=30)


if __name__ == '__main__':
    main()
