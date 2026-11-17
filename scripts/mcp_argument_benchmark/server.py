"""真实 stdio MCP 包装 + 模拟业务；记录原始请求和精确参数匹配结果。"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import validate

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import CASES

parser = argparse.ArgumentParser()
parser.add_argument('--variant', choices=['old', 'new'], required=True)
parser.add_argument('--log', required=True)
parser.add_argument('--baseline', required=True)
args = parser.parse_args()

def record(row):
    with open(args.log, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')

class Capabilities:
    def search(self, *, path='', query='', **kwargs):
        return {'items': [{'service': 'fixture', 'tool_name': name, 'description': description, 'input_schema': schema}
                          for name, description, schema, expected in CASES
                          if not query or query in name or query in description]}

    async def execute(self, *, service, tool_name, params, **kwargs):
        case = next((c for c in CASES if c[0] == tool_name), None)
        if service != 'fixture' or case is None:
            raise ValueError('未知模拟工具')
        if args.variant == 'new':
            from agent_bridge.capability_hub.execute_arguments import validate_execute_params
            validate_execute_params(params, case[2])
        else:
            validate(params, case[2])
        correct = json.dumps(params, sort_keys=True, ensure_ascii=False) == json.dumps(case[3], sort_keys=True, ensure_ascii=False)
        record({'event': 'business', 'tool': tool_name, 'correct': correct, 'params': params})
        if not correct:
            raise ValueError('参数合法，但与任务要求不一致；请检查原始需求')
        return {'success': True, 'result': {'fixture': tool_name, 'verified': True}}

class Service:
    capabilities = Capabilities()

if args.variant == 'old':
    spec = importlib.util.spec_from_file_location('benchmark_old_metamcp', args.baseline)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
else:
    from agent_bridge.capability_hub.gateway import metamcp as module

server = module.create_mcp_server(Service())
# 使用低层公开注册器记录调用；仍经过原生产 FastMCP 调用入口。
@server._mcp_server.call_tool(validate_input=False)
async def logged(name, arguments):
    record({'event': 'request', 'tool': name, 'arguments': arguments})
    try:
        result = await server.call_tool(name, arguments)
        record({'event': 'response', 'tool': name, 'ok': True})
        return result
    except Exception as exc:
        record({'event': 'response', 'tool': name, 'ok': False, 'error': str(exc)})
        raise
server.run()
