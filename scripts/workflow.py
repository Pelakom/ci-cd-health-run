#!/usr/bin/env python3
"""Plan secret-free target jobs and combine their report artifacts."""
import argparse
import json
from pathlib import Path
import re

import yaml
from health import ConfigError, targets, write_report

FIELDS = ('privatekey', 'password', 'known_hosts', 'github_token')


def plan(config_path):
    config = yaml.safe_load(config_path.read_text())
    if not isinstance(config, dict):
        raise ConfigError('Config must be a mapping')
    entries = []
    for index, (kind, label, target, account) in enumerate(targets(config)):
        references = {}
        for field in FIELDS:
            value = (account if field == 'github_token' and kind == 'github-codespace' else target).get(field)
            source, name = '', ''
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(r'(secret|env|var):[A-Za-z_][A-Za-z0-9_]*', value):
                    raise ConfigError('Credentials must use secret:NAME, env:NAME, or var:NAME')
                source, name = value.split(':', 1)
            references[field] = {'source': source, 'name': name}
        entries.append({'id': index, 'type': kind, 'server': label, 'references': references})
    if len(entries) > 256:
        raise ConfigError('GitHub Actions supports at most 256 target matrix jobs')
    return entries


def merge(plan_path, artifacts, output):
    results = []
    for entry in json.loads(plan_path.read_text()):
        path = artifacts / f"target-{entry['id']}" / 'health.json'
        try:
            report = json.loads(path.read_text())
            if len(report['results']) != 1:
                raise ValueError('Expected one target result')
            result = report['results'][0]
            if result['type'] != entry['type'] or result['server'] != entry['server']:
                raise ValueError('Target mismatch')
            if result['status'] not in ('ok', 'error'):
                raise ValueError('Invalid status')
        except (OSError, ValueError, KeyError, TypeError):
            result = {'type': entry['type'], 'server': entry['server'], 'status': 'error',
                      'error': 'Target job did not produce a valid report; inspect its Actions logs'}
        results.append(result)
    return write_report(results, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['plan', 'merge'])
    parser.add_argument('--config', type=Path, default=Path('servers.yaml'))
    parser.add_argument('--plan', type=Path, default=Path('plan.json'))
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts'))
    parser.add_argument('--output', type=Path, default=Path('reports'))
    args = parser.parse_args()
    if args.operation == 'plan':
        entries = plan(args.config)
        args.plan.write_text(json.dumps(entries))
        # A sentinel prevents an invalid empty Actions matrix.
        matrix = entries or [{'id': -1, 'references': {}}]
        print('matrix=' + json.dumps({'include': matrix}, separators=(',', ':')))
    else:
        merge(args.plan, args.artifacts, args.output)
