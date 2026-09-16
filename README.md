# dockerfile-optimizer

A container-optimization micro-agent. It parses a Dockerfile, flags layer-caching
and security anti-patterns, and reports them as machine-readable JSON.

## Commands

The agent is driven through the `stow` launcher:

```bash
./stow analyze <PATH>            # audit a Dockerfile (defaults to ./Dockerfile)
./stow refactor <PATH>           # print an optimized rewrite to stdout
./stow refactor <PATH> --write   # rewrite the file in place
./stow rules                     # list the rule IDs this agent enforces
./stow version                   # print the agent version
```

Exit codes from `analyze`: `0` clean, `1` file not found, `2` findings reported —
so it drops straight into CI as a gate.

## Rules

| Rule | What it catches |
| --- | --- |
| `PINNED_VERSION` | Base image on `:latest` or with no tag/digest at all. |
| `CACHE_CLEANUP` | `apt-get install` that never cleans `/var/lib/apt/lists/*` in the same layer. |
| `LEAST_PRIVILEGE` | No `USER` instruction, so the container runs as root. |

Instructions split across backslash continuations are joined before analysis, so a
multi-line `RUN` is judged as the single layer it actually builds.

## Refactors

`refactor` only fires transforms it can apply mechanically. Anything requiring
judgement is left in place and reported on stderr under `# skipped ->`.

| Transform | Effect |
| --- | --- |
| `APT_NO_RECOMMENDS` | Adds `--no-install-recommends` to apt layers. |
| `APT_CACHE_CLEANUP` | Appends list cleanup to the same `RUN`, so it lands in one layer. |
| `CACHE_ORDER_HOIST` | Moves `COPY . <dest>` below the dependency install, so editing source no longer busts the dependency cache. |
| `LEAST_PRIVILEGE_USER` | Inserts `USER 1001` ahead of the entrypoint. |
| `MULTISTAGE_SPLIT` | Splits a single-stage pip build into `builder` + lean runtime. |

It will **not** invent a version tag for an unpinned base image — picking the
replacement is a human call, so it reports it instead. The multi-stage split
fires only on the pattern it can rewrite faithfully (one `FROM`, one
`pip install -r`); other shapes are reported as skipped rather than guessed at.
Refactoring is idempotent: running it twice yields the same file.

## Try it

```bash
./stow analyze examples/Dockerfile.bad    # 3 findings
./stow analyze examples/Dockerfile.good   # clean
./stow refactor examples/Dockerfile.bad   # hardened rewrite on stdout
```

## Development

```bash
pip install -r requirements.txt
pytest
```

## Scope

The agent cannot run containers, so runtime-only failures are out of reach, and
it cannot introspect private base image layers without registry credentials.
Transforms outside the recognized patterns above are reported, never guessed.
