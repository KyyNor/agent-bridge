from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def node(key="a", **config):
    return {"id": key, "type": "agent", "name": key, "position": {"x": 10, "y": 20},
            "config": {"prompt": "original", "backend_key": "dsh", "mcp_enabled": False, **config}}


def result(prompt="new prompt", **fields):
    return {"summary": "updated", "notes": [], "name": "New", "prompt": prompt,
            "result_mode": "text", "output_schema_json": "", **fields}


def call(wm_paths, current, generated):
    client = TestClient(create_app(wm_paths, {"root"}))
    captured = {}

    async def run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ok=True, result=generated, run_key=None, error=None)

    client.app.state.agent_bridge_service.agents.run = run
    response = client.post('/api/v1/agent-runs/design/agent-node',
                           headers={'X-Agent-Bridge-User': 'root'},
                           json={'mode': 'modify', 'prompt': 'improve', 'current': current})
    return client, response, captured


def test_node_design_preserves_identity_execution_and_has_flat_schema(wm_paths):
    original = node(timeout_seconds=123)
    client, response, captured = call(wm_paths, {'node': original}, result())
    assert response.status_code == 200
    assert response.json()['ok'] is True
    updated = response.json()['result']['node']
    assert updated['id'] == original['id']
    assert updated['position'] == original['position']
    assert updated['config']['backend_key'] == 'dsh'
    assert updated['config']['timeout_seconds'] == 123
    assert updated['config']['prompt'] == 'new prompt'
    assert 'oneOf' not in str(captured['output_schema'])
    assert captured['agent_name'] == 'design_agent_node'
    assert client.post('/api/v1/agent-runs/design/workflow', json={}).status_code == 404


@pytest.mark.parametrize('generated', [result(result_mode='json', output_schema_json='{'),
                                      result(result_mode='json', output_schema_json='false'),
                                      result(result_mode='json', output_schema_json='{"type":"array"}'),
                                      result(prompt='{{ nodes.missing.output.text }}'),
                                      {'summary': 'a', 'workflow': {}}])
def test_node_design_rejects_invalid_generated_content(wm_paths, generated):
    _, response, _ = call(wm_paths, {'node': node()}, generated)
    assert response.status_code == 200
    assert response.json()['ok'] is False
    assert response.json()['result'] is None


def test_node_design_rejects_skippable_branch_reference(wm_paths):
    source = node('source');target = node('target');root = node('root')
    current = {'node': target, 'references': [{'path': 'nodes.source.output.text'}],
               'graph': {'nodes': [root, source, target], 'edges': [
                   {'id': 'r-s', 'source': 'root', 'target': 'source',
                    'condition': {'field': 'nodes.root.output.text', 'operator': 'equals', 'value': 'yes'}},
                   {'id': 's-t', 'source': 'source', 'target': 'target'}]}}
    _, response, _ = call(wm_paths, current, result(prompt='{{ nodes.source.output.text }}'))
    assert response.json()['ok'] is False
    assert '可能被跳过' in response.json()['error']


def test_node_design_requires_agent_node(wm_paths):
    _, response, captured = call(wm_paths, {'node': {'type': 'output'}}, result())
    assert response.status_code == 400
    assert not captured
