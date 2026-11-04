# renovate-config

Shared self-hosted Renovate setup for the tbhb-dev repositories. It holds
four presets and two reusable workflows: one runs Renovate for the calling
repo, and the other validates that repo's Renovate configuration.

## Contents

| Path | Purpose |
|---|---|
| `.github/workflows/renovate.yaml` | Reusable (`workflow_call`) Renovate run for the caller's repo. Global settings live in its `env`. |
| `.github/workflows/renovate-config-validator.yaml` | Reusable strict validation plus a preset-reference resolution check. |
| `.github/workflows/ci.yml` | This repo's gate: `bazel test //...` over the tree, rules' shared repository checks, preset validation, and the `conclusion` job. |
| `.github/workflows/renovate-self.yaml` | Renovate for this repo, through its own reusable workflow. |
| `default.json5` | Every repo: best-practices, 5-day cooldown, ignore paths, grouping, Vale and preset-pin managers, `platformCommit`. |
| `bazel.json5` | Bazel repos: `bazel` manager on `MODULE.bazel` and `*.bzl`, dugite datasource, Bazel version group with its one post-upgrade task. |
| `go.json5` | Go repos: `gomodTidy` plus module and toolchain groups. |
| `python.json5` | uv projects: `pyproject.toml`, `uv.lock`, the uv version pin. |

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

## Security model

- `allowedUnsafeExecutions` allows only `bazelModDeps`. It leaves out `mise`,
  so mise stays in safe mode.
- One post-upgrade command, `bazelisk run //:preset.update`, matched by an
  anchored regular expression in the workflow `env` and attached only to the
  Bazel version group.
- Both allowlists live in the workflow `env`, which overrides config files,
  so a repo PR can't widen them.
- Each run creates its own App token, limited to the calling repo, and
  downscoped to the permissions Renovate needs.
- Renovate never merges an update that ran a command on its own.
- Commits go through the GitHub API (`platformCommit`), so GitHub signs them.
