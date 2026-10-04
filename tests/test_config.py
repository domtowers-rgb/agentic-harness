import os
import subprocess
import sys
from pathlib import Path

from agentic_harness.config import load_env_file

ROOT = Path(__file__).resolve().parent.parent


def test_parses_values_comments_quotes_and_export(tmp_path, monkeypatch):
    for key in ("PLAIN", "QUOTED", "SINGLE", "EXPORTED", "COMMENTED", "EMPTY", "HASH_IN_QUOTES", "URL"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "PLAIN=hello\n"
        'QUOTED="two words"\n'
        "SINGLE='x=1'\n"
        "export EXPORTED=yes\n"
        "COMMENTED=value   # trailing comment\n"
        "EMPTY=\n"
        'HASH_IN_QUOTES="a # b"\n'
        "URL=http://127.0.0.1:1234/v1#frag\n"
        "# DISABLED=nope\n"
    )
    assert load_env_file(env) == 8
    assert os.environ["PLAIN"] == "hello"
    assert os.environ["QUOTED"] == "two words"
    assert os.environ["SINGLE"] == "x=1"
    assert os.environ["EXPORTED"] == "yes"
    assert os.environ["COMMENTED"] == "value"
    assert os.environ["EMPTY"] == ""
    assert os.environ["HASH_IN_QUOTES"] == "a # b"
    assert os.environ["URL"] == "http://127.0.0.1:1234/v1#frag"  # no space before #, so not a comment
    assert "DISABLED" not in os.environ


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WINNER", "from-environment")
    env = tmp_path / ".env"
    env.write_text("WINNER=from-file\n")
    assert load_env_file(env) == 0
    assert os.environ["WINNER"] == "from-environment"


def test_bad_lines_are_reported_and_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GOOD", raising=False)
    env = tmp_path / ".env"
    env.write_text("not a setting\n1BAD=x\nGOOD=1\n")
    assert load_env_file(env) == 1
    out = capsys.readouterr().out
    assert ":1: ignoring" in out and ":2: ignoring" in out


def test_missing_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == 0


def test_override_path_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("FROM_OVERRIDE", raising=False)
    env = tmp_path / "custom.env"
    env.write_text("FROM_OVERRIDE=1\n")
    monkeypatch.setenv("AGENTIC_ENV_FILE", str(env))
    assert load_env_file() == 1


def test_importing_main_does_not_load_env(tmp_path):
    """The tests import agentic_harness.main all the time - a developer's real .env
    must never leak into them. Checked in a fresh interpreter, pointed at
    a .env that would set a marker variable if it were loaded."""
    env = tmp_path / ".env"
    env.write_text("LEAK_MARKER=leaked\n")
    code = "import os, agentic_harness.main; print(os.environ.get('LEAK_MARKER', 'clean'))"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
        env={**{k: v for k, v in os.environ.items() if k != "LEAK_MARKER"}, "AGENTIC_ENV_FILE": str(env)},
    )
    assert result.stdout.strip().splitlines()[-1] == "clean", result.stderr
