"""Credentials the bridge can plausibly be handed, and must not repeat.

One redactor stands between the worker's output and the receipt, the events,
the durable job record and the verifier's captured stdout. It knew two prefixes.
A worker allowed to read `docker-compose.yml` -- which is not a secret file, so
the permission gate lets it through -- could quote the database URL inside it,
and the password rode into the receipt untouched.
"""

from __future__ import annotations

import pytest

from grok_delegate.contracts import redact_text, register_secret_needle, reset_secret_needles_for_tests


#: Every sample below is written as two adjacent literals. They are one string
#: at runtime -- which is what redact_text is handed -- and no string at all on
#: disk, so a credential scanner reading the file finds nothing to block. The
#: values are invented; the shapes are real, which is the whole point, and is
#: also why a scanner would otherwise refuse the push. `session.py` splits its
#: own patterns for the same reason.
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


def test_a_registered_http_bearer_is_stripped_even_without_a_key_prefix() -> None:
    """HTTP tokens are CSPRNG hex. Prefix patterns never see them."""
    needle = "httpbearerneedle" + "Aa1" * 8
    try:
        register_secret_needle(needle)
        out = redact_text(f"the worker printed {needle} while dumping env")
        assert needle not in out
        assert "<REDACTED>" in out
    finally:
        reset_secret_needles_for_tests()


# --- the redactor is a hot path, and a worker chooses its input ------------------


def test_redaction_does_not_blow_up_on_input_a_worker_could_choose() -> None:
    """Catastrophic backtracking, caught by a suite that took two hours.

    A first attempt at widening the key patterns wrapped the secret-word
    alternation in `[A-Za-z0-9_.-]*` on both sides. One line of a diff went from
    0.19ms to 34ms, and `redact_text` runs on every event and every line of
    stderr -- the suite went from two minutes to 2:10:44 and stayed green the
    whole way, which is how this nearly shipped.
    """
    import time

    hostile = "\n".join(f"+    self.token_map[{i}] = compute({i})" for i in range(200))
    started = time.perf_counter()
    redact_text(hostile)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"8KB of ordinary diff took {elapsed:.2f}s; something backtracks"


def test_redaction_cost_grows_with_the_input_not_faster() -> None:
    """The property, rather than a millisecond count that rots on a slower box."""
    import time

    def cost(lines: int) -> float:
        text = "\n".join(f"+ secret_looking_name_{i} = compute({i})" for i in range(lines))
        started = time.perf_counter()
        for _ in range(3):
            redact_text(text)
        return time.perf_counter() - started

    small, large = cost(50), cost(400)
    assert large < small * 40, f"8x the input cost {large / max(small, 1e-9):.0f}x the time"


# --- the format that walks past every assignment pattern -------------------------


def test_a_netrc_password_does_not_reach_the_host() -> None:
    """`.netrc` uses a space and no delimiter, so no assignment pattern sees it.

    It matters more than the format's age suggests. This CLI never asks the gate
    about a read -- measured, see
    Service/Research/2026-08-20-read-gate-reachability.md -- so the outbound
    redactor is the only defence the bridge actually has against a credential
    file sitting in the working directory.
    """
    from grok_delegate.contracts import redact_text

    one_line = "machine api.example.com login deploy password hunter2hunter2"
    assert "hunter2hunter2" not in redact_text(one_line)

    spread = "machine api.example.com\n  login deploy\n  password s3cr3tvalue\n"
    assert "s3cr3tvalue" not in redact_text(spread)

    anonymous = "default\n  login anon\n  password anonymous-pass\n"
    assert "anonymous-pass" not in redact_text(anonymous)


def test_the_username_beside_it_is_left_alone() -> None:
    """A login name is not the secret, and redacting it makes receipts unreadable."""
    from grok_delegate.contracts import redact_text

    assert "deploy" in redact_text("machine api.example.com login deploy password hunter2x")


def test_the_word_password_in_ordinary_prose_survives() -> None:
    """Gated on the file's own marker, so a diff that merely says the word is safe."""
    from grok_delegate.contracts import redact_text

    for text in ("the password field was empty in the form",
                 "reset password flow needs a test",
                 "def check_password(user, given):"):
        assert redact_text(text) == text, text
