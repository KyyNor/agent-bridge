#!/usr/bin/env python3
"""用本机 DSH 的真实 profile 合成器、Pi 的实际设置解析器验证托管预算。

PYTHONPATH=src python scripts/verify-compaction.py --pi-root <pi-coding-agent 包目录>
加 --with-tui 在临时 DSH_HOME 安装受管插件并启动真实 TUI。全程不调用模型。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time

from agent_bridge.agent_workspace.pi_injection import build_settings_payload
from agent_bridge.agent_workspace.terminal_adapter import TUI_PLUGIN_LIST
from agent_bridge.dsh.compaction import build_compaction_overlay
from agent_bridge.dsh.injection import dump_entry_list, load_entry_list
from agent_bridge.dsh.plugins import ensure_build_blocks, parse_plugin_list


TUI_PRESET_PROBE = '''
import { renameSync, writeFileSync } from 'node:fs';
export const inject = ['agentPresets'];
export function apply(ctx, { resultPath }) {
  const check = setInterval(async () => {
    try {
      const registry = ctx.get('agentPresets');
      const ids = ['standard', 'ptc', 'minimal', 'cordis', 'liangshen'];
      const documents = await Promise.all(ids.map(id => registry.readDocument(id)));
      writeFileSync(resultPath + '.tmp', JSON.stringify(documents));
      renameSync(resultPath + '.tmp', resultPath);
      clearInterval(check);
    } catch { /* Wait for TUI to register its presets. */ }
  }, 150);
  ctx.effect(() => () => clearInterval(check));
}
'''


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


def verify_budget(config, window, output):
    policy = next(p for p in config['modelPolicies'] if p['provider'] == 'agent-bridge' and p['model'] == 'verify-model')
    assert window - output - policy['headroomTokens'] == window - max(35_000, window * 15 // 100)


def read_tui_presets(command, *, env, root, result_path):
    import pty

    master, slave = pty.openpty()
    proc = None
    tail = b''
    try:
        try:
            proc = subprocess.Popen(command, env=env, cwd=root, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        finally:
            os.close(slave)
        deadline = time.monotonic() + 30
        while proc.poll() is None and time.monotonic() < deadline:
            if result_path.is_file():
                return json.loads(result_path.read_text())
            if select.select([master], [], [], 0.2)[0]:
                try:
                    tail = (tail + os.read(master, 65536))[-4000:]
                except OSError:
                    break
        raise RuntimeError(f'DSH TUI 未注册验证所需的 preset：{tail.decode(errors="replace")}')
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        os.close(master)


def verify_dsh_tui(dsh, root, env):
    command = [dsh, '--profile', 'tui']
    subprocess.run([dsh, 'plugin', '--profile', 'tui', 'install'], env=env, check=True, timeout=120)
    ensure_build_blocks(root / 'profiles' / 'tui')
    for spec in parse_plugin_list(TUI_PLUGIN_LIST.read_text()):
        subprocess.run([dsh, 'plugin', '--profile', 'tui', 'add', spec], env=env, check=True, timeout=240)

    terminal_home = root / 'terminal-home'
    terminal_home.mkdir()
    env = {**env, 'HOME': str(terminal_home), 'DSH_TUI_BACKEND': 'dsh', 'TERM': 'xterm-256color'}
    user_patch = root / 'user-tui.patch.yml'
    user_patch.write_text(dump_entry_list([{'insert': [{
        'id': 'user-standard-compaction', 'name': '@deepseek-harness-tui/dsh-tui/preset-patch',
        'config': {'preset': 'standard', 'config': {'compaction-basic': {'auto': False}}},
    }]}]))
    command += ['--patch', str(user_patch)]
    dump = subprocess.check_output([*command, '--dump-config'], env=env, text=True, timeout=30)
    window, output = 200_000, 32_768
    patches = build_compaction_overlay(load_entry_list(dump), models=['verify-model'], context_window=window, max_tokens=output)
    probe = root / 'preset-probe.mjs'
    probe.write_text(TUI_PRESET_PROBE)
    result_path = root / 'preset-probe.json'
    patches.append({'insert': [{'id': 'preset-probe', 'name': str(probe), 'config': {'resultPath': str(result_path)}}]})
    patch = root / 'verify-tui.patch.yml'
    patch.write_text(dump_entry_list(patches))
    for document in read_tui_presets([*command, '--patch', str(patch)], env=env, root=root, result_path=result_path):
        entries = load_entry_list(document['content'])
        if document['agentPreset'] == 'liangshen':
            # TUI 通过 cordis-plugin-include 加载此 preset，覆盖项在 include 的 patches 中。
            configs = [override['config'] for row in entries for override in row['config'].get('patches', [])
                       if override['id'] == 'compaction-basic']
        else:
            configs = list(compressors(entries))
        assert configs, document['agentPreset']
        for config in configs:
            verify_budget(config, window, output)
            if document['agentPreset'] == 'standard':
                assert config['auto'] is False
        print(f'DSH tui/{document["agentPreset"]}: window={window}, PASS')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi-root', type=Path, required=True)
    parser.add_argument('--with-tui', action='store_true', help='安装钉版 TUI 插件并检查运行期 preset')
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
                    verify_budget(config, window, output)
                print(f'DSH {profile}: window={window}, active_compressors={len(effective)}, PASS')
        if args.with_tui:
            verify_dsh_tui(dsh, root, env)
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
