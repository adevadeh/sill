"""The embeddings service's image and platform are operator-overridable.

Both were hardcoded to the amd64 release line. That is the right *default* —
the versioned `cpu-N.N` tags are only published for amd64 — but it is the
wrong thing to be unchangeable, because on an arm64 host it silently commits
the operator to emulation for the life of the install. Emulated first boot
was measured at 903 s on an M-series Mac, past install.sh's default 600 s
wait, which turns the slowest step in the install into an apparent failure.

Two properties are pinned here:

1. Both fields interpolate from the environment, so `backend/.env` can point
   at the native arm64 line without editing a tracked file (which would then
   fight every `git pull`).
2. The defaults are byte-for-byte what shipped before. Nobody who does not
   set the variables sees any change at all.

Deliberately text assertions rather than `docker compose config`: the rest of
backend/tests/ needs no docker binary, and the Homebrew `docker` formula does
not even carry the compose plugin, so shelling out would skip on exactly the
machines this change is for.
"""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

# What v0.2.0 shipped, hardcoded. These are the values the defaults must
# still produce for an operator who sets nothing.
SHIPPED_IMAGE = "ghcr.io/huggingface/text-embeddings-inference:cpu-1.8"
SHIPPED_PLATFORM = "linux/amd64"


def embeddings_block() -> str:
    """The `embeddings:` service stanza, up to the next top-level service."""
    text = COMPOSE.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^  embeddings:\n(?P<body>.*?)(?=^  \w|^volumes:|\Z)", text)
    assert m, "no embeddings service found in docker-compose.yml"
    return m.group("body")


def test_the_image_is_overridable_and_defaults_to_what_shipped():
    assert f"${{EMBEDDING_IMAGE:-{SHIPPED_IMAGE}}}" in embeddings_block()


def test_the_platform_is_overridable_and_defaults_to_what_shipped():
    assert f"${{EMBEDDING_PLATFORM:-{SHIPPED_PLATFORM}}}" in embeddings_block()


def test_neither_field_is_still_hardcoded():
    """A bare `image:`/`platform:` value would mean an override silently does
    nothing — the failure this change exists to prevent, reintroduced."""
    for field in ("image", "platform"):
        m = re.search(rf"(?m)^    {field}:\s*(?P<value>\S.*)$", embeddings_block())
        assert m, f"embeddings service has no {field}:"
        assert m.group("value").startswith("${"), (
            f"embeddings.{field} is hardcoded to {m.group('value')!r}; "
            "an operator override would be ignored"
        )


def test_env_example_documents_the_arm64_alternative():
    """An override nobody can discover is not an override. The example file is
    where an operator looks, and it is what install.sh tells them to copy."""
    body = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "EMBEDDING_IMAGE" in body
    assert "EMBEDDING_PLATFORM" in body
    assert "cpu-arm64" in body, "the native arm64 tag line is not named"


def test_env_example_records_the_pinning_tradeoff():
    """The arm64 line is built from main, not from tagged releases, so moving
    to it trades a pinned version for a rolling one. An operator who is not
    told that will read `cpu-arm64-latest` as equivalent to `cpu-1.8`."""
    body = ENV_EXAMPLE.read_text(encoding="utf-8").lower()
    assert "cpu-arm64-1.8" in body or "no `cpu-arm64" in body, (
        "the absence of a version-pinned arm64 tag is not documented"
    )
