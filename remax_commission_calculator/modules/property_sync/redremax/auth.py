"""Auth providers for RedREMAX. Official protocol is pending. No login automation."""

from __future__ import annotations

import os


ENV_ACCESS_TOKEN = "REDREMAX_ACCESS_TOKEN"
BEARER_PREFIX = "bearer "


def normalize_access_token(raw):
    """Return the raw credential. Strip an accidental 'Bearer ' prefix."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text[:7].lower() == BEARER_PREFIX:
        text = text[7:].strip()
    return text or None


def env_token_configured():
    return bool(normalize_access_token(os.environ.get(ENV_ACCESS_TOKEN)))


class RedRemaxAuthProvider:
    """Interface only. Implementations must never log the token."""

    def get_access_token(self):
        raise NotImplementedError

    def is_configured(self):
        return False

    def is_production_ready(self):
        return False


class RedRemaxOfficialAuthProvider(RedRemaxAuthProvider):
    """Placeholder for the future official API/OAuth flow. Do not invent a protocol."""

    def get_access_token(self):
        raise NotImplementedError("redremax_official_auth_pending")

    def is_configured(self):
        return False

    def is_production_ready(self):
        return False


class ConfiguredRedRemaxTokenProvider(RedRemaxAuthProvider):
    """NON-PRODUCTION. Reads REDREMAX_ACCESS_TOKEN from the environment.

    Never a product auth solution. Never persist or display the token.
    Official auth remains pending behind RedRemaxOfficialAuthProvider.
    """

    def __init__(self, token=None, *, allow_in_deployed=False):
        self._token = token
        self._allow_in_deployed = bool(allow_in_deployed)

    def is_production_ready(self):
        return False

    def is_configured(self):
        return bool(self.get_access_token())

    def get_access_token(self):
        return self._resolve()

    def _resolve(self):
        if self._token is not None:
            return normalize_access_token(self._token)
        return normalize_access_token(os.environ.get(ENV_ACCESS_TOKEN))


def default_auth_provider():
    return ConfiguredRedRemaxTokenProvider()
