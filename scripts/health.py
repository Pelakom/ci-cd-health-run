#!/usr/bin/env python3
"""Collect Linux health over SSH or per-account GitHub Codespaces SSH."""
import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

import yaml


class ConfigError(ValueError):
    pass


class Credentials:
    def __init__(self):
        self.sources = {
            'secret': json.loads(os.environ.get('HEALTH_SECRETS_JSON', '{}')),
            'var': json.loads(os.environ.get('HEALTH_VARS_JSON', '{}')),
            'env': dict(os.environ),
        }
        # Actions supplies only references and values selected for this target.
        references = json.loads(os.environ.get('HEALTH_REFERENCES_JSON', '{}'))
        for field, reference in references.items():
            if reference['source'] in ('secret', 'var'):
                self.sources[reference['source']][reference['name']] = os.environ.get(
                    'HEALTH_' + field.upper(), '')
        self.sensitive = set()
        for value in self.sources['secret'].values():
            self.remember(value)

    def remember(self, value):
        if isinstance(value, str) and value:
            self.sensitive.add(value)
            self.sensitive.update(line for line in value.splitlines() if line)

    def resolve(self, ref):
        if not isinstance(ref, str) or not re.fullmatch(r'(secret|env|var):[A-Za-z_][A-Za-z0-9_]*', ref):
            raise ConfigError('Expected secret:NAME, env:NAME, or var:NAME')
        source, name = ref.split(':', 1)
        value = self.sources[source].get(name)
        if not isinstance(value, str) or not value:
            raise ConfigError(f'Missing credential reference: {ref}')
        self.remember(value)
        return value

    def redact(self, text):
        for value in sorted(self.sensitive, key=len, reverse=True):
            text = text.replace(value, '[REDACTED]')
        return text


def child_env():
    # Do not pass the repository secret bundle or unrelated credentials to children.
    result = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
    result.update(LC_ALL='C', GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1')
    return result


def execute(argv, env, probe, timeout):
    with subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
        try:
            stdout, stderr = proc.communicate(probe, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise TimeoutError(f'Health command timed out after {timeout} seconds') from None
        if proc.returncode:
            # Avoid publishing raw SSH/GH diagnostics that may echo credentials.
            raise RuntimeError(f'Remote command failed (exit {proc.returncode}); check connection, credentials and target tools')
        return stdout


def collect(kind, target, account, credentials, command, probe, timeout):
    env = child_env()
    with tempfile.TemporaryDirectory(prefix='health-') as directory:
        if kind == 'ssh':
            host, user = target.get('host', ''), target.get('user', '')
            if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:%_-]*', host):
                raise ConfigError('Invalid SSH host')
            if not isinstance(user, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*', user):
                raise ConfigError('Invalid SSH user')
            port = target.get('port', 22)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ConfigError('SSH port must be between 1 and 65535')
            known_hosts = Path(directory) / 'known_hosts'
            known_hosts.write_text(credentials.resolve(target.get('known_hosts')) + '\n')
            known_hosts.chmod(0o600)
            argv = ['ssh', '-F', '/dev/null', '-T', '-p', str(port),
                    '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=15',
                    '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=yes',
                    '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'IdentitiesOnly=yes']
            if target.get('privatekey'):
                key = Path(directory) / 'identity'
                key.write_text(credentials.resolve(target['privatekey']).rstrip() + '\n')
                key.chmod(0o600)
                argv += ['-i', str(key)]
            if target.get('password'):
                env['SSHPASS'] = credentials.resolve(target['password'])
                argv = ['sshpass', '-e'] + argv + ['-o', 'NumberOfPasswordPrompts=1']
            else:
                argv += ['-o', 'BatchMode=yes']
            if not target.get('privatekey') and not target.get('password'):
                raise ConfigError('SSH target requires privatekey or password')
            argv += [f'{user}@{host}', command]
        else:
            name = target.get('name', '')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', name):
                raise ConfigError('Invalid codespace name')
            env['GH_TOKEN'] = credentials.resolve(account.get('github_token'))
            argv = ['gh', 'cs', 'ssh', '-c', name, '--', '-T', '-o', 'BatchMode=yes', command]
        return execute(argv, env, probe, timeout)


def targets(config):
    servers = config.get('servers')
    if not isinstance(servers, dict) or set(servers) - {'ssh', 'github-codespace'}:
        raise ConfigError('servers must contain ssh and/or github-codespace mappings')
    for kind, entries in servers.items():
        if not isinstance(entries, dict):
            raise ConfigError(f'{kind} must be a mapping')
        for label, entry in entries.items():
            if not isinstance(entry, dict):
                raise ConfigError('Server/account must be a mapping')
            children = {label: entry} if kind == 'ssh' else {k: v for k, v in entry.items() if k != 'github_token'}
            for name, target in children.items():
                if not isinstance(target, dict) or type(target.get('enabled', True)) is not bool:
                    raise ConfigError('Target must be a mapping with boolean enabled')
                if target.get('enabled', True):
                    yield kind, str(label) if kind == 'ssh' else f'{label}/{name}', target, entry


def run(config_path, output, target_index=None):
    credentials = Credentials()
    results = []
    try:
        config = yaml.safe_load(config_path.read_text())
        if not isinstance(config, dict):
            raise ConfigError('Config must be a mapping')
        command = config.get('command', 'sh -s')
        if not isinstance(command, str) or not command.strip():
            raise ConfigError('command must be a nonempty shell command')
        timeout = config.get('timeout_seconds', 180)
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ConfigError('timeout_seconds must be between 1 and 600')
        probe = (config_path.parent / config.get('probe', 'scripts/health.sh')).read_text()
        selected = list(targets(config))
        if target_index is not None:
            selected = [selected[target_index]]
        for kind, label, target, account in selected:
            result = {'type': kind, 'server': label}
            try:
                result.update(status='ok', output=collect(kind, target, account, credentials, command, probe, timeout))
            except (ConfigError, RuntimeError, TimeoutError, OSError) as exc:
                result.update(status='error', error=str(exc))
            results.append(result)
    except (ValueError, TypeError, OSError, yaml.YAMLError) as exc:
        # YAML parse diagnostics can include source lines; don't publish them.
        results.append({'type': 'config', 'server': 'configuration', 'status': 'error',
                        'error': str(exc) if isinstance(exc, ConfigError) else 'Cannot read/parse configuration or probe file'})
    return write_report(results, output, credentials)


def write_report(results, output, credentials=None):
    credentials = credentials or Credentials()
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'failed': sum(r['status'] == 'error' for r in results), 'results': results}
    # Redact only after all referenced credentials have been resolved.
    for result in report['results']:
        for field in ('server', 'output', 'error'):
            if field in result:
                result[field] = credentials.redact(result[field])
    output.mkdir(parents=True, exist_ok=True)
    (output / 'health.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Server health report', '', f"UTC: {report['generated_at']}", '',
             f"Targets: {len(results)} | Failed: {report['failed']}", '']
    if not results:
        lines += ['No enabled targets. Edit servers.yaml to enable configured servers.', '']
    for result in report['results']:
        lines += [f"## {html.escape(result['type'])}: {html.escape(result['server'])}", '',
                  f"Status: **{result['status']}**", '', '<pre>',
                  html.escape(result.get('output', result.get('error', ''))), '</pre>', '']
    markdown = '\n'.join(lines)
    (output / 'health.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(markdown)
    print(f"Health report written: {len(results)} targets, {report['failed']} failures")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('servers.yaml'))
    parser.add_argument('--output', type=Path, default=Path('reports'))
    parser.add_argument('--target-index', type=int)
    args = parser.parse_args()
    run(args.config, args.output, args.target_index)
