"""A real Dockerfile parser.

Line-oriented scanning is enough for a demo and wrong for production: it breaks
on parser directives, custom escape characters, heredocs, and comments embedded
inside continuations. This module turns source text into a structured model that
the rules and refactor engines both read, so neither has to re-guess the syntax.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DIRECTIVE_RE = re.compile(r"^#\s*(syntax|escape|check)\s*=\s*(\S+)\s*$", re.IGNORECASE)
HEREDOC_RE = re.compile(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1")
DEFAULT_ESCAPE = "\\"


@dataclass
class Instruction:
    """One logical Dockerfile instruction, continuations already joined."""

    keyword: str
    argument: str
    line: int
    end_line: int
    stage: int
    raw: str
    heredoc: list[str] = field(default_factory=list)
    source: str = ""
    """Verbatim original text, continuations and heredoc body included.

    Preserved so a refactor can re-emit untouched instructions exactly as the
    author wrote them instead of collapsing their formatting.
    """

    @property
    def text(self) -> str:
        return f"{self.keyword} {self.argument}".strip()

    @property
    def body(self) -> str:
        """Argument plus any heredoc content, for rules that scan shell text."""
        return "\n".join([self.argument, *self.heredoc]) if self.heredoc else self.argument


@dataclass
class Stage:
    index: int
    base: str
    alias: str | None
    line: int
    instructions: list[Instruction] = field(default_factory=list)
    is_final: bool = False

    @property
    def platform_pinned(self) -> bool:
        return "@sha256:" in self.base


@dataclass
class Dockerfile:
    directives: dict[str, str] = field(default_factory=dict)
    instructions: list[Instruction] = field(default_factory=list)
    stages: list[Stage] = field(default_factory=list)

    @property
    def final_stage(self) -> Stage | None:
        return self.stages[-1] if self.stages else None

    def of_keyword(self, *keywords: str) -> list[Instruction]:
        wanted = {k.upper() for k in keywords}
        return [i for i in self.instructions if i.keyword in wanted]


def _read_directives(lines: list[str]) -> dict[str, str]:
    """Parser directives are only honoured before the first non-comment line."""
    directives: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        match = DIRECTIVE_RE.match(stripped)
        if not match:
            break
        directives[match.group(1).lower()] = match.group(2)
    return directives


def _split_keyword(text: str) -> tuple:
    parts = text.split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0].upper(), (parts[1].strip() if len(parts) > 1 else "")


def image_reference(argument: str) -> tuple:
    """Return (image, alias) from a FROM argument, skipping --flags."""
    tokens = [t for t in argument.split() if not t.startswith("--")]
    if not tokens:
        return "", None
    alias = tokens[2] if len(tokens) >= 3 and tokens[1].upper() == "AS" else None
    return tokens[0], alias


def parse(content: str) -> Dockerfile:
    """Parse Dockerfile source into stages and logical instructions."""
    lines = content.splitlines()
    directives = _read_directives(lines)
    escape = directives.get("escape", DEFAULT_ESCAPE)
    if escape not in ("\\", "`"):
        escape = DEFAULT_ESCAPE

    doc = Dockerfile(directives=directives)
    buffer: list[str] = []
    start = 0
    index = 0

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        index += 1

        # Comments and blanks are skipped outright, and are also legal *inside* a
        # continuation, where Docker drops them without ending the instruction.
        if not stripped or stripped.startswith("#"):
            continue

        if not buffer:
            start = index

        if stripped.endswith(escape):
            buffer.append(stripped[: -len(escape)].strip())
            continue

        buffer.append(stripped)
        text = " ".join(part for part in buffer if part).strip()
        buffer = []

        keyword, argument = _split_keyword(text)
        heredoc: list[str] = []
        terminators = [m.group(2) for m in HEREDOC_RE.finditer(argument)]
        for terminator in terminators:
            while index < len(lines):
                body = lines[index]
                index += 1
                if body.strip() == terminator:
                    break
                heredoc.append(body)

        instruction = Instruction(
            keyword=keyword,
            argument=argument,
            line=start,
            end_line=index,
            stage=len(doc.stages),
            raw=text,
            heredoc=heredoc,
            source="\n".join(lines[start - 1 : index]),
        )

        if keyword == "FROM":
            image, alias = image_reference(argument)
            doc.stages.append(
                Stage(index=len(doc.stages) + 1, base=image, alias=alias, line=start)
            )
            instruction.stage = len(doc.stages)

        doc.instructions.append(instruction)
        if doc.stages and instruction.stage == len(doc.stages):
            doc.stages[-1].instructions.append(instruction)

    if buffer:  # trailing continuation with no terminating line
        text = " ".join(part for part in buffer if part).strip()
        keyword, argument = _split_keyword(text)
        doc.instructions.append(
            Instruction(keyword, argument, start, len(lines), len(doc.stages), text)
        )

    if doc.stages:
        doc.stages[-1].is_final = True
    return doc
