"""CLI entry point for dockerfile-optimizer.

Invoked as `stow <command>`:

    stow analyze [PATH]   audit a Dockerfile and emit JSON findings
    stow rules            list the rule IDs this agent enforces
    stow version          print the agent version
"""
import sys
import json

from app import __version__
from app.rules import analyze_dockerfile

RULES = {
    "PINNED_VERSION": "Base image must pin a tag or digest, never ':latest'.",
    "CACHE_CLEANUP": "apt-get install must clean /var/lib/apt/lists/* in the same layer.",
    "LEAST_PRIVILEGE": "A USER instruction must drop the container off root.",
}

USAGE = "usage: stow {analyze [PATH] | rules | version}"


def run(file_path: str = "Dockerfile") -> int:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(json.dumps({"error": f"File '{file_path}' not found."}))
        return 1

    findings = analyze_dockerfile(content)
    output = {
        "status": "PASSED" if not findings else "OPTIMIZATION_NEEDED",
        "target": file_path,
        "total_findings": len(findings),
        "details": findings,
    }
    print(json.dumps(output, indent=2))
    return 0 if not findings else 2


def main(argv: list) -> int:
    command = argv[0] if argv else "analyze"

    if command in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if command in ("version", "--version"):
        print(json.dumps({"name": "dockerfile-optimizer", "version": __version__}))
        return 0
    if command == "rules":
        print(json.dumps(RULES, indent=2))
        return 0
    if command == "analyze":
        return run(argv[1] if len(argv) > 1 else "Dockerfile")

    print(USAGE, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
