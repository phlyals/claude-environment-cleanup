#!/usr/bin/env python3
"""Preview or back up, log out and reset the default macOS Claude Code profile."""
import argparse
from contextlib import contextmanager, ExitStack
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile

REMOVE = {
    'userID', 'hasAvailableSubscription', 'subscriptionNoticeCount', 'clientDataCache',
    'oauthAccount', 'clientDataCacheSlots', 'additionalModelOptionsCache',
    'additionalModelOptionsAnsweredAt', 'additionalModelCostsCache', 'modelAccessCache',
    'orgModelDefaultCache', 'autoCompactWindowsCache', 'cachedExperimentFeatures',
    'cachedExperimentData', 'cachedGrowthBookFeatures', 'cachedGrowthBookFeaturesAt',
    'cachedArtifactRoster', 'groveConfigCache', 'passesEligibilityCache',
    'cachedExtraUsageDisabledReason', 'feedbackSurveyState', 'cachedUsageUtilization',
}
RESET = {'numStartups': 0, 'hasCompletedOnboarding': False}
CACHES = (
    '.claude/cache', '.claude/telemetry', '.claude/stats-cache.json', '.claude/usage-data',
    '.claude/usage.jsonl', '.claude/usage.with-fix.jsonl',
    'Library/Caches/claude-cli-nodejs', '.cache/claude',
)
AUTH_KEYS = {
    'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
    'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR',
    'CLAUDE_CODE_API_KEY_HELPER_TTL_MS', 'CLAUDE_CODE_USE_BEDROCK',
    'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
}
SHELL_FILES = ('.zshrc', '.zprofile', '.zshenv', '.profile', '.bashrc', '.bash_profile')
EXTERNAL = ('.codex/auth.json', '.codex/config.toml', '.cc-switch/settings.json',
            '.gemini/settings.json', *SHELL_FILES)


class CleanupError(Exception):
    """A safe-to-display reason to stop; never raw command output or file contents."""


def require(condition, message):
    if not condition:
        raise CleanupError(message)


def read_object(path):
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        raise CleanupError('Invalid JSON; preserve and repair the original file: ' + str(path)) from None
    require(isinstance(data, dict), 'Expected JSON object: ' + str(path))
    return data


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def manifest(path):
    """Hash files; record symlinks without following them, including broken links."""
    if not path.exists() and not path.is_symlink():
        return {}
    paths = [path]
    if path.is_dir() and not path.is_symlink():
        paths += sorted(path.rglob('*'))
    result = {}
    for p in paths:
        key = str(p.relative_to(path))
        mode = p.lstat().st_mode
        if stat.S_ISLNK(mode):
            result[key] = {'link': os.readlink(p)}
        elif stat.S_ISREG(mode):
            result[key] = {'sha256': digest(p)}
        elif stat.S_ISDIR(mode):
            result[key] = {'directory': True}
        else:
            raise CleanupError('Unsupported special file; stop the owning process first: ' + str(p))
    return result


def plain_path(path, home):
    """Reject linked mutation paths and their ancestors, even absent final paths."""
    for p in [path, *path.parents]:
        if p == home:
            break
        require(not p.is_symlink(), 'Linked cleanup/config path requires manual review: ' + str(p))


def command(args, home):
    try:
        return subprocess.run(args, cwd=home, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise CleanupError('Command unavailable or timed out: ' + Path(args[0]).name) from None


def active_processes(home):
    response = command(['ps', '-axo', 'pid=,comm=,args='], home)
    require(response.returncode == 0, 'Cannot inspect running processes')
    pids = []
    for line in response.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2:
            name = parts[1]
            if (Path(name).name == 'claude' or '/resources/native-binary/claude' in name
                    or '/.local/share/claude/versions/' in name
                    or (Path(name).name == 'node' and len(parts) == 3
                        and '@anthropic-ai/claude-code/' in parts[2])):
                pids.append(parts[0])
    return pids


def targets(home):
    candidates = [home / name for name in CACHES]
    candidates += sorted((home / '.claude/backups').glob('.claude.json.backup.*'))
    return [p for p in candidates if p.exists() or p.is_symlink()]


def inspect(home):
    require(platform.system() == 'Darwin', 'This helper supports macOS only')
    for rel in ('.claude', '.claude.json', '.claude/backups', '.claude/.credentials.json',
                '.claude/settings.json', '.claude/settings.local.json', '.claude-cleanup-backups', *CACHES):
        plain_path(home / rel, home)
    require((home / '.claude').is_dir() and (home / '.claude.json').is_file(),
            'Default Claude profile not found; do not create or guess a profile')
    identity = read_object(home / '.claude.json')
    blockers = []
    keys = sorted(k for k in AUTH_KEYS | {'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME'} if os.environ.get(k))
    if keys:
        blockers.append({'source': 'process environment', 'keys': keys})
    for rel in ('.claude/settings.json', '.claude/settings.local.json'):
        p = home / rel
        if not p.exists():
            continue
        settings = read_object(p)
        env = settings.get('env', {})
        require(isinstance(env, dict), 'Expected settings.env object: ' + str(p))
        keys = sorted(k for k in AUTH_KEYS if env.get(k))
        if settings.get('apiKeyHelper'):
            keys.append('apiKeyHelper')
        if keys:
            blockers.append({'source': rel, 'keys': keys})
    for rel in SHELL_FILES:
        p = home / rel
        if p.exists():
            text = '\n'.join(line for line in p.read_text().splitlines() if not line.lstrip().startswith('#'))
            keys = sorted(k for k in AUTH_KEYS | {'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME'}
                          if re.search(r'\b' + re.escape(k) + r'\b', text))
            if keys:
                blockers.append({'source': rel, 'keys': keys})
    pids = active_processes(home)
    if pids:
        blockers.append({'source': 'running Claude processes', 'pids': pids})
    for p in targets(home):
        plain_path(p, home)
    return {'mode': 'preview', 'blockers': blockers,
            'remove_fields_present': sorted(REMOVE & identity.keys()), 'reset_fields': RESET,
            'quarantine_candidates': [str(p.relative_to(home)) for p in targets(home)],
            'backup_root': str(home / '.claude-cleanup-backups'),
            'scope': 'default macOS CLI profile; network and other apps unchanged'}


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)


@contextmanager
def parent_handle(path, home):
    """Anchor every ancestor without following links; rename through the held fd."""
    with ExitStack() as stack:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for part in path.parent.relative_to(home).parts:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def reset_identity(path, displaced):
    before_bytes = path.read_bytes()
    before = read_object(path)
    require('oauthAccount' not in before, 'Official logout left oauthAccount; stop before resetting')
    after = {k: v for k, v in before.items() if k not in REMOVE}
    after.update(RESET)
    fd, temp = tempfile.mkstemp(prefix='.claude-cleanup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(after, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        with parent_handle(path, path.parent) as source_fd, parent_handle(displaced, path.parent) as backup_fd:
            # Keep the exact displaced inode, including writes made immediately before rename.
            os.rename(path.name, displaced.name, src_dir_fd=source_fd, dst_dir_fd=backup_fd)
            try:
                require(not displaced.is_symlink() and displaced.read_bytes() == before_bytes,
                        'Concurrent identity modification; displaced version preserved')
                # No-clobber publication: a newly created identity file is never overwritten.
                os.link(Path(temp).name, path.name, src_dir_fd=source_fd, dst_dir_fd=source_fd,
                        follow_symlinks=False)
            except Exception:
                try:
                    os.link(displaced.name, path.name, src_dir_fd=backup_fd, dst_dir_fd=source_fd,
                            follow_symlinks=False)
                except FileExistsError:
                    pass  # Keep both the new live file and the displaced version for review.
                raise
        return after
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def clean(home, apply=False):
    home = Path(home).resolve()
    preview = inspect(home)
    if not apply:
        return preview
    require(not preview['blockers'], 'Resolve preview blockers within the approved scope before applying')
    cli = shutil.which('claude')
    require(cli is not None, 'Claude CLI missing; inspect installation separately')
    root = home / '.claude-cleanup-backups'
    root.mkdir(mode=0o700, exist_ok=True)
    require(root.stat().st_mode & 0o077 == 0, 'Backup root must be private (mode 700)')
    with os.fdopen(os.open(root / '.cleanup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CleanupError('Another cleanup is running') from None
        return apply_cleanup(home, preview, cli, root)


def apply_cleanup(home, preview, cli, root):
    run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=root))
    stage = 'backup'
    try:
        backup = run / 'backup'
        backup.mkdir(mode=0o700)
        before = manifest(home / '.claude')
        identity = read_object(home / '.claude.json')
        identity_digest = digest(home / '.claude.json')
        outside = {name: manifest(home / name) for name in EXTERNAL}
        shutil.copytree(home / '.claude', backup / 'dot-claude', symlinks=True)
        shutil.copy2(home / '.claude.json', backup / 'claude.json')
        require(before == manifest(backup / 'dot-claude') == manifest(home / '.claude'),
                'Backup mismatch or concurrent profile modification')
        require(identity_digest == digest(backup / 'claude.json') == digest(home / '.claude.json'),
                'Identity backup mismatch or concurrent modification')
        save(run / 'manifest-before.json', before)
        save(run / 'external-before.json', outside)
        save(run / 'preview.json', preview)
        require(not inspect(home)['blockers'], 'Preconditions changed during backup')
        stage = 'official logout'
        require(command([cli, 'auth', 'logout'], home).returncode == 0, 'Official logout failed')
        auth = command([cli, 'auth', 'status'], home)
        try:
            status = json.loads(auth.stdout)
        except ValueError:
            raise CleanupError('Auth status returned invalid JSON') from None
        require(isinstance(status, dict) and auth.returncode == 1 and status.get('loggedIn') is False
                and status.get('authMethod') == 'none', 'CLI did not confirm an unauthenticated state')
        require(isinstance(status.get('configDirectory'), str)
                and Path(status['configDirectory']).resolve() == home / '.claude',
                'CLI configuration directory differs or is unavailable; stop')
        keychain = command(['security', 'find-generic-password', '-s', 'Claude Code-credentials'], home)
        require(keychain.returncode == 44, 'Keychain item remains or lookup failed; stop')
        require(not (home / '.claude/.credentials.json').exists(), 'Credential file remains; stop')
        require(not active_processes(home), 'Claude restarted during cleanup; stop')
        stage = 'reset and quarantine'
        plain_path(home / '.claude.json', home)
        displaced = backup / 'identity-before-reset.json'
        reset_before_hash = digest(home / '.claude.json')
        expected = reset_identity(home / '.claude.json', displaced)
        quarantine = run / 'quarantine'
        quarantine.mkdir(mode=0o700)
        moved = []
        # Freeze the list and hold no-follow parent descriptors before any move.
        with ExitStack() as stack:
            planned = []
            for source in targets(home):
                plain_path(source, home)
                parent_fd = stack.enter_context(parent_handle(source, home))
                entry = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
                require(not stat.S_ISLNK(entry.st_mode), 'Cleanup target became a symlink')
                planned.append((source, parent_fd, (entry.st_dev, entry.st_ino), manifest(source)))
            destination_fd = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, destination_fd)
            for index, (source, parent_fd, inode, source_manifest) in enumerate(planned):
                plain_path(source, home)
                entry = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
                require(inode == (entry.st_dev, entry.st_ino), 'Cleanup target changed during execution')
                destination = quarantine / ('%02d-' % index + source.name)
                os.rename(source.name, destination.name, src_dir_fd=parent_fd, dst_dir_fd=destination_fd)
                moved.append({'source': str(source.relative_to(home)), 'destination': destination.name})
                save(run / 'quarantined.json', moved)
                require(not source.exists() and source_manifest == manifest(destination), 'Quarantine mismatch')
        stage = 'verification'
        after = manifest(home / '.claude')
        excluded = [Path(x['source']).relative_to('.claude').as_posix()
                    for x in moved if x['source'].startswith('.claude/')]
        def retained(name):
            return name != '.credentials.json' and not any(name == x or name.startswith(x + '/') for x in excluded)
        differences = [name for name, value in before.items() if retained(name) and after.get(name) != value]
        save(run / 'manifest-after.json', after)
        require(not differences, 'Protected Claude data changed; inspect private verification manifests')
        require(outside == {name: manifest(home / name) for name in EXTERNAL}, 'Protected external config changed')
        require(read_object(home / '.claude.json') == expected, 'Identity changed during verification')
        require(digest(displaced) == reset_before_hash, 'Concurrent writes to displaced identity; review preserved version')
        for key, value in identity.items():
            if key not in REMOVE and key not in RESET:
                require(expected.get(key) == value, 'Unrelated identity setting changed: ' + key)
        require(not list((home / '.claude/backups').glob('.claude.json.backup.*')), 'Recovery backups reappeared')
        require(not inspect(home)['blockers'], 'Authentication/configuration was reintroduced during cleanup')
        credential = home / '.claude/.credentials.json'
        require(not credential.exists() and not credential.is_symlink(), 'Credential file reappeared during cleanup')
        require(command(['security', 'find-generic-password', '-s', 'Claude Code-credentials'], home).returncode == 44,
                'Credential state changed during verification')
        result = {'mode': 'applied', 'verified': True, 'record': str(run),
                  'preserved_entries': sum(retained(k) for k in before), 'quarantined': moved,
                  'logged_in': False, 'keychain_absent': True, 'network_tested': False,
                  'login_tested': False, 'installation_modified': False}
        save(run / 'result.json', result)
        return result
    except Exception as error:
        # Never save exception/CLI text: it can contain credentials. No blind rollback of auth.
        save(run / 'failure.json', {'stage': stage, 'exception_type': type(error).__name__, 'verified': False})
        raise CleanupError('Stopped at ' + stage + '; preserve backup and inspect: ' + str(run)) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Execute after user authorization; default is read-only preview')
    args = parser.parse_args()
    try:
        print(json.dumps(clean(Path.home(), args.apply), ensure_ascii=False, indent=2))
    except (CleanupError, OSError) as error:
        # OSError path/error text can be sensitive; show only its class.
        message = str(error) if isinstance(error, CleanupError) else type(error).__name__
        print(json.dumps({'error': message, 'verified': False}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
