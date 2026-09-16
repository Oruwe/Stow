"""Linting and optimization rules for Dockerfiles."""
from typing import List, Dict, Tuple

CLEANUP_MARKERS = (
    "rm -rf /var/lib/apt/lists",
    "apt-get clean",
)


def _logical_lines(content: str) -> List[Tuple[int, str]]:
    """Collapse backslash continuations into one logical instruction.

    Returns (line_number_of_first_physical_line, joined_instruction) pairs so a
    RUN spread over several lines is judged as the single layer it builds.
    """
    logical: List[Tuple[int, str]] = []
    buffer = ""
    start = 0

    for idx, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = idx
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        buffer += stripped
        logical.append((start, buffer.strip()))
        buffer = ""

    if buffer:
        logical.append((start, buffer.strip()))

    return logical


def _image_token(instruction: str) -> str:
    """Return the image reference from a FROM line, skipping --flags."""
    for token in instruction.split()[1:]:
        if token.startswith("--"):
            continue
        return token
    return ""


def analyze_dockerfile(content: str) -> List[Dict[str, str]]:
    issues = []
    has_user_instruction = False
    stages = 0

    for idx, clean_line in _logical_lines(content):
        upper = clean_line.upper()

        if upper.startswith("FROM"):
            stages += 1
            image = _image_token(clean_line)
            unpinned = image.endswith(":latest") or (
                ":" not in image and "@" not in image and image != "scratch"
            )
            if image and unpinned:
                issues.append({
                    "line": idx,
                    "stage": stages,
                    "rule": "PINNED_VERSION",
                    "message": "Base image uses unpinned version or ':latest'. Pin specific digests or version tags."
                })

        if upper.startswith("USER"):
            has_user_instruction = True

        lowered = clean_line.lower()
        if "apt-get install" in lowered and not any(m in lowered for m in CLEANUP_MARKERS):
            issues.append({
                "line": idx,
                "stage": stages,
                "rule": "CACHE_CLEANUP",
                "message": "apt-get install without cleaning /var/lib/apt/lists/* inflates layer size."
            })

    if not has_user_instruction:
        issues.append({
            "line": 0,
            "stage": stages,
            "rule": "LEAST_PRIVILEGE",
            "message": "No USER instruction declared. Container executes as root by default."
        })

    return issues
