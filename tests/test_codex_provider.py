import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eviepoch.config import Settings
from eviepoch.providers import ProviderUnavailable, StructuredOutputInvalid
from experiments.codex_provider import CodexCLIProviders, ROOT


class CodexProviderControls(unittest.TestCase):
    def setUp(self):
        (ROOT / 'logs').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'logs')
        self.config = Settings(model_policy='research', llm_model='gpt-6-luna',
                               llm_api_key='codex-cli-auth-not-an-api-key')
        self.provider = CodexCLIProviders(self.config, cache_dir=self.temp.name, max_calls=1, executable='codex-test')
        self.commands = []

    def tearDown(self):
        self.temp.cleanup()

    def response(self, command, **kwargs):
        self.commands.append((command, kwargs))
        target = Path(command[command.index('--output-last-message')+1])
        target.write_text('{"answer":"synthetic"}', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'turn.completed',
            'usage': {'input_tokens': 80, 'output_tokens': 6, 'cached_input_tokens': 40}})+'\n', stderr='')

    def test_exact_cache_hit_avoids_a_new_cli_call_and_prompt_change_hits_budget(self):
        with patch('experiments.codex_provider.subprocess.run', side_effect=self.response) as run:
            first = self.provider._structured('system', 'data', stage='answer')
            self.assertEqual(self.provider._structured('system', 'data', stage='answer'), first)
            with self.assertRaises(ProviderUnavailable):
                self.provider._structured('system', 'changed data', stage='answer')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.provider.usage['codex_calls'], 1)
        self.assertEqual(self.provider.usage['codex_cache_hits'], 1)

    def test_separate_provider_instances_share_cache_and_single_launch(self):
        other = CodexCLIProviders(self.config, cache_dir=self.temp.name, max_calls=1, executable='codex-test')
        with patch('experiments.codex_provider.subprocess.run', side_effect=self.response) as run:
            with ThreadPoolExecutor(2) as pool:
                values = list(pool.map(lambda p: p._structured('system', 'same data'), [self.provider, other]))
        self.assertEqual(values[0], values[1]); self.assertEqual(run.call_count, 1)

    def test_cli_uses_read_only_no_tools_and_does_not_inherit_api_billing_keys(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-secret', 'DEEPSEEK_API_KEY': 'synthetic-secret'}):
            with patch('experiments.codex_provider.subprocess.run', side_effect=self.response):
                self.provider._structured('system', 'data')
        command, options = self.commands[0]
        self.assertIn('--ignore-user-config', command); self.assertIn('--ephemeral', command)
        self.assertEqual(command[command.index('--sandbox')+1], 'read-only')
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('features.apps=false', command)
        self.assertNotIn('OPENAI_API_KEY', options['env']); self.assertNotIn('DEEPSEEK_API_KEY', options['env'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)

    def test_cli_failure_consumes_launch_budget_and_never_uses_http_fallback(self):
        with patch('experiments.codex_provider.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='synthetic error')) as run:
            with patch.object(self.provider, '_client', side_effect=AssertionError('HTTP fallback')):
                for _ in range(2):
                    with self.assertRaises(ProviderUnavailable):
                        self.provider._structured('system', 'data')
        self.assertEqual(run.call_count, 1)

    def test_timeout_does_not_trigger_retries(self):
        with patch('experiments.codex_provider.subprocess.run', side_effect=subprocess.TimeoutExpired('codex-test', 1)) as run:
            with self.assertRaises(ProviderUnavailable):
                self.provider._structured('system', 'data')
        self.assertEqual(run.call_count, 1)

    def test_tool_activity_is_rejected_and_not_cached(self):
        response = SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'item.completed',
            'item': {'type': 'command_execution'}})+'\n'+json.dumps({'type': 'turn.completed'}), stderr='')
        with patch('experiments.codex_provider.subprocess.run', return_value=response):
            with self.assertRaises(ProviderUnavailable):
                self.provider._structured('system', 'data')
        self.assertFalse(list(Path(self.temp.name).glob('*.json')))

    def test_non_json_final_output_is_rejected(self):
        def invalid(command, **kwargs):
            response = self.response(command, **kwargs)
            Path(command[command.index('--output-last-message')+1]).write_text('not JSON', encoding='utf-8')
            return response
        with patch('experiments.codex_provider.subprocess.run', side_effect=invalid):
            with self.assertRaises(StructuredOutputInvalid):
                self.provider._structured('system', 'data')

    def test_competition_configuration_cannot_use_research_adapter(self):
        with self.assertRaises(ValueError):
            CodexCLIProviders(Settings(), executable='codex-test')
        with self.assertRaises(ProviderUnavailable):
            self.provider._complete('system', 'data')
