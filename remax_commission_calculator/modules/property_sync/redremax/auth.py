"""Auth providers for RedREMAX. Official protocol is pending. No login automation."""

from __future__ import annotations

import os

from modules.config import is_deployed


ENV_ACCESS_TOKEN = "REDREMAX_ACCESS_TOKEN"


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

    Disabled in staging/production. Never a product auth solution.
    Never persist or display the token.
    """

    def __init__(self, token=None, *, allow_in_deployed=False):
        self._token = token
        self._allow_in_deployed = bool(allow_in_deployed)

    def is_production_ready(self):
        return False

    def is_configured(self):
        if is_deployed() and not self._allow_in_deployed:
            return False
        return bool(self._resolve())

    def get_access_token(self):
        if is_deployed() and not self._allow_in_deployed:
            return None
        return self._resolve()

    def _resolve(self):
        if self._token is not None:
            text = str(self._token).strip()
            return text or None
        return (os.environ.get(ENV_ACCESS_TOKEN) or "").strip() or None


def default_auth_provider():
    return ConfiguredRedRemaxTokenProvider()
