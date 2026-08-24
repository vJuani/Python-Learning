import re


PASSWORD_MIN_LENGTH = 8
UPPERCASE_PATTERN = re.compile(r"[A-Z]")
LOWERCASE_PATTERN = re.compile(r"[a-z]")
DIGIT_PATTERN = re.compile(r"[0-9]")


def validate_password_policy(password, confirm_password=None):
    if password is None or password == "":
        return "err_password_required"

    if confirm_password is not None and password != confirm_password:
        return "err_password_mismatch"

    if len(password) < PASSWORD_MIN_LENGTH:
        return "err_password_short"

    if UPPERCASE_PATTERN.search(password) is None:
        return "err_password_uppercase"

    if LOWERCASE_PATTERN.search(password) is None:
        return "err_password_lowercase"

    if DIGIT_PATTERN.search(password) is None:
        return "err_password_digit"

    return None
