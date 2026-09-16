from app.rules import SEVERITY_ORDER, analyze_dockerfile, rule_catalog
from tests.conftest import blocking

CLEAN = """FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
HEALTHCHECK NONE
USER 1001
CMD ["python", "-m", "app"]
"""


def test_clean_dockerfile_has_no_findings():
    assert analyze_dockerfile(CLEAN) == []


def test_rule_catalog_is_complete_and_sorted_by_severity():
    catalog = rule_catalog()
    assert len(catalog) >= 14
    severities = [SEVERITY_ORDER[c["severity"]] for c in catalog]
    assert severities == sorted(severities, reverse=True)


# --- PINNED_VERSION -------------------------------------------------------


def test_latest_tag_is_flagged():
    assert "PINNED_VERSION" in blocking(analyze_dockerfile("FROM python:latest\nUSER 1\n"))


def test_untagged_image_is_flagged():
    assert "PINNED_VERSION" in blocking(analyze_dockerfile("FROM python\nUSER 1\n"))


def test_digest_and_scratch_are_pinned():
    content = "FROM alpine@sha256:abc123\nFROM scratch\nUSER 1001\n"
    assert "PINNED_VERSION" not in blocking(analyze_dockerfile(content))


def test_stage_reference_is_not_an_unpinned_image():
    """`FROM builder` names a previous stage, not a registry image."""
    content = "FROM python:3.11-slim AS builder\nFROM builder\nUSER 1001\n"
    assert "PINNED_VERSION" not in blocking(analyze_dockerfile(content))


def test_arg_interpolated_tag_is_not_flagged():
    content = "ARG VERSION\nFROM python:${VERSION}\nUSER 1001\n"
    assert "PINNED_VERSION" not in blocking(analyze_dockerfile(content))


def test_registry_port_is_not_mistaken_for_a_tag():
    content = "FROM registry.internal:5000/app\nUSER 1001\n"
    assert "PINNED_VERSION" in blocking(analyze_dockerfile(content))


# --- LEAST_PRIVILEGE ------------------------------------------------------


def test_user_in_builder_stage_does_not_protect_the_runtime_stage():
    """The shipped stage is the last one; a builder USER is security theatre."""
    content = (
        "FROM python:3.11-slim AS builder\nUSER 1001\n\n"
        "FROM python:3.11-slim\nCMD [\"x\"]\n"
    )
    assert "LEAST_PRIVILEGE" in blocking(analyze_dockerfile(content))


def test_explicit_user_root_is_flagged():
    content = "FROM python:3.11-slim\nUSER 1001\nUSER root\nCMD [\"x\"]\n"
    assert "LEAST_PRIVILEGE" in blocking(analyze_dockerfile(content))


def test_user_zero_is_root():
    assert "LEAST_PRIVILEGE" in blocking(analyze_dockerfile("FROM python:3.11-slim\nUSER 0\n"))


def test_nonroot_final_user_passes():
    content = "FROM python:3.11-slim AS b\nUSER root\n\nFROM python:3.11-slim\nUSER 1001\n"
    assert "LEAST_PRIVILEGE" not in blocking(analyze_dockerfile(content))


# --- SECRET_IN_IMAGE ------------------------------------------------------


def test_literal_secret_in_env_is_critical():
    findings = analyze_dockerfile("FROM x:1\nENV API_KEY=sk-live-abc123\nUSER 1\n")
    secret = next(f for f in findings if f["rule"] == "SECRET_IN_IMAGE")
    assert secret["severity"] == "critical"


def test_legacy_env_form_is_parsed():
    assert "SECRET_IN_IMAGE" in blocking(analyze_dockerfile("FROM x:1\nENV DB_PASSWORD hunter2\n"))


def test_build_arg_placeholder_is_not_a_baked_secret():
    content = "FROM x:1\nARG TOKEN\nENV TOKEN=${TOKEN}\nUSER 1\n"
    assert "SECRET_IN_IMAGE" not in blocking(analyze_dockerfile(content))


def test_non_secret_env_is_ignored():
    assert "SECRET_IN_IMAGE" not in blocking(analyze_dockerfile("FROM x:1\nENV APP_PORT=8080\n"))


# --- caching and size -----------------------------------------------------


def test_apt_install_without_cleanup():
    content = "FROM x:1\nRUN apt-get update && apt-get install -y curl\nUSER 1\n"
    assert "CACHE_CLEANUP" in blocking(analyze_dockerfile(content))


def test_apt_cleanup_across_continuation_is_one_layer():
    content = (
        "FROM x:1\nRUN apt-get update \\\n && apt-get install -y curl \\\n"
        " && rm -rf /var/lib/apt/lists/*\nUSER 1\n"
    )
    assert "CACHE_CLEANUP" not in blocking(analyze_dockerfile(content))


def test_isolated_apt_update_is_flagged():
    content = (
        "FROM x:1\nRUN apt-get update\n"
        "RUN apt-get install -y curl && rm -rf /var/lib/apt/lists/*\nUSER 1\n"
    )
    assert "APT_UPDATE_ISOLATED" in blocking(analyze_dockerfile(content))


def test_source_copied_before_dependency_install():
    content = "FROM x:1\nCOPY . /app\nRUN pip install --no-cache-dir -r requirements.txt\nUSER 1\n"
    assert "CACHE_ORDER" in blocking(analyze_dockerfile(content))


def test_correct_cache_order_passes():
    content = (
        "FROM x:1\nCOPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\nCOPY . /app\nUSER 1\n"
    )
    assert "CACHE_ORDER" not in blocking(analyze_dockerfile(content))


# --- shell correctness ----------------------------------------------------


def test_pipeline_without_pipefail():
    content = "FROM x:1\nRUN curl -s https://example.com | sh\nUSER 1\n"
    assert "PIPE_WITHOUT_PIPEFAIL" in blocking(analyze_dockerfile(content))


def test_logical_or_is_not_a_pipeline():
    content = "FROM x:1\nRUN make test || echo failed\nUSER 1\n"
    assert "PIPE_WITHOUT_PIPEFAIL" not in blocking(analyze_dockerfile(content))


def test_pipefail_declared_passes():
    content = "FROM x:1\nRUN set -o pipefail && curl -s https://e.com | sh\nUSER 1\n"
    assert "PIPE_WITHOUT_PIPEFAIL" not in blocking(analyze_dockerfile(content))


def test_heredoc_body_is_scanned_for_shell_issues():
    """A rule that only reads the argument would miss everything in a heredoc."""
    content = "FROM x:1\nRUN <<EOF\nsudo apt-get install -y curl\nEOF\nUSER 1\n"
    assert "NO_SUDO" in blocking(analyze_dockerfile(content))


def test_add_remote_url_is_high_severity():
    findings = analyze_dockerfile("FROM x:1\nADD https://example.com/f.sh /f.sh\nUSER 1\n")
    add = next(f for f in findings if f["rule"] == "ADD_REMOTE")
    assert add["severity"] == "high"


def test_add_of_archive_is_not_reported_as_copy_misuse():
    content = "FROM x:1\nADD payload.tar.gz /opt/\nUSER 1\n"
    assert "ADD_OVER_COPY" not in [f["rule"] for f in analyze_dockerfile(content)]


# --- disabling ------------------------------------------------------------


def test_disabled_rule_is_not_run():
    content = "FROM python:latest\nUSER 1\n"
    findings = analyze_dockerfile(content, disabled=["PINNED_VERSION"])
    assert "PINNED_VERSION" not in blocking(findings)


def test_findings_carry_line_stage_and_remediation():
    findings = analyze_dockerfile("FROM python:latest\nUSER 1\n")
    finding = next(f for f in findings if f["rule"] == "PINNED_VERSION")
    assert finding["line"] == 1
    assert finding["stage"] == 1
    assert finding["remediation"]
