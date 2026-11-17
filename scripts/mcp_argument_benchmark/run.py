"""运行隔离的 CLI 会话；真实模型工具调用，模拟业务结果，不测性能。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from fixtures import CASES

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--output', required=True)
p.add_argument('--baseline', required=True)
p.add_argument('--model', required=True)
p.add_argument('--client', choices=['cmd', 'codex'], required=True)
p.add_argument('--variant', choices=['old', 'new'], required=True)
a = p.parse_args()
folder = Path(a.output).resolve()
if (folder / 'status.json').exists() or (folder / 'calls.jsonl').exists():
    raise SystemExit('输出目录已有运行证据，请使用新目录，避免混入历史调用。')
folder.mkdir(parents=True, exist_ok=True)
server_args = [str(ROOT / 'scripts/mcp_argument_benchmark/server.py'), '--variant', a.variant, '--log', str(folder / 'calls.jsonl'), '--baseline', str(Path(a.baseline).resolve())]
config = {'command': str(ROOT / '.venv/bin/python'), 'args': server_args}
(folder / '.commandcode').mkdir(exist_ok=True)
(folder / '.commandcode/settings.json').write_text(json.dumps({'permissions': {'allow': ['mcp__argument_lab__search', 'mcp__argument_lab__execute']}}))
(folder / '.mcp.json').write_text(json.dumps({'mcpServers': {'argument_lab': config}}))
prompt = '''你是 MCP 调用评测参与者。仅使用 argument_lab 的 search 和 execute 工具完成下面6个任务，每个任务成功一次即可。工具都是无副作用的模拟工具。先搜索工具目录，根据工具描述和参数schema调用；失败可以纠正，每个任务最多尝试3次，然后继续下一个。不要使用shell、文件工具或其他MCP，不查看实现，不编写代码模拟调用，不解释工具使用方法代替实际调用。以下JSON是各目标工具要求的业务参数，必须保持所有值和结构不变。服务是fixture。完成后简短报告。\n'''
for name, desc, schema, expected in CASES:
    prompt += f'任务 {name}: {json.dumps(expected, ensure_ascii=False)}\n'
(folder / 'prompt.txt').write_text(prompt)
if a.client == 'cmd':
    command = ['cmd', '-p', prompt, '--model', a.model, '--output-format', 'json', '--max-turns', '35', '--trust', '--permission-mode', 'dont-ask', '--no-skills', '--skip-onboarding', '--no-auto-update', '--no-session']
else:
    command = ['codex', 'exec', '--skip-git-repo-check', '--ephemeral', '--json', '-m', a.model, '-c', 'model_reasoning_effort="low"', '-c', 'mcp_servers.argument_lab.command=' + json.dumps(config['command']), '-c', 'mcp_servers.argument_lab.args=' + json.dumps(config['args']), prompt]
started = time.monotonic()
with (folder / 'transcript.jsonl').open('w') as out, (folder / 'stderr.log').open('w') as err:
    try:
        result = subprocess.run(command, cwd=folder, stdout=out, stderr=err, timeout=480)
        status = {'exit_code': result.returncode}
    except subprocess.TimeoutExpired:
        status = {'timeout': True}
status.update({'client': a.client, 'model': a.model, 'variant': a.variant, 'elapsed_seconds': round(time.monotonic()-started, 2)})
(folder / 'status.json').write_text(json.dumps(status, indent=2))
print(json.dumps(status))
