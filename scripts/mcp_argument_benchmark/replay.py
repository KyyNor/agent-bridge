"""独立于模型的错误注入；经过真实 MCP stdio 协议比较旧/新入口。"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from fixtures import CASES

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--output', required=True)
p.add_argument('--baseline', required=True)
a = p.parse_args()
output = Path(a.output).resolve()
if any((output / name).exists() for name in ('results.json', 'old-calls.jsonl', 'new-calls.jsonl')):
    raise SystemExit('输出目录已有运行证据，请使用新目录。')
output.mkdir(parents=True, exist_ok=True)
route = {'service': 'fixture', 'tool_name': CASES[0][0]}
business = CASES[0][3]
samples = [
 ('standard', {**route, 'params': business}, True),
 ('flat', {**route, **business}, True),
 ('mixed', {**route, 'params': {'code': business['code']}, **{k:v for k,v in business.items() if k != 'code'}}, True),
 ('empty_params_flat', {**route, 'params': {}, **business}, True),
 ('identical_duplicate', {**route, 'params': business, 'code': business['code']}, True),
 ('conflicting_duplicate', {**route, 'params': business, 'code': 'WRONG'}, False),
 ('wrong_wrapper', {**route, 'path': business}, False),
 ('string_params', {**route, 'params': json.dumps(business)}, False),
 ('wrong_type', {**route, 'params': {**business, 'limits': {'seconds': '30', 'network': False}}}, False),
]
for name, desc, schema, params in CASES[1:]:
 samples.append((name, {'service': 'fixture', 'tool_name': name, 'params': params}, True))

async def main():
 results = []
 for variant in ['old', 'new']:
  log = output / f'{variant}-calls.jsonl'
  config = StdioServerParameters(command=str(ROOT / '.venv/bin/python'), args=[str(ROOT / 'scripts/mcp_argument_benchmark/server.py'), '--variant', variant, '--log', str(log), '--baseline', str(Path(a.baseline).resolve())])
  async with stdio_client(config) as (read, write):
   async with ClientSession(read, write) as session:
    await session.initialize()
    for name, arguments, expected in samples:
     response = await session.call_tool('execute', arguments)
     results.append({'variant': variant, 'case': name, 'expected_success': expected, 'is_error': response.isError, 'content': [c.model_dump(mode='json') for c in response.content]})
 (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
 print(json.dumps([{k:v for k,v in r.items() if k != 'content'} for r in results], ensure_ascii=False, indent=2))

asyncio.run(main())
