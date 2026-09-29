"""A missing API key is explained before pairing, not raised as a traceback after it."""
from argparse import Namespace

from pyunto_agent import cli


def _args(**kw):
    base = dict(cmd="pair", backend="claude-api", command=None, url=None, model=None)
    base.update(kw)
    return Namespace(**base)


def test_missing_api_key_is_explained_not_raised(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli._backend_or_explain(_args()) is None
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "--no-run" in out
    assert "claude -p --output-format json" in out


def test_claude_code_is_offered_with_the_exact_command_when_installed(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/claude")
    cli._backend_or_explain(_args(cmd="run"))
    out = capsys.readouterr().out
    assert "pyunto-agent run --backend command --command 'claude -p --output-format json'" in out
    assert "--no-run" not in out  # only meaningful for pair


def test_a_usable_backend_is_returned(monkeypatch):
    backend = cli._backend_or_explain(_args(backend="command", command="cat"))
    assert backend is not None
