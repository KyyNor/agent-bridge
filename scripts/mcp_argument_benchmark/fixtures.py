"""只含虚构数据的 MCP 参数兼容性样本，不执行代码、不访问业务系统。"""

def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': required or list(properties), 'additionalProperties': False}

S = {'type': 'string'}
I = {'type': 'integer'}
CASES = [
    ('python_run', '执行 Python（仅模拟），保留代码、嵌套环境和资源限制。',
     obj({'code': S, 'environment': obj({'variables': obj({'REGION': S}), 'packages': {'type': 'array', 'items': S}}), 'limits': obj({'seconds': I, 'network': {'type': 'boolean'}})}),
     {'code': 'print({"中文": [1, 2]})', 'environment': {'variables': {'REGION': '内网'}, 'packages': ['pandas==2.2.3']}, 'limits': {'seconds': 30, 'network': False}}),
    ('sql_query', '执行带复合过滤器的查询（仅模拟）。',
     obj({'query': S, 'bindings': {'type': 'array', 'items': {'oneOf': [S, I, {'type': 'null'}]}}, 'output': obj({'format': {'enum': ['json', 'csv']}, 'columns': {'type': 'array', 'items': S}})}),
     {'query': 'SELECT * FROM orders WHERE region = ? AND amount > ? AND deleted IS ?', 'bindings': ['华东', 1200, None], 'output': {'format': 'json', 'columns': ['id', 'amount']}}),
    ('file_publish', '发布文件（仅模拟），path 是合法的对象字段，不是参数包装。',
     obj({'path': obj({'directory': S, 'filename': S}), 'content': S, 'overwrite': {'const': False}}),
     {'path': {'directory': '报表/2026', 'filename': 'a.json'}, 'content': '{"params":{"code":"literal"}}', 'overwrite': False}),
    ('batch_transform', '转换批量记录（仅模拟），保留嵌套数组、空对象和布尔值。',
     obj({'rows': {'type': 'array', 'minItems': 2, 'items': obj({'id': I, 'tags': {'type': 'array', 'items': S}, 'enabled': {'type': 'boolean'}})}, 'options': {'type': 'object', 'additionalProperties': False}}),
     {'rows': [{'id': 1, 'tags': ['甲', '乙'], 'enabled': True}, {'id': 2, 'tags': [], 'enabled': False}], 'options': {}}),
    ('route_probe', '测试路由字段重名（仅模拟）：service/tool_name/params 都是业务参数。',
     obj({'service': S, 'tool_name': S, 'params': obj({'input': S})}),
     {'service': 'business-service', 'tool_name': 'business-operation', 'params': {'input': '原样保留'}}),
    ('artifact_export', '按目的地联合类型导出产物（仅模拟）。',
     obj({'destination': {'oneOf': [obj({'kind': {'const': 'file'}, 'path': S}), obj({'kind': {'const': 'memory'}, 'key': S})]}, 'selection': obj({'ids': {'type': 'array', 'items': I}, 'comment': {'type': ['string', 'null']}})}),
     {'destination': {'kind': 'file', 'path': 'out/a b.json'}, 'selection': {'ids': [7, 19], 'comment': None}}),
]
