"""Rule registry for Dockerfile audits.

Every rule is a pure function over the parsed model and yields Findings. Rules
carry a severity so callers can gate a build on what matters to them rather than
on the raw count of findings.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from app.parser import Dockerfile, Instruction, Stage, parse

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

CLEANUP_MARKERS = ("rm -rf /var/lib/apt/lists", "apt-get clean", "rm -rf /var/cache/apt")
DEPENDENCY_INSTALLS = (
    "pip install",
    "npm ci",
    "npm install",
    "yarn install",
    "poetry install",
    "bundle install",
    "go mod download",
)
SECRET_KEY_RE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL)",
    re.IGNORECASE,
)
INTERPOLATED_RE = re.compile(r"\$\{?\w+")
ROOT_USERS = {"root", "0", "0:0", "root:root"}


@dataclass
class Finding:
    rule: str
    severity: str
    line: int
    stage: int
    message: str
    remediation: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Rule:
    id: str
    severity: str
    summary: str
    check: Callable[[Dockerfile], Iterable[Finding]]


REGISTRY: list[Rule] = []


def rule(rule_id: str, severity: str, summary: str) -> Callable:
    def decorator(func: Callable[[Dockerfile], Iterable[Finding]]) -> Callable:
        REGISTRY.append(Rule(rule_id, severity, summary, func))
        return func

    return decorator


def _shell_instructions(doc: Dockerfile) -> list[Instruction]:
    return [i for i in doc.instructions if i.keyword == "RUN"]


def _stage_aliases(doc: Dockerfile) -> set[str]:
    return {s.alias for s in doc.stages if s.alias}


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------


@rule("PINNED_VERSION", "high", "Base images must pin a tag or digest, never ':latest'.")
def check_pinned_version(doc: Dockerfile) -> Iterable[Finding]:
    aliases = _stage_aliases(doc)
    for stage in doc.stages:
        image = stage.base
        if not image or image in aliases or image == "scratch":
            continue  # a stage reference or scratch is already reproducible
        if INTERPOLATED_RE.search(image):
            continue  # resolved from an ARG; the value is outside this file
        if "@sha256:" in image:
            continue
        tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else ""
        if not tag or tag == "latest":
            yield Finding(
                "PINNED_VERSION",
                "high",
                stage.line,
                stage.index,
                f"Base image '{image}' uses unpinned version or ':latest'.",
                "Pin an explicit version tag or a @sha256 digest for reproducible builds.",
            )


@rule("LEAST_PRIVILEGE", "critical", "The final stage must drop off root.")
def check_least_privilege(doc: Dockerfile) -> Iterable[Finding]:
    """Only the final stage ships. A USER in a builder stage protects nothing."""
    final = doc.final_stage
    if final is None:
        return

    users = [i for i in final.instructions if i.keyword == "USER"]
    if not users:
        yield Finding(
            "LEAST_PRIVILEGE",
            "critical",
            0,
            final.index,
            "No USER instruction in the final stage. Container executes as root by default.",
            "Add a non-root USER before the entrypoint.",
        )
        return

    last = users[-1]
    if last.argument.strip().lower() in ROOT_USERS:
        yield Finding(
            "LEAST_PRIVILEGE",
            "critical",
            last.line,
            final.index,
            f"Final stage explicitly runs as '{last.argument.strip()}'.",
            "Switch to an unprivileged UID before the entrypoint.",
        )


@rule("SECRET_IN_IMAGE", "critical", "Secrets must not be baked into image layers.")
def check_secret_in_image(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in doc.of_keyword("ENV", "ARG"):
        for key, value in _key_values(instruction.argument):
            if not SECRET_KEY_RE.search(key):
                continue
            if not value or INTERPOLATED_RE.search(value):
                continue  # declared for injection at build time, not a baked value
            yield Finding(
                "SECRET_IN_IMAGE",
                "critical",
                instruction.line,
                instruction.stage,
                f"{instruction.keyword} '{key}' assigns a literal secret value.",
                "Use BuildKit secret mounts (RUN --mount=type=secret) or runtime injection.",
            )


@rule("NO_SUDO", "high", "sudo has no place in a container build.")
def check_no_sudo(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in _shell_instructions(doc):
        if re.search(r"\bsudo\b", instruction.body):
            yield Finding(
                "NO_SUDO",
                "high",
                instruction.line,
                instruction.stage,
                "RUN layer invokes sudo.",
                "Build layers run as root already; use USER to step down instead.",
            )


@rule("ADD_REMOTE", "high", "ADD with a remote URL fetches unverified content.")
def check_add_remote(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in doc.of_keyword("ADD"):
        if re.search(r"https?://", instruction.argument):
            yield Finding(
                "ADD_REMOTE",
                "high",
                instruction.line,
                instruction.stage,
                "ADD pulls a remote URL into the image without checksum verification.",
                "Fetch with RUN curl and verify a checksum, or vendor the artifact.",
            )


# --------------------------------------------------------------------------
# Layer size and caching
# --------------------------------------------------------------------------


@rule("CACHE_CLEANUP", "medium", "apt layers must clean their package lists.")
def check_cache_cleanup(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in _shell_instructions(doc):
        lowered = instruction.body.lower()
        if "apt-get install" not in lowered:
            continue
        if any(marker in lowered for marker in CLEANUP_MARKERS):
            continue
        yield Finding(
            "CACHE_CLEANUP",
            "medium",
            instruction.line,
            instruction.stage,
            "apt-get install without cleaning /var/lib/apt/lists/* inflates layer size.",
            "Append '&& rm -rf /var/lib/apt/lists/*' to the same RUN layer.",
        )


@rule("APT_UPDATE_ISOLATED", "medium", "apt-get update must share a layer with install.")
def check_apt_update_isolated(doc: Dockerfile) -> Iterable[Finding]:
    """A cached `update` layer serves stale indexes to a later `install`."""
    for instruction in _shell_instructions(doc):
        lowered = instruction.body.lower()
        if "apt-get update" in lowered and "apt-get install" not in lowered:
            yield Finding(
                "APT_UPDATE_ISOLATED",
                "medium",
                instruction.line,
                instruction.stage,
                "apt-get update runs in its own layer and will be served from cache.",
                "Chain update and install in one RUN so the index is always fresh.",
            )


@rule("CACHE_ORDER", "medium", "Dependency installs must precede the source copy.")
def check_cache_order(doc: Dockerfile) -> Iterable[Finding]:
    for stage in doc.stages:
        broad = _broad_copy_index(stage)
        install = next(
            (
                idx
                for idx, i in enumerate(stage.instructions)
                if i.keyword == "RUN"
                and any(dep in i.body.lower() for dep in DEPENDENCY_INSTALLS)
            ),
            None,
        )
        if broad is None or install is None or broad > install:
            continue
        yield Finding(
            "CACHE_ORDER",
            "medium",
            stage.instructions[install].line,
            stage.index,
            "Source is copied before dependencies are installed, so any code edit "
            "invalidates the dependency layer.",
            "Copy the manifest and install first, then COPY the rest of the source.",
        )


@rule("PIP_NO_CACHE", "low", "pip should not persist its wheel cache into a layer.")
def check_pip_no_cache(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in _shell_instructions(doc):
        lowered = instruction.body.lower()
        if "pip install" in lowered and "--no-cache-dir" not in lowered:
            yield Finding(
                "PIP_NO_CACHE",
                "low",
                instruction.line,
                instruction.stage,
                "pip install without --no-cache-dir leaves the wheel cache in the layer.",
                "Add --no-cache-dir, or mount a BuildKit cache instead.",
            )


@rule("ADD_OVER_COPY", "low", "Prefer COPY over ADD for local files.")
def check_add_over_copy(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in doc.of_keyword("ADD"):
        argument = instruction.argument
        if re.search(r"https?://", argument):
            continue  # reported by ADD_REMOTE
        if re.search(r"\.(tar|tar\.gz|tgz|tar\.bz2|tar\.xz|zip)\b", argument):
            continue  # ADD's auto-extraction is the intended use here
        yield Finding(
            "ADD_OVER_COPY",
            "low",
            instruction.line,
            instruction.stage,
            "ADD used for a plain local path.",
            "Use COPY, whose behaviour is explicit and predictable.",
        )


# --------------------------------------------------------------------------
# Correctness and hygiene
# --------------------------------------------------------------------------


@rule("PIPE_WITHOUT_PIPEFAIL", "medium", "Piped shell layers swallow upstream failures.")
def check_pipe_without_pipefail(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in _shell_instructions(doc):
        body = instruction.body
        if body.strip().startswith("["):
            continue  # exec form, no shell involved
        if "|" not in re.sub(r"\|\|", "", body):
            continue
        if "pipefail" in body:
            continue
        yield Finding(
            "PIPE_WITHOUT_PIPEFAIL",
            "medium",
            instruction.line,
            instruction.stage,
            "Pipeline in a RUN layer: a failure upstream of the pipe is discarded.",
            "Set a pipefail SHELL, or prefix the layer with 'set -o pipefail &&'.",
        )


@rule("WORKDIR_ABSOLUTE", "low", "WORKDIR should be an absolute path.")
def check_workdir_absolute(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in doc.of_keyword("WORKDIR"):
        path = instruction.argument.strip().strip('"')
        if not path or path.startswith("/") or INTERPOLATED_RE.search(path):
            continue
        yield Finding(
            "WORKDIR_ABSOLUTE",
            "low",
            instruction.line,
            instruction.stage,
            f"Relative WORKDIR '{path}' depends on the preceding working directory.",
            "Use an absolute path so the instruction is order-independent.",
        )


@rule("MAINTAINER_DEPRECATED", "low", "MAINTAINER is deprecated.")
def check_maintainer(doc: Dockerfile) -> Iterable[Finding]:
    for instruction in doc.of_keyword("MAINTAINER"):
        yield Finding(
            "MAINTAINER_DEPRECATED",
            "low",
            instruction.line,
            instruction.stage,
            "MAINTAINER is deprecated.",
            'Use LABEL org.opencontainers.image.authors="..." instead.',
        )


@rule("MISSING_HEALTHCHECK", "low", "Long-running images should declare a HEALTHCHECK.")
def check_missing_healthcheck(doc: Dockerfile) -> Iterable[Finding]:
    final = doc.final_stage
    if final is None or any(i.keyword == "HEALTHCHECK" for i in final.instructions):
        return
    yield Finding(
        "MISSING_HEALTHCHECK",
        "low",
        0,
        final.index,
        "Final stage declares no HEALTHCHECK, so orchestrators cannot detect a hung process.",
        "Add a HEALTHCHECK, or document that liveness is handled outside the image.",
    )


# --------------------------------------------------------------------------
# Helpers and entry points
# --------------------------------------------------------------------------


def _key_values(argument: str) -> list[tuple]:
    """Parse both `ENV K=v K2=v2` and the legacy `ENV K v` forms."""
    tokens = argument.split()
    if not tokens:
        return []
    if "=" not in tokens[0]:
        return [(tokens[0], " ".join(tokens[1:]).strip().strip("\"'"))]
    pairs: list[tuple[str, str]] = []
    for token in tokens:
        if "=" in token:
            key, _, value = token.partition("=")
            pairs.append((key, value.strip().strip("\"'")))
    return pairs


def _broad_copy_index(stage: Stage) -> int | None:
    for idx, instruction in enumerate(stage.instructions):
        if instruction.keyword != "COPY":
            continue
        args = [a for a in instruction.argument.split() if not a.startswith("--")]
        if len(args) == 2 and args[0] in (".", "./"):
            return idx
    return None


def severity_at_least(finding: dict[str, object], threshold: str) -> bool:
    return SEVERITY_ORDER.get(str(finding.get("severity")), 0) >= SEVERITY_ORDER[threshold]


def analyze(doc: Dockerfile, disabled: Iterable[str] | None = None) -> list[Finding]:
    """Run every enabled rule and return findings ordered by severity then line."""
    skip = set(disabled or ())
    findings: list[Finding] = []
    for registered in REGISTRY:
        if registered.id in skip:
            continue
        findings.extend(registered.check(doc))
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.stage, f.line))
    return findings


def analyze_dockerfile(
    content: str, disabled: Iterable[str] | None = None
) -> list[dict[str, object]]:
    """Audit Dockerfile source and return findings as plain dicts."""
    return [f.to_dict() for f in analyze(parse(content), disabled)]


def rule_catalog() -> list[dict[str, str]]:
    return [
        {"rule": r.id, "severity": r.severity, "summary": r.summary}
        for r in sorted(REGISTRY, key=lambda r: (-SEVERITY_ORDER[r.severity], r.id))
    ]
