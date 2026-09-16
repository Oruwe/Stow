"""Deterministic Dockerfile refactors.

Every transform is mechanical: it fires only on a pattern it can rewrite without
guessing at project intent. Anything it cannot rewrite safely is left alone and
reported under `skipped`, so the caller always knows what was *not* done.
"""
from __future__ import annotations

import re
from typing import Any

from app.parser import image_reference, parse
from app.rules import CLEANUP_MARKERS, ROOT_USERS

APT_CLEANUP = "rm -rf /var/lib/apt/lists/*"
BUILDER_PREFIX = "/install"
DEFAULT_WORKDIR = "/app"
NONROOT_UID = "1001"

Transform = tuple[list[str], list[str]]


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
    return len(args) == 2 and args[0] in (".", "./")


def _pip_manifest(instruction: str) -> str:
    """Return the requirements file a `pip install -r ...` layer reads, if any."""
    if _keyword(instruction) != "RUN" or "pip install" not in instruction:
        return ""
    tokens = instruction.split()
    for flag, nxt in zip(tokens, tokens[1:], strict=False):
        if flag == "-r":
            return nxt
    return ""


def _final_stage_slice(instructions: list[str]) -> int:
    """Index of the final FROM, or 0 when the file has no stages."""
    froms = [i for i, x in enumerate(instructions) if _keyword(x) == "FROM"]
    return froms[-1] if froms else 0


def harden_apt(instructions: list[str]) -> Transform:
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


def harden_pip(instructions: list[str]) -> Transform:
    """Stop pip persisting its wheel cache into the layer."""
    applied, out = [], []
    for instruction in instructions:
        if (
            _keyword(instruction) == "RUN"
            and "pip install" in instruction
            and "--no-cache-dir" not in instruction
        ):
            instruction = instruction.replace("pip install", "pip install --no-cache-dir", 1)
            applied.append("PIP_NO_CACHE")
        out.append(instruction)
    return out, applied


def add_to_copy(instructions: list[str]) -> Transform:
    """Rewrite ADD to COPY for plain local paths, where the two are equivalent."""
    applied, out = [], []
    for instruction in instructions:
        argument = _argument(instruction)
        if (
            _keyword(instruction) == "ADD"
            and not re.search(r"https?://", argument)
            and not re.search(r"\.(tar|tar\.gz|tgz|tar\.bz2|tar\.xz|zip)\b", argument)
        ):
            instruction = f"COPY {argument}"
            applied.append("ADD_TO_COPY")
        out.append(instruction)
    return out, applied


def hoist_manifest(instructions: list[str]) -> Transform:
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


def drop_root(instructions: list[str]) -> Transform:
    """Ensure the *final* stage runs unprivileged.

    A USER in a builder stage protects nothing, so only the shipped stage counts.
    """
    start = _final_stage_slice(instructions)
    final = instructions[start:]
    users = [i for i, x in enumerate(final) if _keyword(x) == "USER"]

    if users:
        last = users[-1]
        if _argument(final[last]).lower() not in ROOT_USERS:
            return instructions, []
        out = list(instructions)
        out[start + last] = f"USER {NONROOT_UID}"
        return out, ["LEAST_PRIVILEGE_DEROOT"]

    entry = next(
        (i for i, x in enumerate(final) if _keyword(x) in ("CMD", "ENTRYPOINT")), len(final)
    )
    out = list(instructions)
    out.insert(start + entry, f"USER {NONROOT_UID}")
    return out, ["LEAST_PRIVILEGE_USER"]


def split_multistage(instructions: list[str]) -> tuple[list[str], list[str], list[str]]:
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

    base, _ = image_reference(_argument(instructions[froms[0]]))
    manifest = _pip_manifest(instructions[install])
    workdir = next(
        (_argument(x) for x in instructions[:install][::-1] if _keyword(x) == "WORKDIR"),
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


def _render(instructions: list[str], originals: dict[str, str]) -> str:
    """Join instructions, blank-separating each stage so output re-parses identically.

    An instruction no transform touched is re-emitted from its original source, so
    a hand-formatted multi-line RUN survives the rewrite instead of being
    collapsed onto one line.
    """
    lines: list[str] = []
    for instruction in instructions:
        if _keyword(instruction) == "FROM" and lines:
            lines.append("")
        lines.append(originals.get(instruction, instruction))
    return "\n".join(lines).strip() + "\n"


def refactor_dockerfile(content: str) -> dict[str, Any]:
    """Run every transform in order and return the rewritten Dockerfile."""
    doc = parse(content)
    instructions = [i.text for i in doc.instructions]
    originals = {i.text: i.source for i in doc.instructions if i.source}
    notes: list[str] = []
    applied: list[str] = []

    for transform in (harden_apt, harden_pip, add_to_copy, hoist_manifest, drop_root):
        instructions, done = transform(instructions)
        applied.extend(done)

    instructions, done, skipped = split_multistage(instructions)
    applied.extend(done)
    notes.extend(skipped)

    aliases = {s.alias for s in doc.stages if s.alias}
    unpinned = sorted(
        {
            image
            for image in (
                image_reference(_argument(x))[0]
                for x in instructions
                if _keyword(x) == "FROM"
            )
            if image and image not in aliases and image != "scratch"
            and (image.endswith(":latest") or ":" not in image.rsplit("/", 1)[-1])
        }
    )
    if unpinned:
        notes.append(
            "PINNED_VERSION: "
            + ", ".join(unpinned)
            + " left untouched — choosing a replacement tag needs a human."
        )

    header = [f"# {k}={v}" for k, v in doc.directives.items()]
    body = _render(instructions, originals)
    return {
        "dockerfile": ("\n".join(header) + "\n" + body) if header else body,
        "transformations": applied,
        "skipped": notes,
    }
