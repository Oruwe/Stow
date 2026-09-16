from app.parser import image_reference, parse


def test_joins_continuations_into_one_instruction():
    doc = parse("FROM python:3.11-slim\nRUN apt-get update \\\n    && apt-get install -y curl\n")
    runs = [i for i in doc.instructions if i.keyword == "RUN"]
    assert len(runs) == 1
    assert runs[0].argument == "apt-get update && apt-get install -y curl"


def test_comment_inside_continuation_does_not_end_instruction():
    """Docker drops embedded comments; a line-based scanner would split here."""
    doc = parse("FROM x:1\nRUN a \\\n    # explain\n    && b\n")
    assert [i.argument for i in doc.instructions if i.keyword == "RUN"] == ["a && b"]


def test_escape_directive_switches_continuation_character():
    doc = parse("# escape=`\nFROM x:1\nRUN a `\n    && b\n")
    assert doc.directives["escape"] == "`"
    assert [i.argument for i in doc.instructions if i.keyword == "RUN"] == ["a && b"]


def test_syntax_directive_is_captured():
    doc = parse("# syntax=docker/dockerfile:1\nFROM x:1\n")
    assert doc.directives["syntax"] == "docker/dockerfile:1"


def test_heredoc_body_is_consumed_not_parsed_as_instructions():
    source = "FROM x:1\nRUN <<EOF\napt-get update\n" "FROM not-an-instruction\nEOF\nUSER 1001\n"
    doc = parse(source)
    assert [i.keyword for i in doc.instructions] == ["FROM", "RUN", "USER"]
    assert "FROM not-an-instruction" in doc.instructions[1].heredoc


def test_stages_are_indexed_and_final_marked():
    doc = parse("FROM x:1 AS build\nFROM y:2\n")
    assert [s.alias for s in doc.stages] == ["build", None]
    assert doc.stages[-1].is_final is True
    assert doc.stages[0].is_final is False


def test_image_reference_skips_platform_flag():
    reference = image_reference("--platform=linux/amd64 python:3.11 AS build")
    assert reference == ("python:3.11", "build")


def test_bare_from_is_tolerated():
    doc = parse("FROM\n")
    assert doc.stages[0].base == ""


def test_unterminated_continuation_does_not_drop_instruction():
    doc = parse("FROM x:1\nRUN echo hi \\\n")
    assert [i.keyword for i in doc.instructions] == ["FROM", "RUN"]
