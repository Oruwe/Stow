from pathlib import Path

from app.refactor import refactor_dockerfile
from app.rules import analyze_dockerfile
from tests.conftest import blocking

PIP_BUILD = """FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
RUN apt-get update && apt-get install -y curl
CMD ["python", "-m", "app.main"]
"""

NO_DEPS = """FROM python:latest
RUN apt-get update && apt-get install -y curl
COPY . /app
CMD ["python", "-m", "app.main"]
"""


def test_refactor_output_clears_every_blocking_finding():
    """The point of the engine: what it emits must pass the gate."""
    result = refactor_dockerfile(PIP_BUILD)
    assert blocking(analyze_dockerfile(str(result["dockerfile"]))) == []


def test_apt_layer_is_hardened():
    out = str(refactor_dockerfile(PIP_BUILD)["dockerfile"])
    apt = next(line for line in out.splitlines() if "apt-get install" in line)
    assert "--no-install-recommends" in apt
    assert "rm -rf /var/lib/apt/lists/*" in apt


def test_pip_cache_flag_added():
    out = str(refactor_dockerfile(PIP_BUILD)["dockerfile"])
    assert all("--no-cache-dir" in line for line in out.splitlines() if "pip install" in line)


def test_dependency_layer_precedes_source_copy():
    lines = str(refactor_dockerfile(PIP_BUILD)["dockerfile"]).splitlines()
    install = next(i for i, x in enumerate(lines) if "pip install" in x)
    broad = next(i for i, x in enumerate(lines) if x.startswith("COPY . "))
    assert install < broad


def test_multistage_split_produces_builder_and_runtime():
    out = str(refactor_dockerfile(PIP_BUILD)["dockerfile"])
    assert "FROM python:3.11-slim AS builder" in out
    assert "COPY --from=builder /install /usr/local" in out
    assert out.count("FROM ") == 2


def test_nonroot_user_added_before_entrypoint():
    lines = str(refactor_dockerfile(PIP_BUILD)["dockerfile"]).splitlines()
    user = next(i for i, x in enumerate(lines) if x.startswith("USER "))
    cmd = next(i for i, x in enumerate(lines) if x.startswith("CMD "))
    assert user < cmd


def test_existing_nonroot_user_is_not_duplicated():
    out = str(refactor_dockerfile('FROM python:3.11-slim\nUSER 2000\nCMD ["sh"]\n')["dockerfile"])
    assert out.count("USER ") == 1
    assert "USER 2000" in out


def test_explicit_root_in_final_stage_is_rewritten():
    source = 'FROM python:3.11-slim\nUSER root\nCMD ["sh"]\n'
    result = refactor_dockerfile(source)
    assert "LEAST_PRIVILEGE_DEROOT" in result["transformations"]
    assert "USER root" not in str(result["dockerfile"])


def test_builder_stage_user_does_not_satisfy_the_runtime_stage():
    source = 'FROM python:3.11-slim AS b\nUSER 1001\n\nFROM python:3.11-slim\nCMD ["sh"]\n'
    result = refactor_dockerfile(source)
    assert "LEAST_PRIVILEGE_USER" in result["transformations"]
    assert str(result["dockerfile"]).count("USER ") == 2


def test_add_rewritten_to_copy_for_local_path():
    result = refactor_dockerfile("FROM x:1\nADD ./cfg /etc/cfg\nUSER 1\n")
    assert "ADD_TO_COPY" in result["transformations"]
    assert "COPY ./cfg /etc/cfg" in str(result["dockerfile"])


def test_add_of_remote_url_is_left_alone():
    """Rewriting a remote ADD to COPY would silently break the build."""
    result = refactor_dockerfile("FROM x:1\nADD https://e.com/f.sh /f.sh\nUSER 1\n")
    assert "ADD_TO_COPY" not in result["transformations"]
    assert "ADD https://e.com/f.sh /f.sh" in str(result["dockerfile"])


def test_multistage_declines_without_dependency_layer():
    result = refactor_dockerfile(NO_DEPS)
    assert "MULTISTAGE_SPLIT" not in result["transformations"]
    assert any("no pip dependency layer" in n for n in result["skipped"])


def test_unpinned_base_is_reported_not_invented():
    """The agent must never fabricate a version tag it cannot know."""
    result = refactor_dockerfile(NO_DEPS)
    assert "python:latest" in str(result["dockerfile"])
    assert any("PINNED_VERSION" in n for n in result["skipped"])


def test_parser_directives_survive_the_rewrite():
    """Dropping `# syntax=` would silently change which frontend builds the image."""
    result = refactor_dockerfile("# syntax=docker/dockerfile:1\nFROM x:1\nUSER 1\n")
    assert str(result["dockerfile"]).startswith("# syntax=docker/dockerfile:1")


def test_refactor_is_idempotent():
    once = str(refactor_dockerfile(PIP_BUILD)["dockerfile"])
    twice = str(refactor_dockerfile(once)["dockerfile"])
    assert once == twice


def test_already_optimal_file_is_unchanged():
    source = Path("examples/Dockerfile.good").read_text(encoding="utf-8")
    result = refactor_dockerfile(source)
    assert "MULTISTAGE_SPLIT" not in result["transformations"]
    assert blocking(analyze_dockerfile(str(result["dockerfile"]))) == []


MULTILINE_CLEAN = """FROM python:3.11-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl \\
    && rm -rf /var/lib/apt/lists/*
HEALTHCHECK NONE
USER 1001
"""


def test_untouched_multiline_run_keeps_its_formatting():
    """Collapsing a hand-formatted RUN would make --write churn every diff."""
    result = refactor_dockerfile(MULTILINE_CLEAN)
    assert result["transformations"] == []
    assert str(result["dockerfile"]) == MULTILINE_CLEAN


def test_check_is_clean_when_no_transform_fires():
    result = refactor_dockerfile(MULTILINE_CLEAN)
    assert str(result["dockerfile"]) == MULTILINE_CLEAN


def test_modified_multiline_run_is_still_rewritten():
    """Preserving formatting must not stop a needed fix being applied."""
    source = (
        "FROM python:3.11-slim\n"
        "RUN apt-get update \\\n    && apt-get install -y curl\nUSER 1001\n"
    )
    result = refactor_dockerfile(source)
    assert "APT_CACHE_CLEANUP" in result["transformations"]
    assert "rm -rf /var/lib/apt/lists/*" in str(result["dockerfile"])


def test_heredoc_instruction_survives_a_rewrite():
    source = "FROM python:3.11-slim\nRUN <<EOF\necho hello\nEOF\nUSER 1001\n"
    assert "echo hello" in str(refactor_dockerfile(source)["dockerfile"])
