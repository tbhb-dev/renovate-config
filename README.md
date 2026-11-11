# renovate-config

Shared self-hosted Renovate setup for the tbhb-dev repositories. It holds
five presets and two reusable workflows: one runs Renovate for the calling
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
| `.github/workflows/renovate-config-validator.yaml` | Reusable strict validation plus a preset-reference resolution check. |
| `.github/workflows/ci.yml` | This repo's gate: `bazel test //...` over the tree, rules' shared repository checks, preset validation, and the `conclusion` job. |
| `.github/workflows/renovate-self.yaml` | Renovate for this repo, through its own reusable workflow. |
| `default.json5` | Every repo: best-practices, 5-day cooldown, ignore paths, grouping, Vale and preset-pin managers, `platformCommit`, and PR titles that fit the 72-character squash subject. |
| `bazel.json5` | Bazel repos: `bazel` manager on `MODULE.bazel` and `*.bzl`, dugite datasource, Bazel version group with its one post-upgrade task. |
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

A preset reference must include the `.json5` suffix.

Renovate PRs auto-merge after the main ruleset's required approval and CI
checks pass. Consumer repos must enable "Allow auto-merge" in GitHub
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

## Security model

- `allowedUnsafeExecutions` allows only `bazelModDeps`. It leaves out `mise`,
  so mise stays in safe mode.
- Two post-upgrade commands, each matched by an anchored regular expression
  in the workflow `env`. `bazel.json5` attaches `bazelisk run //:preset.update`
  to the Bazel version group alone. `ansible.json5` attaches
  `bazelisk run @tbhb_rules_ansible//:lock` to `ansible-galaxy` bumps alone.
  The lock target reads Galaxy version metadata and writes
  `requirements.lock.json`, without downloading any collection.
- Both allowlists live in the workflow `env`. They're global-only options,
  which Renovate ignores in a repo's config, so a repo PR can't widen them.
- Each run creates its own App token, limited to the calling repo, and
  downscoped to the permissions Renovate needs.
- The reviews App approves updates that ran commands only when the diff
  touches manifests and lock files, plus the generated file named in
  `fileFilters` for post-upgrade tasks. This covers post-upgrade tasks,
  `bazel mod deps`, and lock maintenance. These PRs auto-merge after the
  required review and CI pass. Lock maintenance runs weekly on Monday from
  00:00 to 03:59 Coordinated Universal Time (UTC). The reviews App approves
  it only when every newly locked version is at least 5 days old.
- Commits go through the GitHub API (`platformCommit`), so GitHub signs them.
