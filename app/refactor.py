"""Deterministic Dockerfile refactors.

Each transform is mechanical: it fires only on a pattern it can rewrite without
guessing at project intent. Anything it cannot rewrite safely is left alone and
reported under `skipped`, so the caller always knows what was *not* done.
"""
from typing import Dict, List, Tuple

from app.rules import CLEANUP_MARKERS, _image_token, _logical_lines

APT_CLEANUP = "rm -rf /var/lib/apt/lists/*"
BUILDER_PREFIX = "/install"
DEFAULT_WORKDIR = "/app"
NONROOT_UID = "1001"


def _keyword(instruction: str) -> str:
    parts = instruction.split(maxsplit=1)
    return parts[0].upper() if parts else ""


def _argument(instruction: str) -> str:
    parts = instruction.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _is_broad_copy(instruction: str) -> bool:
    """True for `COPY . <dest>` — the layer that busts the cache on any edit."""
    if _keyword(instruction) != "COPY":
        return False
    args = [a for a in _argument(instruction).split() if not a.startswith("--")]
    return len(args) == 2 and args[0] == "."


def _pip_manifest(instruction: str) -> str:
    """Return the requirements file a `pip install -r ...` layer reads, if any."""
    if _keyword(instruction) != "RUN" or "pip install" not in instruction:
        return ""
    tokens = instruction.split()
    for flag, nxt in zip(tokens, tokens[1:]):
        if flag == "-r":
            return nxt
    return ""


def harden_apt(instructions: List[str]) -> Tuple[List[str], List[str]]:
    """Add --no-install-recommends and list cleanup to apt layers."""
    applied, out = [], []
    for instruction in instructions:
        lowered = instruction.lower()
        if _keyword(instruction) == "RUN" and "apt-get install" in lowered:
            if "--no-install-recommends" not in lowered:
                instruction = instruction.replace(
                    "apt-get install", "apt-get install --no-install-recommends", 1
                )
                applied.append("APT_NO_RECOMMENDS")
            if not any(m in instruction.lower() for m in CLEANUP_MARKERS):
                instruction = f"{instruction} && {APT_CLEANUP}"
                applied.append("APT_CACHE_CLEANUP")
        out.append(instruction)
    return out, applied


def hoist_manifest(instructions: List[str]) -> Tuple[List[str], List[str]]:
    """Move `COPY . <dest>` below the dependency install so deps stay cached.

    Editing application source should not invalidate the dependency layer.
    """
    broad = next((i for i, x in enumerate(instructions) if _is_broad_copy(x)), None)
    install = next((i for i, x in enumerate(instructions) if _pip_manifest(x)), None)
    if broad is None or install is None or broad > install:
        return instructions, []

    manifest = _pip_manifest(instructions[install])
    out = list(instructions)
    broad_copy = out.pop(broad)
    install -= 1
    out.insert(install, f"COPY {manifest} .")
    out.insert(install + 2, broad_copy)
    return out, ["CACHE_ORDER_HOIST"]


def drop_root(instructions: List[str]) -> Tuple[List[str], List[str]]:
    """Insert a non-root USER ahead of the entrypoint when none is declared."""
    if any(_keyword(x) == "USER" for x in instructions):
        return instructions, []

    out = list(instructions)
    entry = next(
        (i for i, x in enumerate(out) if _keyword(x) in ("CMD", "ENTRYPOINT")), len(out)
    )
    out.insert(entry, f"USER {NONROOT_UID}")
    return out, ["LEAST_PRIVILEGE_USER"]


def split_multistage(instructions: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """Split a single-stage pip build into builder + lean runtime stages.

    Fires only on the pattern it can rewrite faithfully: one FROM, one
    `pip install -r <manifest>`. Anything else is left as-is.
    """
    froms = [i for i, x in enumerate(instructions) if _keyword(x) == "FROM"]
    install = next((i for i, x in enumerate(instructions) if _pip_manifest(x)), None)

    if len(froms) != 1:
        return instructions, [], ["MULTISTAGE: already multi-stage or no FROM"]
    if install is None:
        return instructions, [], ["MULTISTAGE: no pip dependency layer to isolate"]

    base = _image_token(instructions[froms[0]])
    manifest = _pip_manifest(instructions[install])
    workdir = next(
        (_argument(x) for x in instructions[: install][::-1] if _keyword(x) == "WORKDIR"),
        DEFAULT_WORKDIR,
    )

    skip = {froms[0], install}
    skip |= {
        i
        for i, x in enumerate(instructions)
        if _keyword(x) == "COPY" and _argument(x).split()[:1] == [manifest]
    }
    remainder = [x for i, x in enumerate(instructions) if i not in skip]

    builder = [
        f"FROM {base} AS builder",
        f"WORKDIR {workdir}",
        f"COPY {manifest} .",
        f"RUN pip install --no-cache-dir --prefix={BUILDER_PREFIX} -r {manifest}",
        f"FROM {base}",
        f"COPY --from=builder {BUILDER_PREFIX} /usr/local",
    ]
    return builder + remainder, ["MULTISTAGE_SPLIT"], []


def _render(instructions: List[str]) -> str:
    """Join instructions, blank-separating each stage so output re-parses identically."""
    lines: List[str] = []
    for instruction in instructions:
        if _keyword(instruction) == "FROM" and lines:
            lines.append("")
        lines.append(instruction)
    return "\n".join(lines).strip() + "\n"


def refactor_dockerfile(content: str) -> Dict[str, object]:
    """Run every transform in order and return the rewritten Dockerfile."""
    instructions = [text for _, text in _logical_lines(content)]
    notes: List[str] = []
    applied: List[str] = []

    for transform in (harden_apt, hoist_manifest, drop_root):
        instructions, done = transform(instructions)
        applied.extend(done)

    instructions, done, skipped = split_multistage(instructions)
    applied.extend(done)
    notes.extend(skipped)

    unpinned = [
        _image_token(x)
        for x in instructions
        if _keyword(x) == "FROM" and _image_token(x).endswith(":latest")
    ]
    if unpinned:
        notes.append(
            "PINNED_VERSION: "
            + ", ".join(sorted(set(unpinned)))
            + " left untouched — choosing a replacement tag needs a human."
        )

    return {
        "dockerfile": _render(instructions),
        "transformations": applied,
        "skipped": notes,
    }
