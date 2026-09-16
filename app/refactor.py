"""Deterministic Dockerfile refactors.

Every transform is mechanical: it fires only on a pattern it can rewrite without
guessing at project intent. Anything it cannot rewrite safely is left alone and
reported under `skipped`, so the caller always knows what was *not* done.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.parser import image_reference, parse
from app.rules import CLEANUP_MARKERS, ROOT_USERS

APT_CLEANUP = "rm -rf /var/lib/apt/lists/*"
BUILDER_PREFIX = "/install"
DEFAULT_WORKDIR = "/app"
NONROOT_UID = "1001"

@dataclass
class Unit:
    """One instruction plus everything the author wrote around it.

    Transforms move, insert and rewrite these rather than bare strings, so a
    comment stays attached to the instruction it explains however the file is
    reordered.
    """

    text: str
    leading: list[str] = field(default_factory=list)
    source: str | None = None
    """Verbatim original, or None once a transform has rewritten the text."""

    def rewrite(self, text: str) -> None:
        self.text = text
        self.source = None

    @property
    def rendered(self) -> str:
        return self.source if self.source is not None else self.text


Transform = tuple[list[Unit], list[str]]


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


def _final_stage_slice(units: list[Unit]) -> int:
    """Index of the final FROM, or 0 when the file has no stages."""
    froms = [i for i, u in enumerate(units) if _keyword(u.text) == "FROM"]
    return froms[-1] if froms else 0


def harden_apt(units: list[Unit]) -> Transform:
    """Add --no-install-recommends and list cleanup to apt layers."""
    applied: list[str] = []
    for unit in units:
        lowered = unit.text.lower()
        if _keyword(unit.text) != "RUN" or "apt-get install" not in lowered:
            continue
        text = unit.text
        if "--no-install-recommends" not in lowered:
            text = text.replace("apt-get install", "apt-get install --no-install-recommends", 1)
            applied.append("APT_NO_RECOMMENDS")
        if not any(m in text.lower() for m in CLEANUP_MARKERS):
            text = f"{text} && {APT_CLEANUP}"
            applied.append("APT_CACHE_CLEANUP")
        if text != unit.text:
            unit.rewrite(text)
    return units, applied


def harden_pip(units: list[Unit]) -> Transform:
    """Stop pip persisting its wheel cache into the layer."""
    applied: list[str] = []
    for unit in units:
        if (
            _keyword(unit.text) == "RUN"
            and "pip install" in unit.text
            and "--no-cache-dir" not in unit.text
        ):
            unit.rewrite(unit.text.replace("pip install", "pip install --no-cache-dir", 1))
            applied.append("PIP_NO_CACHE")
    return units, applied


def add_to_copy(units: list[Unit]) -> Transform:
    """Rewrite ADD to COPY for plain local paths, where the two are equivalent."""
    applied: list[str] = []
    for unit in units:
        argument = _argument(unit.text)
        if (
            _keyword(unit.text) == "ADD"
            and not re.search(r"https?://", argument)
            and not re.search(r"\.(tar|tar\.gz|tgz|tar\.bz2|tar\.xz|zip)\b", argument)
        ):
            unit.rewrite(f"COPY {argument}")
            applied.append("ADD_TO_COPY")
    return units, applied


def hoist_manifest(units: list[Unit]) -> Transform:
    """Move `COPY . <dest>` below the dependency install so deps stay cached.

    Editing application source should not invalidate the dependency layer. The
    moved instruction carries its own comments with it.
    """
    broad = next((i for i, u in enumerate(units) if _is_broad_copy(u.text)), None)
    install = next((i for i, u in enumerate(units) if _pip_manifest(u.text)), None)
    if broad is None or install is None or broad > install:
        return units, []

    manifest = _pip_manifest(units[install].text)
    out = list(units)
    broad_copy = out.pop(broad)
    install -= 1
    out.insert(install, Unit(f"COPY {manifest} ."))
    out.insert(install + 2, broad_copy)
    return out, ["CACHE_ORDER_HOIST"]


def drop_root(units: list[Unit]) -> Transform:
    """Ensure the *final* stage runs unprivileged.

    A USER in a builder stage protects nothing, so only the shipped stage counts.
    """
    start = _final_stage_slice(units)
    final = units[start:]
    users = [i for i, u in enumerate(final) if _keyword(u.text) == "USER"]

    if users:
        last = users[-1]
        if _argument(final[last].text).lower() not in ROOT_USERS:
            return units, []
        final[last].rewrite(f"USER {NONROOT_UID}")
        return units, ["LEAST_PRIVILEGE_DEROOT"]

    entry = next(
        (i for i, u in enumerate(final) if _keyword(u.text) in ("CMD", "ENTRYPOINT")), len(final)
    )
    out = list(units)
    out.insert(start + entry, Unit(f"USER {NONROOT_UID}"))
    return out, ["LEAST_PRIVILEGE_USER"]


def split_multistage(units: list[Unit]) -> tuple[list[Unit], list[str], list[str]]:
    """Split a single-stage pip build into builder + lean runtime stages.

    Fires only on the pattern it can rewrite faithfully: one FROM, one
    `pip install -r <manifest>`. Anything else is left as-is.
    """
    froms = [i for i, u in enumerate(units) if _keyword(u.text) == "FROM"]
    install = next((i for i, u in enumerate(units) if _pip_manifest(u.text)), None)

    if len(froms) != 1:
        return units, [], ["MULTISTAGE: already multi-stage or no FROM"]
    if install is None:
        return units, [], ["MULTISTAGE: no pip dependency layer to isolate"]

    base, _ = image_reference(_argument(units[froms[0]].text))
    manifest = _pip_manifest(units[install].text)
    workdir = next(
        (_argument(u.text) for u in units[:install][::-1] if _keyword(u.text) == "WORKDIR"),
        DEFAULT_WORKDIR,
    )

    skip = {froms[0], install}
    skip |= {
        i
        for i, u in enumerate(units)
        if _keyword(u.text) == "COPY" and _argument(u.text).split()[:1] == [manifest]
    }
    remainder = [u for i, u in enumerate(units) if i not in skip]

    builder = [
        # The file's own header comment introduces the build, so it stays on top.
        Unit(f"FROM {base} AS builder", leading=units[froms[0]].leading),
        Unit(f"WORKDIR {workdir}"),
        Unit(f"COPY {manifest} ."),
        Unit(f"RUN pip install --no-cache-dir --prefix={BUILDER_PREFIX} -r {manifest}"),
        Unit(f"FROM {base}"),
        Unit(f"COPY --from=builder {BUILDER_PREFIX} /usr/local"),
    ]
    return builder + remainder, ["MULTISTAGE_SPLIT"], []


def _render(units: list[Unit], trailing: list[str]) -> str:
    """Re-emit the file: comments, blank lines and untouched formatting intact.

    An instruction no transform touched is written back from its original
    source, so a hand-formatted multi-line RUN survives instead of being
    collapsed onto one line.
    """
    lines: list[str] = []
    for unit in units:
        leading = list(unit.leading)
        # Separate stages, unless the author's own blank line already does.
        needs_gap = lines and lines[-1] != "" and not (leading and leading[0] == "")
        if _keyword(unit.text) == "FROM" and needs_gap:
            lines.append("")
        lines.extend(leading)
        lines.append(unit.rendered)
    lines.extend(trailing)
    return "\n".join(lines).strip() + "\n"


def refactor_dockerfile(content: str) -> dict[str, Any]:
    """Run every transform in order and return the rewritten Dockerfile."""
    doc = parse(content)
    units = [
        Unit(text=i.text, leading=list(i.leading), source=i.source or None)
        for i in doc.instructions
    ]
    notes: list[str] = []
    applied: list[str] = []

    for transform in (harden_apt, harden_pip, add_to_copy, hoist_manifest, drop_root):
        units, done = transform(units)
        applied.extend(done)

    units, done, skipped = split_multistage(units)
    applied.extend(done)
    notes.extend(skipped)

    aliases = {s.alias for s in doc.stages if s.alias}
    unpinned = sorted(
        {
            image
            for image in (
                image_reference(_argument(u.text))[0]
                for u in units
                if _keyword(u.text) == "FROM"
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

    # Parser directives need no special handling: they are comments, and the
    # renderer now emits every comment verbatim where the author put it.
    return {
        "dockerfile": _render(units, doc.trailing),
        "transformations": applied,
        "skipped": notes,
    }
