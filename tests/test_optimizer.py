from app.rules import analyze_dockerfile

SAMPLE_BAD_DOCKERFILE = """
FROM python:latest
RUN apt-get update && apt-get install -y curl
COPY . /app
WORKDIR /app
CMD ["python", "app/main.py"]
"""

SAMPLE_GOOD_DOCKERFILE = """
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
USER 1001
CMD ["python", "app/main.py"]
"""

MULTILINE_CLEAN_RUN = """FROM python:3.11-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl \\
    && rm -rf /var/lib/apt/lists/*
USER 1001
"""


def test_detects_unpinned_and_root():
    issues = analyze_dockerfile(SAMPLE_BAD_DOCKERFILE)
    rules = [i["rule"] for i in issues]
    assert "PINNED_VERSION" in rules
    assert "CACHE_CLEANUP" in rules
    assert "LEAST_PRIVILEGE" in rules


def test_clean_dockerfile_passes():
    issues = analyze_dockerfile(SAMPLE_GOOD_DOCKERFILE)
    assert len(issues) == 0


def test_continuation_lines_are_one_layer():
    """A RUN split over several physical lines is judged as the single layer it builds."""
    assert analyze_dockerfile(MULTILINE_CLEAN_RUN) == []


def test_platform_flag_does_not_false_positive():
    issues = analyze_dockerfile("FROM --platform=linux/amd64 python:3.11-slim\nUSER 1001\n")
    assert issues == []


def test_digest_and_scratch_are_pinned():
    content = "FROM alpine@sha256:abc123\nFROM scratch\nUSER 1001\n"
    assert [i["rule"] for i in analyze_dockerfile(content)] == []


def test_bare_from_does_not_crash():
    assert analyze_dockerfile("FROM\nUSER 1001\n") == []


def test_comments_and_blank_lines_ignored():
    content = "# FROM python:latest\n\nFROM python:3.11-slim\nUSER 1001\n"
    assert analyze_dockerfile(content) == []


def test_findings_report_stage_and_line():
    issues = analyze_dockerfile("FROM python:latest\n")
    pinned = next(i for i in issues if i["rule"] == "PINNED_VERSION")
    assert pinned["line"] == 1
    assert pinned["stage"] == 1
