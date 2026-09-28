#!/usr/bin/env python3
"""Commit one Markdown report through GitHub's Contents API."""
import base64
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def request(method, url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, method=method, headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28',
    })
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def archive(report, repository, branch, run_id, attempt, token, api_url):
    if not re.fullmatch(r'[0-9]+', run_id) or not re.fullmatch(r'[0-9]+', attempt):
        raise ValueError('Run ID and attempt must be numeric')
    path = f'reports-sample/health-{run_id}-{attempt}.md'
    content = report.read_bytes()
    endpoint = f"{api_url.rstrip('/')}/repos/{repository}/contents/{path}"
    lookup = endpoint + '?ref=' + quote(branch, safe='')
    for retry in range(3):
        try:
            existing = request('GET', lookup, token)
        except HTTPError as exc:
            if exc.code != 404:
                raise
        else:
            if base64.b64decode(existing.get('content', '')) == content:
                return path
            raise ValueError('Report path already exists with different content; refusing to overwrite')
        try:
            request('PUT', endpoint, token, {
                'message': f'Add health report {run_id}-{attempt}',
                'content': base64.b64encode(content).decode(), 'branch': branch,
            })
            return path
        except HTTPError as exc:
            if exc.code not in (409, 422) or retry == 2:
                raise
            time.sleep(1)
    raise RuntimeError('Unable to commit report')


if __name__ == '__main__':
    try:
        path = archive(Path('reports/health.md'), os.environ['GITHUB_REPOSITORY'],
                       os.environ['REPORT_BRANCH'], os.environ['GITHUB_RUN_ID'],
                       os.environ['GITHUB_RUN_ATTEMPT'], os.environ['GH_TOKEN'],
                       os.environ.get('GITHUB_API_URL', 'https://api.github.com'))
        print(f'Committed report: {path}')
    except HTTPError as exc:
        raise SystemExit(f'Report commit failed (HTTP {exc.code}); check branch rules and contents: write permission') from None
