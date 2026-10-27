"""Public addresses and access keys for agents.

Every agent can be called at `POST /v1/agents/{slug}/chat` with a key that
belongs to that agent alone. It is deliberately not the admin key: an access
key lets its holder talk to one agent and nothing else - it cannot change any
configuration, register an MCP server, or reach a different agent.

Only a SHA-256 digest of the key is stored. That is enough here, unlike for a
password, because the key is 256 bits of randomness: there is nothing to
brute-force, so a slow hash would buy nothing. The plaintext is returned
exactly once, when the key is issued.
"""

from __future__ import annotations

import hashlib
import re
import secrets

ACCESS_KEY_PREFIX = "agk_"
SLUG_MAX_LENGTH = 80

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# An agent is looked up by id or slug, so a slug must never look like an id.
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    # Leave room for a "-NN" suffix when the name is already taken.
    return slug[: SLUG_MAX_LENGTH - 4].strip("-") or "agent"


def slug_problem(slug: str) -> str | None:
    """Why a requested slug is unusable, or None if it is fine."""
    if not 1 <= len(slug) <= SLUG_MAX_LENGTH:
        return f"The URL name must be 1-{SLUG_MAX_LENGTH} characters."
    if not _SLUG_PATTERN.match(slug):
        return (
            "The URL name may only contain lowercase letters, digits and "
            "single hyphens, and must not start or end with a hyphen."
        )
    if _UUID_PATTERN.match(slug):
        return "The URL name must not look like an agent id."
    return None


def first_free_slug(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def generate_access_key() -> tuple[str, str, str]:
    """A new key as (plaintext, digest to store, hint safe to display)."""
    key = ACCESS_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, hash_access_key(key), key[: len(ACCESS_KEY_PREFIX) + 4]


def hash_access_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def access_key_matches(stored_hash: str | None, supplied: str | None) -> bool:
    if not stored_hash or not supplied:
        return False
    return secrets.compare_digest(hash_access_key(supplied), stored_hash)
