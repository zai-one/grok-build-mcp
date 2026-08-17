"""Credentials the bridge can plausibly be handed, and must not repeat.

One redactor stands between the worker's output and the receipt, the events,
the durable job record and the verifier's captured stdout. It knew two prefixes.
A worker allowed to read `docker-compose.yml` -- which is not a secret file, so
the permission gate lets it through -- could quote the database URL inside it,
and the password rode into the receipt untouched.
"""

from __future__ import annotations

import pytest

from grok_delegate.contracts import redact_text


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("github pat, classic", "gh" "p_PLANTEDGITHUBPAT1234567890abcd"),
        ("github pat, fine-grained", "github" "_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz"),
        ("aws access key id", "AKI" "AIOSFODNN7EXAMPLE"),
        ("aws temporary key id", "ASI" "AIOSFODNN7EXAMPLE"),
        ("slack bot token", "xox" "b-123456789012-plantedslacktoken"),
        ("gitlab pat", "glp" "at-ABCDEFGHIJKLMNOPQRST"),
        ("google api key", "AIz" "aSyD-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"),
        ("google oauth token", "ya2" "9.a0ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        ("npm token", "npm" "_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("jwt", "eyJ" "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"),
        ("xai key", "xai-abcdefghijklmnopqrst"),
        ("openai-style key", "sk-abcdefghijklmnopqrst"),
    ],
)
def test_a_bare_credential_never_reaches_the_receipt(label: str, text: str) -> None:
    assert text not in redact_text(f"the worker printed {text} while reading config")


@pytest.mark.parametrize(
    "url",
    [
        "postgres://user:s3cret@localhost/db",
        "mongodb://admin:hunter2@db:27017/app",
        "amqp://svc:tr0ub4dor@broker:5672/",
        "https://ci:deploy-token@internal.example/repo.git",
    ],
)
def test_a_password_in_a_url_is_removed_and_the_service_is_kept(url: str) -> None:
    """`key=value` was the only shape the redactor knew, and a DSN is not it."""
    out = redact_text(f"connecting to {url} now")
    secret = url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
    assert secret not in out
    assert url.split("://", 1)[0] in out, "the operator still needs to know which service"


def test_a_credential_wrapped_onto_the_next_line_is_still_a_credential() -> None:
    """A long key printed to a terminal arrives split; each half looks harmless.

    The joined form was already redacted, which is exactly why the split one was
    easy to miss: the same string leaked or not depending on the line width.
    """
    for prefix in ("sk-", "xai-", "ghp_", "glpat-"):
        wrapped = f"{prefix}\nABCDEFGHIJKLMNOPQRSTUV"
        assert "ABCDEFGHIJKLMNOPQRSTUV" not in redact_text(wrapped), prefix


def test_a_credential_split_across_two_captures_is_redacted_once_joined() -> None:
    """Why stderr is joined before redaction rather than redacted per line."""
    first, second = "sk-", "PLANTEDSPLITTOKEN1234567890"
    assert second in redact_text(first) + redact_text(second)
    assert second not in redact_text(first + second)


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/zai-one/grok-mcp",
        "see tests/test_env.py for the fixture",
        "the sk- prefix is short",
        "http://localhost:8080/healthz",
        "def add(a, b):\n    return a + b",
        "diff --git a/app.py b/app.py",
        "AKIA is a prefix",
    ],
)
def test_ordinary_output_is_left_alone(text: str) -> None:
    """A redactor that eats the diff is a redactor nobody will keep enabled."""
    assert redact_text(text) == text
