"""All fixtures are synthetic; no real CLI or Keychain command is executed."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/claude-environment-cleanup/scripts/cleanup.py'
spec = importlib.util.spec_from_file_location('cleanup', SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.identity = self.home / '.claude.json'
        self.original = {'userID': 'synthetic-user', 'oauthAccount': {'id': 'synthetic-account'},
                         'machineID': 'preserve-machine', 'mcpServers': {'local': {'command': 'demo'}},
                         'hasCompletedOnboarding': True, 'numStartups': 12, 'unrelated': [1, 2]}
        self.identity.write_text(json.dumps(self.original))
        self.write('.claude/projects/demo/session.jsonl', 'synthetic-history')
        self.write('.claude/skills/local/SKILL.md', 'synthetic-skill')
        self.write('.claude/settings.json', '{"env":{},"permissions":{"allow":[]}}')
        self.write('.claude/backups/.claude.json.backup.1', json.dumps(self.original))
        self.write('.claude/cache/data', 'synthetic-cache')
        self.write('.claude/.credentials.json', '{"synthetic":true}')
        self.write('.codex/auth.json', 'synthetic-protected-auth')
        self.write('.zshrc', '# synthetic shell configuration\n')
        self.calls = []
        self.processes = ''
        self.logout_code = 0
        self.keychain_code = 44
        self.logged_in = False
        self.addCleanup(patch.stopall)
        patch.object(cleanup.platform, 'system', return_value='Darwin').start()
        patch.object(cleanup.shutil, 'which', return_value='/synthetic/claude').start()
        patch.object(cleanup.subprocess, 'run', side_effect=self.command).start()
        patch.dict(os.environ, {}, clear=True).start()

    def write(self, name, text):
        p = self.home / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == 'ps':
            return subprocess.CompletedProcess(args, 0, self.processes, '')
        if args[0] == 'security':
            return subprocess.CompletedProcess(args, self.keychain_code, '', 'synthetic-private-error')
        if args == ['/synthetic/claude', 'auth', 'logout']:
            # A real filesystem backup must exist before the first auth mutation.
            backups = list((self.home / '.claude-cleanup-backups').glob('*/backup/claude.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), self.original)
            if self.logout_code == 0:
                data = json.loads(self.identity.read_text())
                data.pop('oauthAccount', None)
                self.identity.write_text(json.dumps(data))
                (self.home / '.claude/.credentials.json').unlink(missing_ok=True)
                self.write('.claude/backups/.claude.json.backup.2', json.dumps(self.original))
            return subprocess.CompletedProcess(args, self.logout_code, 'synthetic-private-output', '')
        if args == ['/synthetic/claude', 'auth', 'status']:
            return subprocess.CompletedProcess(args, 0 if self.logged_in else 1,
                json.dumps({'loggedIn': self.logged_in, 'authMethod': 'none',
                            'configDirectory': str(self.home / '.claude')}), '')
        raise AssertionError('Unexpected external command: ' + repr(args))

    def test_preview_does_not_write_or_log_out(self):
        before = sorted(str(p.relative_to(self.home)) for p in self.home.rglob('*'))
        report = cleanup.clean(self.home)
        self.assertEqual(report['mode'], 'preview')
        self.assertEqual(json.loads(self.identity.read_text()), self.original)
        self.assertEqual(before, sorted(str(p.relative_to(self.home)) for p in self.home.rglob('*')))
        self.assertFalse(any('auth' in call for call in self.calls))

    def test_apply_preserves_data_and_quarantines_recovery_copies(self):
        result = cleanup.clean(self.home, apply=True)
        self.assertTrue(result['verified'])
        now = json.loads(self.identity.read_text())
        self.assertNotIn('userID', now)
        self.assertNotIn('oauthAccount', now)
        self.assertEqual(now['numStartups'], 0)
        self.assertIs(now['hasCompletedOnboarding'], False)
        for key in ('machineID', 'mcpServers', 'unrelated'):
            self.assertEqual(now[key], self.original[key])
        for rel, expected in [('.claude/projects/demo/session.jsonl', 'synthetic-history'),
                              ('.claude/skills/local/SKILL.md', 'synthetic-skill'),
                              ('.codex/auth.json', 'synthetic-protected-auth')]:
            self.assertEqual((self.home / rel).read_text(), expected)
        self.assertFalse(list((self.home / '.claude/backups').iterdir()))
        run = Path(result['record'])
        self.assertEqual(run.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(list((run / 'quarantine').iterdir())), 3)
        self.assertNotIn('synthetic-private-output', json.dumps(result))
        self.assertEqual(json.loads((run / 'backup/claude.json').read_text()), self.original)

    def test_malformed_json_stops_before_backup_or_logout(self):
        self.identity.write_text('{bad')
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertEqual(self.identity.read_text(), '{bad')
        self.assertFalse((self.home / '.claude-cleanup-backups').exists())

    def test_active_process_stops_before_logout(self):
        self.processes = '42 /somewhere/claude\n'
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertEqual(json.loads(self.identity.read_text()), self.original)

    def test_npm_claude_process_blocks_cleanup(self):
        self.processes = '42 /usr/local/bin/node node /synthetic/node_modules/@anthropic-ai/claude-code/cli.js --resume\n'
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertFalse(any('auth' in call for call in self.calls))

    def test_injection_is_reported_without_secret_and_not_removed(self):
        p = self.write('.claude/settings.json', '{"env":{"ANTHROPIC_API_KEY":"synthetic-secret"}}')
        report = cleanup.clean(self.home)
        self.assertTrue(report['blockers'])
        self.assertNotIn('synthetic-secret', json.dumps(report))
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertIn('synthetic-secret', p.read_text())

    def test_logout_failure_preserves_identity_and_recovery(self):
        self.logout_code = 2
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertEqual(json.loads(self.identity.read_text()), self.original)
        self.assertTrue((self.home / '.claude/backups/.claude.json.backup.1').exists())
        self.assertTrue(list((self.home / '.claude-cleanup-backups').glob('*/backup/claude.json')))

    def test_keychain_error_stops_before_reset(self):
        self.keychain_code = 36
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertEqual(json.loads(self.identity.read_text())['userID'], 'synthetic-user')

    def test_symlink_cache_does_not_touch_target(self):
        target = self.write('outside/valuable', 'keep')
        (self.home / '.cache').symlink_to(target.parent, target_is_directory=True)
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertEqual(target.read_text(), 'keep')

    def test_no_live_auth_during_nondefault_config(self):
        os.environ['CLAUDE_CONFIG_DIR'] = '/synthetic/custom'
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertFalse(any('auth' in call for call in self.calls))

    def test_logged_in_status_blocks_reset(self):
        self.logged_in = True
        with self.assertRaises(cleanup.CleanupError):
            cleanup.clean(self.home, apply=True)
        self.assertEqual(json.loads(self.identity.read_text())['userID'], 'synthetic-user')

    def test_backup_copy_failure_prevents_logout(self):
        with patch.object(cleanup.shutil, 'copytree', side_effect=OSError('synthetic-full-disk')):
            with self.assertRaises((cleanup.CleanupError, OSError)):
                cleanup.clean(self.home, apply=True)
        self.assertFalse(any('auth' in call for call in self.calls))
        self.assertEqual(json.loads(self.identity.read_text()), self.original)


    def test_credentials_reappearing_during_reset_stop_completion(self):
        original_reset = cleanup.reset_identity
        def race(*args, **kwargs):
            result = original_reset(*args, **kwargs)
            self.write('.claude/.credentials.json', '{"synthetic":true}')
            self.write('.claude/settings.local.json', '{"env":{"ANTHROPIC_API_KEY":"synthetic-new-secret"}}')
            return result
        with patch.object(cleanup, 'reset_identity', side_effect=race):
            with self.assertRaises(cleanup.CleanupError):
                cleanup.clean(self.home, apply=True)
        self.assertTrue((self.home / '.claude/.credentials.json').exists())

    def test_concurrent_identity_update_is_preserved(self):
        real_replace, real_rename = os.replace, os.rename
        def mutate():
            data = json.loads(self.identity.read_text())
            data['mcpServers']['new-server'] = {'command': 'preserve-concurrent-update'}
            self.identity.write_text(json.dumps(data))
        def replacing(src, dst, **kwargs):
            if Path(dst) == self.identity:
                mutate()
            return real_replace(src, dst, **kwargs)
        def renaming(src, dst, **kwargs):
            if Path(src).name == '.claude.json':
                mutate()
            return real_rename(src, dst, **kwargs)
        with patch.object(cleanup.os, 'replace', side_effect=replacing), patch.object(cleanup.os, 'rename', side_effect=renaming):
            with self.assertRaises(cleanup.CleanupError):
                cleanup.clean(self.home, apply=True)
        self.assertIn('new-server', json.loads(self.identity.read_text())['mcpServers'])

    def test_parent_replaced_with_link_during_move_never_moves_external_data(self):
        self.write('.cache/claude/original', 'original-cache')
        valuable = self.write('outside/claude/valuable', 'external-data')
        real_move, real_rename = cleanup.shutil.move, os.rename
        raced = False
        def switch_parent():
            nonlocal raced
            if not raced:
                raced = True
                real_rename(self.home / '.cache', self.home / 'cache-before-race')
                (self.home / '.cache').symlink_to(self.home / 'outside', target_is_directory=True)
        def moving(src, dst, **kwargs):
            if Path(src) == self.home / '.cache/claude':
                switch_parent()
            return real_move(src, dst, **kwargs)
        def renaming(src, dst, **kwargs):
            if Path(src).name == 'claude':
                switch_parent()
            return real_rename(src, dst, **kwargs)
        with patch.object(cleanup.shutil, 'move', side_effect=moving), patch.object(cleanup.os, 'rename', side_effect=renaming):
            try:
                cleanup.clean(self.home, apply=True)
            except cleanup.CleanupError:
                pass
        self.assertTrue(valuable.exists(), 'External data must not be relocated through a symlink')
        self.assertEqual(valuable.read_text(), 'external-data')


if __name__ == '__main__':
    unittest.main()
