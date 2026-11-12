# renovate-config

Shared self-hosted Renovate setup for the tbhb-dev repositories. It holds
six presets and two reusable workflows: one runs Renovate for the calling
repo, and the other validates that repo's Renovate configuration.

The Renovate workflow checks its JSON file log after each run, failing on
missing, empty, invalid, or incomplete logs. Error or fatal entries and the
known failures `Error updating branch`, `artifactErrors`, and
`Failed to update MODULE.bazel.lock` also fail the job. The workflow defines
this allowlist. Annotations show only a fixed label and a validated branch.

## Contents

| Path | Purpose |
|---|---|
| `.github/workflows/renovate.yaml` | Reusable (`workflow_call`) Renovate run for the caller's repo. Global settings live in its `env`. |
| `.github/workflows/renovate-config-validator.yaml` | Reusable strict validation, preset-reference resolution, consistent consumer pins, and PR pin-range summaries. |
| `.github/workflows/ci.yml` | This repo's gate: `bazel test //...` over the tree, rules' shared repository checks, preset validation, and the `conclusion` job. |
| `.github/workflows/renovate-self.yaml` | Renovate for this repo, through its own reusable workflow. |
| `default.json5` | Every repo: best-practices, 5-day cooldown, ignore paths, grouping, Vale and preset-pin managers, `platformCommit`, and PR titles that fit the 72-character squash subject. |
| `bazel.json5` | Bazel repos: `bazel` manager on `MODULE.bazel` and `*.bzl`, dugite datasource, Bazel version group with its one post-upgrade task. |
| `cloudflare-worker.json5` | Worker repos: a `tbhb_rules` bump regenerates `worker-configuration.d.ts` in the same branch. |
| `go.json5` | Go repos: `gomodTidy` plus module and toolchain groups. |
| `python.json5` | uv projects: `pyproject.toml`, `uv.lock`, the uv version pin. |
| `ansible.json5` | Repos that pin Galaxy collections with `tbhb_rules_ansible`. A collection bump rewrites `requirements.lock.json` in the same branch. |

## Using it from a repo

Add `.github/renovate.json5`, a caller `.github/workflows/renovate.yaml` and a
validator caller. Pin the presets and workflows to a full commit hash, with a
trailing comment that states the ref it tracks: `main`, which Renovate bumps
to the branch's latest commit, or a release tag such as `v1.0.0`:

```json5
{
  extends: [
    "github>tbhb-dev/renovate-config//default.json5#<sha>", // main
    "github>tbhb-dev/renovate-config//bazel.json5#<sha>", // main
  ],
}
```

```yaml
jobs:
  renovate:
    uses: tbhb-dev/renovate-config/.github/workflows/renovate.yaml@<sha> # main
    secrets:
      RENOVATE_CLIENT_ID: ${{ secrets.RENOVATE_CLIENT_ID }}
      RENOVATE_PRIVATE_KEY: ${{ secrets.RENOVATE_PRIVATE_KEY }}
```

The validator caller runs on PRs that change the Renovate config, the
workflows, or a `tbhb_rules` pin. The pin checks then see every bump:

```yaml
on:
  pull_request:
    paths:
      - ".github/renovate.json5"
      - ".github/workflows/*.yaml"
      - ".github/workflows/*.yml"
      - "MODULE.bazel"
      - "**/*.bzl"

jobs:
  validate:
    uses: tbhb-dev/renovate-config/.github/workflows/renovate-config-validator.yaml@<sha> # main
    with:
      configs: .github/renovate.json5
```

A preset reference must include the `.json5` suffix.

The validator requires
all `tbhb-dev/renovate-config` preset and reusable workflow pins in a consumer
to use the same full 40-character commit hash. Repositories without these
references pass the check. Renovate keeps them in step: every
`tbhb-dev/renovate-config` reference, preset or workflow, joins the
`pins` group in `default.json5`, so one PR moves them all.

The `pins` group opens one PR per repository a day, between 05:00 and 08:00
Coordinated Universal Time. It takes the `renovate-config` and `tbhb_rules`
pins, plus minor, patch, and digest updates to GitHub Actions and mise tools.
Renovate opens a separate PR for each major version and each security fix.
The Bazel version group uses the same window but stays a separate PR, and so
do `tbhb_rules` bumps in Worker repos, because each runs its own post-upgrade
task. One failing pin blocks the rest of the group. To exclude one, add a rule to the
repository's `.github/renovate.json5`:

```json5
{
  packageRules: [
    { matchPackageNames: ["<package>"], groupName: "<package>" },
  ],
}
```

Worker repos add `github>tbhb-dev/renovate-config//cloudflare-worker.json5#<sha>`
after `bazel.json5` in their `extends` list.

Renovate PRs auto-merge after the main ruleset's required approval and CI
checks pass. Before Renovate starts, the workflow checks active rules on
`main` using the Renovate App token. It requires at least one approving
review and a status-check rule. Every applied ruleset must report that the
App can never bypass it. Only the drafts repositories pass
`require_approval: false`, because their PRs merge on green CI alone. Their
check keeps the status-check and bypass parts. Missing protection or a failed API check stops
the run. Consumer repos must enable "Allow auto-merge" in GitHub
settings for native auto-merge.

Extend `ansible.json5` only from a repository whose root `MODULE.bazel`
declares `ansible.collections`. The lock task runs at the repository root,
so it rewrites the `requirements.lock.json` that tag names. A repository
with an Ansible module root nested below its own root shouldn't extend the
preset, because the task fails there.

Both reusable workflows take a `runs_on` input, the runner label, which
defaults to `ubuntu-24.04`. Public repositories leave it out. A private
repository must pass its `tbhb-dev-*` runner label, because private jobs
never run on GitHub-hosted runners:

```yaml
    with:
      runs_on: tbhb-dev-ubuntu-24-amd64
```

## Pin-bump validation

On pull requests, the validator checks changed `tbhb-dev/renovate-config` and
`tbhb-dev/rules` pins against their upstream `main` branch. Each new pin must
use a full commit hash on that branch. The job summary lists up to 50 commit
subjects per range and the changed upstream files. New pins have no previous
range to list. Other workflow events skip this check.

## Security model

- `allowedUnsafeExecutions` allows only `bazelModDeps`. It leaves out `mise`,
  so mise stays in safe mode.
- Three post-upgrade commands, each matched by an anchored regular expression
  in the workflow `env`. `bazel.json5` attaches `bazelisk run //:preset.update`
  to the Bazel version group alone. `ansible.json5` attaches
  `bazelisk run @tbhb_rules_ansible//:lock` to `ansible-galaxy` bumps alone.
  The lock target reads Galaxy version metadata and writes
  `requirements.lock.json`, without downloading any collection.
  `cloudflare-worker.json5` attaches `bazelisk run //:worker_types_test.update`
  to `tbhb_rules` bumps in Worker repos and commits only
  `worker-configuration.d.ts` through `fileFilters`.
- Both allowlists live in the workflow `env`. They're global-only options,
  which Renovate ignores in a repo's config, so a repo PR can't widen them.
- Each run creates its own App token, limited to the calling repo, and
  downscoped to the permissions Renovate needs.
- The reviews App approves updates that ran commands only when the diff
  touches manifests and lock files, plus the generated file named in
  `fileFilters` for post-upgrade tasks. This covers post-upgrade tasks,
  `bazel mod deps`, and lock maintenance. These PRs auto-merge after the
  required review and CI pass. Lock maintenance also needs a human to tick
  its Dependency Dashboard box before it runs.
- Commits go through the GitHub API (`platformCommit`), so GitHub signs them.
