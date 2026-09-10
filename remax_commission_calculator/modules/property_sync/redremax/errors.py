"""RedREMAX connector errors. Never include tokens or payloads."""


class RedRemaxError(Exception):
    def __init__(self, message_key, status_code=400, *, safe_message=None):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code
        self.safe_message = safe_message


class RedRemaxAuthError(RedRemaxError):
    def __init__(self, message_key="redremax_err_auth", status_code=401, *, safe_message=None):
        super().__init__(message_key, status_code, safe_message=safe_message)


class RedRemaxConfigError(RedRemaxError):
    def __init__(self, message_key="redremax_err_office_required"):
        super().__init__(message_key, 400)


class RedRemaxPartialError(RedRemaxError):
    """A later page failed. Collected items are safe to inspect, not to archive-from."""

    def __init__(self, items, *, page, pages_fetched, source_total=None):
        super().__init__("redremax_err_partial_page", 502)
        self.items = list(items or [])
        self.page = page
        self.pages_fetched = pages_fetched
        self.source_total = source_total
