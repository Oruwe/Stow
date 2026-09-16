# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0]

### Added
- Structured Dockerfile parser handling parser directives (`# syntax`, `# escape`),
  custom escape characters, heredocs, comments embedded in continuations, and
  multi-stage indexing.
- Rule registry of 14 rules across security, caching and hygiene, each with a
  severity (`low` → `critical`).
- `stow analyze` with `--format json|text|sarif`, `--fail-on <severity>` and
  repeatable `--disable RULE`.
- `stow refactor` with `--write` (edit in place) and `--check` (CI drift gate),
  applying six deterministic transforms.
- `stow rules` catalog listing and `stow version`.
- Configuration via `.dockerfile-optimizer.toml` or `[tool.dockerfile-optimizer]`
  in `pyproject.toml`, discovered by walking up from the working directory.
- SARIF 2.1.0 output and a code-scanning workflow so findings land inline on a
  pull request diff.
- Packaging with a `stow` console script, a self-hosting `Dockerfile` that passes
  the agent's own audit, and a `.pre-commit-hooks.yaml` hook definition.

### Fixed
- `analyze`/`refactor` on a directory now report "is a directory, not a
  Dockerfile" on every platform. Linux raises `IsADirectoryError` when opening
  a directory and Windows raises `PermissionError`, so relying on the OS's
  choice of exception gave Windows users a misleading "is not readable".
- CI runs the suite on Windows as well as Linux. The bug above shipped because
  every job ran on ubuntu, and no amount of Linux coverage can catch a
  divergence that only exists off Linux.
- `examples/Dockerfile.good` declares `HEALTHCHECK NONE`, so the shipped
  example now has zero findings rather than one non-blocking one. An example
  named "good" should be exemplary, not merely passing.
- Lowered the supported Python floor to 3.10. The 3.11 requirement made the
  package uninstallable on a common interpreter, including the author's own
  machine; `tomli` is now pulled in only where the standard library lacks
  `tomllib`.
- Added `stow.cmd` so the launcher works in PowerShell and cmd. The `stow`
  script is bash and cannot run on Windows without WSL or Git Bash.
- Added `.gitattributes` pinning `.cmd` to CRLF and everything else to LF, so a
  checkout with `core.autocrlf` set either way still produces a runnable
  launcher.
- `LEAST_PRIVILEGE` now evaluates only the final stage. A `USER` in a builder
  stage does not reach the shipped image and no longer suppresses the finding.
- `USER root` and `USER 0` in the final stage are flagged rather than accepted as
  satisfying the check.
- `FROM builder` (a stage reference) and `FROM python:${VERSION}` (an ARG) are no
  longer reported as unpinned images.
- A registry host with a port (`registry.internal:5000/app`) is no longer mistaken
  for a tagged image.
- Continuation-spanning `RUN` layers are judged as the single layer they build,
  so the standard `apt-get install ... && rm -rf /var/lib/apt/lists/*` idiom is
  no longer flagged.
- `FROM --platform=...` no longer has its flag read as the image name.
- A bare `FROM` no longer raises `IndexError`.
- Digest pins (`@sha256:`) are recognised as pinned.
- Refactoring is idempotent; stage separation is applied at render time so output
  re-parses to itself.
- Instructions no transform touched are re-emitted from their original source, so
  a hand-formatted multi-line `RUN` is no longer collapsed onto one line. Without
  this, `refactor --check` reported drift on almost every real Dockerfile and
  `--write` churned every diff.
- The `stow` launcher no longer changes directory, so relative paths resolve
  against the caller's working directory.
