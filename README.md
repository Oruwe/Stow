# dockerfile-optimizer

A container-optimization micro-agent. It parses a Dockerfile into a structured
model, audits it for caching, size and security problems, and mechanically
rewrites the ones it can fix without guessing.

Requires Python 3.10 or newer. No runtime dependencies on 3.11+; on 3.10 it pulls
in `tomli` alone, because a CI gate should not drag a dependency tree into every
build it guards.

## Install

```bash
pip install -e ".[dev]"   # provides the `stow` command plus the test toolchain
stow --help
```

Running straight from a checkout, without installing:

```bash
./stow --help             # macOS / Linux
.\stow.cmd --help         # Windows (PowerShell or cmd)
python -m app.cli --help  # any platform
```

On Windows, if `stow` or `pytest` is "not recognized" after installing, your
Python `Scripts` directory is not on `PATH`. Use `python -m app.cli` and
`python -m pytest`, or reinstall Python with **Add python.exe to PATH** ticked.

## Commands

```bash
stow analyze <PATH>              # audit; defaults to ./Dockerfile
stow analyze <PATH> --format sarif   # json (default) | text | sarif
stow analyze <PATH> --fail-on high   # low | medium | high | critical
stow analyze <PATH> --disable MISSING_HEALTHCHECK   # repeatable

stow refactor <PATH>             # print the rewrite to stdout
stow refactor <PATH> --write     # rewrite in place
stow refactor <PATH> --check     # exit non-zero if a rewrite would change it

stow rules --format text         # the rule catalog
stow version
```

Exit codes: `0` clean, `1` findings at or above the threshold, `2` usage error,
`3` unreadable input. `analyze` and `refactor --check` both drop into CI as gates.

## Rules

Fourteen rules, each with a severity. `--fail-on` decides which ones break a build
(default `medium`, so the `low` ones inform without blocking).

| Severity | Rule | Catches |
| --- | --- | --- |
| critical | `LEAST_PRIVILEGE` | Final stage runs as root, explicitly or by default |
| critical | `SECRET_IN_IMAGE` | A literal secret baked into `ENV`/`ARG` |
| high | `PINNED_VERSION` | Base image on `:latest` or with no tag/digest |
| high | `NO_SUDO` | `sudo` in a build layer |
| high | `ADD_REMOTE` | `ADD` of a URL, fetched without checksum verification |
| medium | `CACHE_CLEANUP` | apt layer that never cleans `/var/lib/apt/lists/*` |
| medium | `APT_UPDATE_ISOLATED` | `apt-get update` alone in a layer, served stale from cache |
| medium | `CACHE_ORDER` | Source copied before dependencies install |
| medium | `PIPE_WITHOUT_PIPEFAIL` | Pipeline that discards an upstream failure |
| low | `ADD_OVER_COPY` | `ADD` where `COPY` is clearer |
| low | `PIP_NO_CACHE` | `pip install` leaving its wheel cache in the layer |
| low | `WORKDIR_ABSOLUTE` | Relative `WORKDIR` |
| low | `MAINTAINER_DEPRECATED` | `MAINTAINER` instead of `LABEL` |
| low | `MISSING_HEALTHCHECK` | Final stage declares no `HEALTHCHECK` |

`LEAST_PRIVILEGE` evaluates **only the final stage**. A `USER` in a builder stage
never reaches the shipped image, so treating it as compliance is a false pass.

## Refactors

`refactor` fires only transforms it can apply mechanically. Anything needing
judgement is left in place and reported on stderr as `# skipped ->`.

| Transform | Effect |
| --- | --- |
| `APT_NO_RECOMMENDS` | Adds `--no-install-recommends` |
| `APT_CACHE_CLEANUP` | Appends list cleanup to the same `RUN`, so it stays one layer |
| `PIP_NO_CACHE` | Adds `--no-cache-dir` |
| `ADD_TO_COPY` | Rewrites `ADD` → `COPY` for plain local paths only |
| `CACHE_ORDER_HOIST` | Moves `COPY . <dest>` below the dependency install |
| `LEAST_PRIVILEGE_USER` / `_DEROOT` | Adds a non-root `USER`, or replaces `USER root` |
| `MULTISTAGE_SPLIT` | Splits a single-stage pip build into `builder` + lean runtime |

What it deliberately will not do:

- **Invent a version tag.** Which release replaces `python:latest` is a human
  call; guessing ships a build that works locally and breaks in production.
- **Rewrite a remote `ADD`.** `COPY` cannot fetch a URL, so the rewrite would
  silently break the build.
- **Split a multi-stage build it cannot rewrite faithfully.** `MULTISTAGE_SPLIT`
  fires on one `FROM` plus one `pip install -r`; other shapes are reported.

Refactoring is idempotent — running it twice yields the same file, so `--write`
does not churn diffs.

## Configuration

`.dockerfile-optimizer.toml`, or `[tool.dockerfile-optimizer]` in `pyproject.toml`,
discovered by walking up from the working directory:

```toml
disabled_rules = ["MISSING_HEALTHCHECK"]
fail_on = "high"
format = "text"
```

An invalid value is an error, not a silent fallback — a typo in `fail_on` must not
leave a team believing they have a gate they do not have.

## CI

SARIF output uploads to GitHub code scanning, putting findings inline on the diff:

```yaml
- run: stow analyze Dockerfile --format sarif > dockerfile.sarif
  continue-on-error: true
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: dockerfile.sarif
```

As a pre-commit hook:

```yaml
- repo: https://github.com/Oruwe/Stow
  rev: main
  hooks:
    - id: dockerfile-optimizer
```

## Try it

```bash
./stow analyze examples/Dockerfile.bad --format text    # 13 findings
./stow analyze examples/Dockerfile.good                 # clean
./stow refactor examples/Dockerfile.bad                 # hardened rewrite
```

## Development

```bash
pip install -e ".[dev]"
ruff check . && mypy app && pytest
```

On Windows, prefix with the interpreter: `python -m pytest`, `python -m ruff check .`.

The agent gates itself: CI runs `stow analyze Dockerfile` against this repo's own
image definition, and `stow refactor examples/Dockerfile.good --check` proves the
refactor engine is a no-op on already-optimal input.

## Scope

No network access, so the agent cannot confirm a digest exists or report CVEs in a
base image. No container execution, so runtime failures and true layer sizes are
outside what static analysis can see. Patterns outside the table above are
reported, never guessed at.
