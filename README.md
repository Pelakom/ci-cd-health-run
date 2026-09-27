# CI/CD Health Run

A fully open-sourced workflow-based self-hosted health checker for CI/CD runners, with reports published to GitHub Releases.

CI/CD Health Run turns a GitHub repository into a configurable home for scheduled infrastructure checks. Define Linux targets in YAML, keep credentials in repository secrets, and let GitHub Actions connect to each target, collect a health snapshot, and publish a report. The collector, remote probe, and publishing workflow are all included in this repository and can be inspected and adapted to your environment.

Use it to check the machines behind your build pipelines, inspect development environments across multiple GitHub accounts, or keep a release-backed record of server availability and resource usage. It runs a short probe over SSH without installing a persistent monitoring agent on the target.

## What it does

- Checks Linux servers over SSH using a private key, a password, or both.
- Connects to named GitHub Codespaces using a separate token for each account.
- Collects uptime, CPU usage, memory usage, and the five live processes with the highest CPU usage.
- Runs manually through `workflow_dispatch` or hourly with `34 * * * *`.
- Publishes Markdown and JSON reports to the same repository's GitHub Releases.
- Records individual target failures and continues checking the remaining targets.
- Supports custom secret names, environment variables, and a replaceable remote probe.

The default workflow executes in an Alpine container on a GitHub-hosted Ubuntu runner. The **targets** may be self-hosted CI/CD runner machines, ordinary Linux servers, or Codespaces. Checking a runner machine measures its operating-system health; it does not query runner registration, job queues, or CI job results.

## How it works

```text
Manual trigger / hourly schedule
              |
              v
GitHub Actions -> Alpine collector -> servers.yaml + repository secrets
                         |
                         +-- SSH -> Linux servers / CI runner machines
                         |
                         +-- GitHub CLI SSH -> Codespaces across accounts
                         |
                         v
                 Per-target health results
                         |
                         v
         GitHub Releases + Actions job summary
              health.md and health.json
```

Targets are checked sequentially. Once collection completes, the workflow publishes the report and marks the run as failed if any target failed. A successful result means the connection and probe completed; the default probe does not apply CPU or memory alert thresholds.

## Quick start

### 1. Prepare your repository

Fork this repository or copy its files into your own repository, then enable GitHub Actions. Keep the workflow on the default branch for scheduled runs and manual workflow discovery.

Use [`servers.yaml.example`](servers.yaml.example) as a starting point for `servers.yaml`. If you already have a configured file, edit it instead of replacing it.

```sh
cp servers.yaml.example servers.yaml
```

All targets in the example are disabled. Replace the placeholders and set `enabled: true` for the targets you want to check.

### 2. Define targets

```yaml
command: sh -s
probe: scripts/health.sh
timeout_seconds: 180

servers:
  ssh:
    build-runner:
      enabled: true
      user: runner
      host: runner.example.com
      port: 22
      privatekey: secret:MY_RUNNER_KEY
      known_hosts: secret:MY_RUNNER_KNOWN_HOSTS

    staging-server:
      enabled: true
      user: monitor
      host: staging.example.com
      password: secret:MY_STAGING_PASSWORD
      known_hosts: secret:MY_STAGING_KNOWN_HOSTS

  github-codespace:
    personal-account:
      github_token: secret:MY_CODESPACES_TOKEN
      development:
        enabled: true
        name: curly-space-train-767qr77vg562pvv9

    second-account:
      github_token: secret:SECOND_ACCOUNT_TOKEN
      development:
        enabled: false
        name: replace-with-another-codespace-name
```

Server labels, account labels, Codespace labels, and secret names are yours to choose. Add more entries under `ssh` or more accounts and Codespaces under `github-codespace`. Account entries contain `github_token` and directly nested Codespace entries, as shown above.

### 3. Add repository secrets

Open **Settings → Secrets and variables → Actions → New repository secret** in your repository. Create the secrets referenced by your enabled targets:

| Example secret name | Value to store |
| --- | --- |
| `MY_RUNNER_KEY` | The complete SSH private key, including its BEGIN and END lines |
| `MY_RUNNER_KNOWN_HOSTS` | Verified OpenSSH known_hosts entries for the runner |
| `MY_STAGING_PASSWORD` | The SSH login password |
| `MY_STAGING_KNOWN_HOSTS` | Verified OpenSSH known_hosts entries for the staging server |
| `MY_CODESPACES_TOKEN` | A GitHub token belonging to the account that owns the Codespace |
| `SECOND_ACCOUNT_TOKEN` | A token for the second account, when its targets are enabled |

For `secret:MY_RUNNER_KEY`, the repository secret name is **`MY_RUNNER_KEY`**, without the `secret:` prefix. Match the name in YAML to the name you create in GitHub. You can change these names without editing the workflow.

#### SSH authentication

For key authentication, install the matching public key in the target user's `~/.ssh/authorized_keys`. The collector expects an unencrypted private key without a passphrase. Root access is not required for the default probe.

For password authentication, the target SSH server must permit password login. Both `privatekey` and `password` may be configured when you need key authentication with a password fallback.

Host-key checking is mandatory. Store a verified entry from your OpenSSH `known_hosts` file in the referenced secret. Verify the fingerprint through a trusted server access channel before accepting the entry. Typical entry formats are:

```text
runner.example.com ssh-ed25519 <server-public-host-key>
[runner.example.com]:2222 ssh-ed25519 <server-public-host-key>
```

Use the second form for a nonstandard port, and configure the matching `port` in YAML. This is the server's public host key, separate from the private key used to log in.

#### Codespaces authentication

Use a token owned by the account that owns the configured Codespace. A classic personal access token with the `codespace` scope is a straightforward option, subject to your organization's access policies. Use the actual Codespace name, rather than its display name or repository name.

The workflow's built-in `GITHUB_TOKEN` handles source download and release publication. It is separate from the personal tokens used to access Codespaces.

Codespaces must have an SSH server installed. See the [GitHub CLI SSH documentation](https://cli.github.com/manual/gh_codespace_ssh) for the supported devcontainer SSH feature.

### 4. Check target requirements

The default remote probe requires Linux with:

- `/proc/uptime`, `/proc/meminfo`, and `/proc/stat`.
- A POSIX shell, `awk`, and `sleep`.
- `ps` from procps, with support for CPU sorting.

Install `procps` on targets where it is missing. Ordinary SSH targets must be reachable from the workflow runner. For targets on a private network, provide runner network access or adapt `runs-on` to an appropriate Linux self-hosted runner with Docker support for the Alpine job container.

### 5. Run a check

Commit your configuration to GitHub, then open:

**Actions → CI/CD Runner Health Report → Run workflow → select branch → Run workflow**

The workflow display name is **CI/CD Runner Health Report**. Manual execution is enabled through `workflow_dispatch`; no source change is needed to trigger another check.

The schedule is:

```yaml
schedule:
  - cron: '34 * * * *'
```

It runs hourly at minute 34 in UTC, which is also minute 34 each hour in WIB. Scheduled runs use the default branch and may be delayed by GitHub Actions scheduling. See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

### 6. Read the report

Open your repository's **Releases** page. Each completed publication creates a release named:

```text
health-<run-id>-<attempt>
```

The release includes the report in its notes and attaches:

| File | Contents |
| --- | --- |
| `health.md` | A readable report containing each target's status and probe output |
| `health.json` | Generation timestamp, failure count, and per-target status with text output or an error |

The JSON report stores probe output as text; individual CPU and memory measurements are not separate structured fields. The report also appears in the Actions job summary.

Releases point to the checked commit and are not marked as the latest release. The workflow requests `contents: write` for publication. Repository or organization policies must allow this permission.

## Configuration reference

| Field | Purpose | Default / requirement |
| --- | --- | --- |
| `command` | Shell command executed on each remote target | `sh -s` |
| `probe` | Local script sent to the remote command over stdin; relative to the YAML file | `scripts/health.sh` |
| `timeout_seconds` | Maximum local connection/probe duration per target | `180`, allowed range `1–600` |
| `enabled` | Whether a target is checked | `true` if omitted; example targets explicitly use `false` |
| `user`, `host` | SSH login user and hostname/IP address | Required for SSH |
| `port` | SSH port | `22` |
| `privatekey`, `password` | Credential references for SSH authentication | At least one required |
| `known_hosts` | Reference to verified server host-key entries | Required for SSH |
| `github_token` | Credential reference at the Codespaces account level | Required for enabled Codespaces |
| `name` | Actual Codespace name | Required for each enabled Codespace |

### Credential reference types

| Syntax | Where the value comes from |
| --- | --- |
| `secret:NAME` | Repository Actions secrets supplied through `HEALTH_SECRETS_JSON` |
| `env:NAME` | The collector process's environment |
| `var:NAME` | Actions variables supplied through `HEALTH_VARS_JSON` |

Use secrets for tokens, passwords, and private keys. Environment references are useful for local runs. A repository secret does not automatically become an environment variable. To use `env:MY_TOKEN` in Actions, explicitly map it in the **Collect health** step:

```yaml
env:
  HEALTH_SECRETS_JSON: ${{ toJSON(secrets) }}
  HEALTH_VARS_JSON: ${{ toJSON(vars) }}
  MY_TOKEN: ${{ secrets.MY_TOKEN }}
```

The collector receives the secrets bundle to support user-defined secret names. It passes only the selected account token or SSH password to the relevant child process, and writes private keys to temporary files with restricted permissions. Treat the workflow, YAML command, and probe as trusted executable configuration.

## Understanding the measurements

| Measurement | Meaning |
| --- | --- |
| Uptime | Seconds and days from `/proc/uptime` |
| Memory | `MemTotal - MemAvailable`, reported in MiB and percent |
| CPU | Aggregate CPU activity measured over approximately one second from `/proc/stat`; iowait counts as idle |
| Top five processes | Live processes sorted by `ps` CPU percentage, including sleeping processes; PID, executable name, CPU%, and MEM% |

Process CPU percentage is the `ps` lifetime average, distinct from the system's one-second CPU sample. Command-line arguments are excluded from the process listing.

Inside containers and Codespaces, uptime, CPU, and memory reflect the host view exposed through `/proc`, rather than container cgroup limits. The process list follows the visible PID namespace.

## Custom probes and local use

Replace `scripts/health.sh` or point `probe` at another script to collect additional information. Keep `command: sh -s` for shell scripts, or provide another remote command that consumes your probe over stdin. The command and probe are shared by all configured targets. A custom probe must emit its own report content and return a nonzero exit code on failure.

Codespace execution is equivalent to:

```sh
GH_TOKEN="$ACCOUNT_TOKEN" gh cs ssh -c "$CODESPACE_NAME" -- -T -o BatchMode=yes 'sh -s' < scripts/health.sh
```

For local collection, install Python 3, PyYAML, GitHub CLI, OpenSSH client, and `sshpass` if you use SSH passwords. Use `env:` credential references with values supplied in your shell environment, then run:

```sh
python3 scripts/health.py --config servers.yaml --output reports
```

This command contacts enabled targets and writes local reports. Release publication is performed separately by the GitHub Actions workflow. The collector records target failures in the JSON `failed` count; the workflow checks that count after publishing to determine the final run status.

To validate the collector and run the default probe on your local Linux machine:

```sh
python3 -m unittest discover -s tests -v
sh scripts/health.sh
```

The test suite uses local execution and mocked remote connections. Files in `reports/`, Python caches, and `.env` are ignored by Git. `.env` files are not loaded automatically.

## Operational behavior and troubleshooting

| Symptom | What to check |
| --- | --- |
| No enabled targets in the report | Set `enabled: true` on the targets you want to inspect |
| Missing credential reference | Match the YAML reference to a repository secret or an explicitly supplied environment variable |
| SSH connection fails | Check reachability, port, login permissions, verified known_hosts entries, and required target tools |
| Codespace connection fails | Check the token's account/access, actual Codespace name, SSH server availability, and target tools |
| Release publication fails | Check the publish step logs and effective `contents: write` permission |
| Workflow is red but a report exists | One or more targets failed; inspect the per-target entries in the release |
| Manual trigger is unavailable | Ensure Actions is enabled and the workflow exists on the default branch |

Per-target failures do not stop the remaining checks. Connection diagnostics are summarized rather than publishing raw SSH/GH stderr. If the entire job is cancelled, exceeds its 45-minute limit, or fails before report generation, a release may not be created. For larger inventories, account for sequential execution when adjusting the job timeout.

Reports inherit repository visibility and include server labels and process names. Known credential values are redacted, but custom probe output should be chosen with the report audience in mind. Each run creates a new release; automatic retention cleanup is not implemented.

Checks capture health at the time of execution. The hourly schedule does not provide continuous monitoring or guarantee target availability. Codespaces remain subject to their idle timeout. See the [Codespaces lifecycle documentation](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle).

## Repository layout

```text
.github/workflows/health.yml  Scheduling, Alpine setup, collection, and publication
servers.yaml                 Active target configuration
servers.yaml.example         Editable configuration template
scripts/health.py             Credential resolution, connections, and report generation
scripts/health.sh             Default Linux health probe
tests/test_health.py          Collector and probe validation
```
