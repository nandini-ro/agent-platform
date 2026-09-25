"""Per-agent provider API keys, encrypted at rest.

An agent's provider key is entered once in the agent form, encrypted here with
AES-256-GCM under a single server-side master key (CREDENTIAL_ENCRYPTION_KEY),
and stored only as ciphertext. It is decrypted server-side, just before the
agent's provider client is built, and handed to nothing else - not the model,
not tools, not MCP servers, and never back out through the API.

Every ciphertext is bound to the agent and provider it was entered for through
GCM's associated data. A key copied onto another agent's row, or read back
after the agent was switched to a different provider, fails authentication
rather than decrypting - so a Groq key can never be sent to Gemini, and one
agent can never run on another agent's credential.
"""

from __future__ import annotations

import base64
import binascii
import os

from app.config.settings import get_settings
from app.services.llm.base import ProviderNotConfigured

# Prefix on every stored value, so the scheme can change later without
# guessing what an existing row was written with.
_VERSION = "v1"
_NONCE_BYTES = 12
MAX_API_KEY_LENGTH = 1024


class CredentialError(ProviderNotConfigured):
    """A credential cannot be stored or used. The message is always safe to
    show: it never contains key material."""


def generate_master_key() -> str:
    """A fresh value for CREDENTIAL_ENCRYPTION_KEY."""
    return base64.b64encode(os.urandom(32)).decode()


def clean_api_key(raw: str | None) -> str | None:
    """Normalise a submitted key. Blank means "none supplied".

    Raises ValueError with a message that never echoes the input.
    """
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_API_KEY_LENGTH:
        raise ValueError("The API key is too long.")
    if any(c.isspace() or not c.isprintable() for c in key):
        raise ValueError("The API key must not contain spaces or line breaks.")
    return key


def encrypt_api_key(api_key: str, *, agent_id: str, provider: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(_master_key()).encrypt(
        nonce, api_key.encode(), _associated_data(agent_id, provider)
    )
    return f"{_VERSION}:{base64.b64encode(nonce + sealed).decode()}"


def decrypt_api_key(token: str, *, agent_id: str, provider: str) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    unreadable = CredentialError(
        f"The stored API key for provider '{provider}' could not be decrypted. "
        "Re-enter it in the agent's configuration."
    )
    version, _, body = token.partition(":")
    if version != _VERSION:
        raise unreadable
    try:
        blob = base64.b64decode(body, validate=True)
        plain = AESGCM(_master_key()).decrypt(
            blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], _associated_data(agent_id, provider)
        )
    except (InvalidTag, ValueError, binascii.Error):
        raise unreadable from None
    return plain.decode()


def resolve_api_key(agent) -> str | None:
    """The decrypted key for this agent's *current* provider, or None.

    None when the agent has no credential, or only one saved for a different
    provider; the provider then raises its own "not configured" error when a
    call is attempted.
    """
    if not agent.api_key_encrypted or agent.api_key_provider != agent.provider:
        return None
    return decrypt_api_key(
        agent.api_key_encrypted, agent_id=agent.id, provider=agent.provider
    )


def _associated_data(agent_id: str, provider: str) -> bytes:
    return f"agent-credential|{agent_id}|{provider}".encode()


def _master_key() -> bytes:
    configured = get_settings().credential_encryption_key
    if not configured:
        raise CredentialError(
            "The server has no CREDENTIAL_ENCRYPTION_KEY configured, so provider "
            "API keys cannot be stored or used. Ask the administrator to set it."
        )
    try:
        # Accept both standard and URL-safe base64.
        value = configured.strip().replace("-", "+").replace("_", "/")
        key = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except (ValueError, binascii.Error):
        key = b""
    if len(key) != 32:
        raise CredentialError(
            "CREDENTIAL_ENCRYPTION_KEY must be 32 bytes, base64-encoded "
            "(generate one with: openssl rand -base64 32)."
        )
    return key
