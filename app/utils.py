import secrets
import string

ALPHABET = string.ascii_letters + string.digits  # base62: a-z A-Z 0-9


def generate_short_code(length: int = 6) -> str:
    """Generate a cryptographically random base62 short code."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
