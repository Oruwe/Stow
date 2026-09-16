from app.refactor import refactor_dockerfile
from app.rules import analyze_dockerfile

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


def test_refactor_output_audits_clean():
    """The whole point: what refactor emits must satisfy the linter."""
    result = refactor_dockerfile(PIP_BUILD)
    assert analyze_dockerfile(result["dockerfile"]) == []


def test_apt_layer_is_hardened():
    out = refactor_dockerfile(PIP_BUILD)["dockerfile"]
    apt = next(line for line in out.splitlines() if "apt-get install" in line)
    assert "--no-install-recommends" in apt
    assert "rm -rf /var/lib/apt/lists/*" in apt


def test_dependency_layer_precedes_source_copy():
    """Editing source must not invalidate the cached dependency layer."""
    lines = refactor_dockerfile(PIP_BUILD)["dockerfile"].splitlines()
    install = next(i for i, x in enumerate(lines) if "pip install" in x)
    broad = next(i for i, x in enumerate(lines) if x.startswith("COPY . "))
    assert install < broad


def test_multistage_split_produces_builder_and_runtime():
    out = refactor_dockerfile(PIP_BUILD)["dockerfile"]
    assert "FROM python:3.11-slim AS builder" in out
    assert "COPY --from=builder /install /usr/local" in out
    assert out.count("FROM ") == 2


def test_nonroot_user_added_before_entrypoint():
    lines = refactor_dockerfile(PIP_BUILD)["dockerfile"].splitlines()
    user = next(i for i, x in enumerate(lines) if x.startswith("USER "))
    cmd = next(i for i, x in enumerate(lines) if x.startswith("CMD "))
    assert user < cmd


def test_existing_user_is_not_duplicated():
    out = refactor_dockerfile("FROM python:3.11-slim\nUSER 2000\nCMD [\"sh\"]\n")["dockerfile"]
    assert out.count("USER ") == 1
    assert "USER 2000" in out


def test_multistage_declines_without_dependency_layer():
    result = refactor_dockerfile(NO_DEPS)
    assert "MULTISTAGE_SPLIT" not in result["transformations"]
    assert any("no pip dependency layer" in n for n in result["skipped"])


def test_unpinned_base_is_reported_not_invented():
    """The agent must never fabricate a version tag it cannot know."""
    result = refactor_dockerfile(NO_DEPS)
    assert "python:latest" in result["dockerfile"]
    assert any("PINNED_VERSION" in n for n in result["skipped"])


def test_refactor_is_idempotent():
    once = refactor_dockerfile(PIP_BUILD)["dockerfile"]
    twice = refactor_dockerfile(once)["dockerfile"]
    assert once == twice


def test_already_multistage_is_left_alone(): 
    source = open("examples/Dockerfile.good").read()
    result = refactor_dockerfile(source)
    assert "MULTISTAGE_SPLIT" not in result["transformations"]
    assert analyze_dockerfile(result["dockerfile"]) == []
