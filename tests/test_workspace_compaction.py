"""托管预算、实际 preset 合并与配置清空行为。"""
from copy import deepcopy
import math

import pytest

from agent_bridge.agent_workspace.compaction import compaction_reserve
from agent_bridge.agent_workspace.pi_injection import build_settings_payload
from agent_bridge.core.domain import ValidationError
from agent_bridge.dsh.compaction import build_compaction_overlay
from agent_bridge.dsh.config import normalize_context_window
from agent_bridge.dsh.injection import dump_entry_list, load_entry_list


@pytest.mark.parametrize(('window', 'reserve'), [(0, 0), (200_000, 35_000), (233_333, 35_000), (233_334, 35_001), (1_000_000, 150_000)])
def test_reserve_boundary(window, reserve):
    assert compaction_reserve(window) == reserve


@pytest.mark.parametrize('window', [1, 34_999, 35_000])
def test_small_window_rejected_at_config_boundary(window):
    with pytest.raises(ValidationError, match='35000'):
        normalize_context_window(window)


def profile_entries():
    return load_entry_list('''
- id: compaction-basic
  name: '@deepseek-ai/dsh-compaction-basic'
  disabled: true
- id: preset-standard
  name: '@deepseek-ai/dsh-agent-preset'
  config:
    id: standard
    custom: !!js process.platform
    plugins:
      - id: tool-fs
        name: '@deepseek-ai/dsh-tool-fs'
      - id: compaction
        group: true
        isolate: {compaction: true}
        config:
          - id: compaction-basic
            name: '@deepseek-ai/dsh-compaction-basic'
            config:
              retainRatio: 0.1
              modelPolicies:
                - provider: own
                  model: custom
                  headroomTokens: 8192
''')


@pytest.mark.parametrize('window,output', [(200_000, 0), (200_000, 16_384), (200_000, 32_768), (1_000_000, 65_536)])
def test_dsh_budget_in_live_preset_preserves_plugins_and_tags(window, output):
    entries = profile_entries()
    original = dump_entry_list(entries)
    patches = build_compaction_overlay(entries, models=['m'], context_window=window, max_tokens=output)
    assert [p['id'] for p in patches] == ['preset-standard']
    config = patches[0]['config']
    assert config['plugins'][0] == entries[1]['config']['plugins'][0]
    assert config['custom'].tag == 'tag:yaml.org,2002:js'
    policies = config['plugins'][1]['config'][0]['config']['modelPolicies']
    assert policies[0] == {'provider': 'own', 'model': 'custom', 'headroomTokens': 8192}
    policy = policies[1]
    threshold = math.floor(min(window * policy['thresholdRatio'], window - output - policy['headroomTokens']))
    assert threshold == window - compaction_reserve(window)
    assert policy['maxTokens'] > 0
    assert dump_entry_list(entries) == original
    assert dump_entry_list(load_entry_list(dump_entry_list(patches))) == dump_entry_list(patches)


def test_dsh_output_larger_than_budget_uses_upstream_guard(caplog):
    patches = build_compaction_overlay(profile_entries(), models=['m'], context_window=200_000, max_tokens=64_000)
    policy = patches[0]['config']['plugins'][1]['config'][0]['config']['modelPolicies'][1]
    assert policy['headroomTokens'] == 0
    assert min(200_000 * policy['thresholdRatio'], 200_000 - 64_000) == 136_000
    assert '将提前压缩' in caplog.text


def test_dsh_acp_root_and_disabled_presets():
    entries = profile_entries()
    entries[0].pop('disabled')
    entries[1]['disabled'] = True
    patches = build_compaction_overlay(entries, models=['m'], context_window=200_000, max_tokens=32_768)
    assert [p['id'] for p in patches] == ['compaction-basic']
    assert patches[0]['config']['modelPolicies'][0]['headroomTokens'] == 2232
    with pytest.raises(ValidationError, match='没有可用压缩器'):
        build_compaction_overlay([], models=['m'], context_window=200_000, max_tokens=0)


def test_zero_window_leaves_dsh_upstream_policy():
    assert build_compaction_overlay([], models=['m'], context_window=0, max_tokens=0) == []


def test_pi_managed_models_keep_other_settings_and_clear_stale_reserve():
    existing = {'theme': 'dark', 'compaction': {'keepRecentTokens': 12_000, 'modelOverrides': {
        'own/m': {'reserveTokens': 4000},
        'agent-bridge/m': {'reserveTokens': 1000, 'keepRecentTokens': 8000},
        'agent-bridge/removed': {'reserveTokens': 9999},
    }}}
    original = deepcopy(existing)
    payload = build_settings_payload(existing, default_model='m', models=['m', 'n'], context_window=200_000)
    overrides = payload['compaction']['modelOverrides']
    assert overrides['agent-bridge/m'] == {'reserveTokens': 35_000, 'keepRecentTokens': 8000}
    assert overrides['agent-bridge/n'] == {'reserveTokens': 35_000}
    assert overrides['own/m'] == original['compaction']['modelOverrides']['own/m']
    assert 'agent-bridge/removed' not in overrides
    assert payload['compaction']['keepRecentTokens'] == 12_000
    assert existing == original
    cleared = build_settings_payload(payload, default_model='m', models=['m'], context_window=0)
    assert cleared['compaction']['modelOverrides']['agent-bridge/m'] == {'keepRecentTokens': 8000}
    assert 'agent-bridge/n' not in cleared['compaction']['modelOverrides']


def test_resolver_merges_profile_once_and_fails_closed_on_dump_error(tmp_path):
    from agent_bridge.dsh.injection import resolve_model_overlay

    calls = []
    def reader(**kwargs):
        calls.append(kwargs)
        return profile_entries()

    kwargs = dict(dsh_home=tmp_path, dsh_binary='dsh', profile='web', base_url='http://model/v1',
                  models=['m'], default_model='m', env={}, cwd=tmp_path, identity=None,
                  context_window=200_000, max_tokens=32_768)
    overlay = resolve_model_overlay(**kwargs, config_reader=reader)
    assert len(calls) == 1
    assert {e['id'] for e in overlay} == {'llm-pi-ai', 'agent-default-model', 'preset-standard'}

    def broken_reader(**kwargs):
        raise RuntimeError('配置合成失败')

    with pytest.raises(ValidationError, match='无法应用托管预算'):
        resolve_model_overlay(**kwargs, config_reader=broken_reader)
