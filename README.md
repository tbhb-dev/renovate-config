# renovate-config

Shared self-hosted Renovate setup for the tbhb-dev repositories: a reusable
workflow that runs Renovate for the calling repo, a reusable validator, and
four presets.

## Contents

| Path | Purpose |
|---|---|
| `.github/workflows/renovate.yaml` | Reusable (`workflow_call`) Renovate run for the caller's repo. Global settings live in its `env`. |
| `.github/workflows/renovate-config-validator.yaml` | Reusable strict validation plus a preset-reference resolution check. |
| `.github/workflows/ci.yaml` | This repo's gate: actionlint, zizmor, ryl, REUSE, preset validation, and the `conclusion` job. |
| `.github/workflows/renovate-self.yaml` | Renovate for this repo, through its own reusable workflow. |
| `default.json5` | Every repo: best-practices, 5-day cooldown, ignore paths, grouping, Vale and preset-pin managers, `platformCommit`. |
| `bazel.json5` | Bazel repos: `bazel` manager on `MODULE.bazel` and `*.bzl`, dugite datasource, Bazel version group with its one post-upgrade task. |
| `go.json5` | Go repos: `gomodTidy`, module and toolchain groups. |
| `python.json5` | uv projects: `pyproject.toml`, `uv.lock`, the uv version pin. |

## Using it from a repo

Add `.github/renovate.json5`, a caller `.github/workflows/renovate.yaml` and a
validator caller. Pin the presets and workflows by full commit SHA with a
comment naming what the SHA tracks: `main`, which Renovate bumps to the
branch's latest commit, or a release tag such as `v1.0.0`:

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

The `.json5` suffix in a preset reference is required.

## Security model

- Only `bazelModDeps` is allowed in `allowedUnsafeExecutions`. `mise` is not,
  so mise stays in safe mode.
- One post-upgrade command, `bazelisk run //:preset.update`, matched by an
  anchored regex in the workflow env and attached only to the Bazel version
  group.
- Both allowlists sit in workflow env, which overrides config files, so a
  repo PR cannot widen them.
- The App token is minted per run, scoped to the calling repo and downscoped
  to the permissions Renovate needs.
- Nothing that ran a command is automerged.
- Commits go through the GitHub API (`platformCommit`), so GitHub signs them.
