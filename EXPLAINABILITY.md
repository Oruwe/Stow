# Dockerfile Optimizer Explainability

## Decision Reasoning
The agent parses a Dockerfile into stages and logical instructions, then runs a fixed registry of deterministic rules over that model, each carrying a severity that callers gate on. Refactors are applied only where a pattern can be rewritten mechanically, so the same input always yields the same output and nothing is inferred from project intent.

## Data Inputs
The only input is the raw text of the target Dockerfile, read from a path supplied on the command line. Behaviour is further shaped by an optional TOML configuration file that disables rules or changes the failure threshold.

## Known Limitations
The agent performs no network access and never resolves a registry, so it cannot verify that a pinned digest exists or report CVEs affecting a base image. It also cannot execute containers, meaning runtime failures and layer sizes are outside what static analysis can observe.
