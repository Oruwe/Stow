# dockerfile-optimizer

A container-optimization micro-agent. It parses a Dockerfile, flags layer-caching
and security anti-patterns, and reports them as machine-readable JSON.

## Commands

The agent is driven through the `stow` launcher:

```bash
./stow analyze <PATH>   # audit a Dockerfile (defaults to ./Dockerfile)
./stow rules            # list the rule IDs this agent enforces
./stow version          # print the agent version
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

## Try it

```bash
./stow analyze examples/Dockerfile.bad    # 3 findings
./stow analyze examples/Dockerfile.good   # clean
```

## Development

```bash
pip install -r requirements.txt
pytest
```

## Scope

This version audits and reports. It does not yet rewrite a Dockerfile into a
multi-stage build — `examples/Dockerfile.good` shows the target shape by hand.
It also cannot run containers, so runtime-only failures are out of reach, and it
cannot introspect private base image layers without registry credentials.
