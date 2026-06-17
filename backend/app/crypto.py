"""At-rest encryption for per-user provider API keys (e.g. OpenRouter).

Keys are symmetrically encrypted with Fernet (AES-128-CBC + HMAC) using
KEY_ENCRYPTION_KEY from the environment, so the SQLite DB never stores a usable
provider key in plaintext. Losing/rotating KEY_ENCRYPTION_KEY makes every stored
key undecryptable — users simply reconnect. Treat it like SESSION_SECRET.

Never log plaintext keys.
"""
from cryptography.fernet import Fernet, InvalidToken

from . import config

_fernet = Fernet(config.KEY_ENCRYPTION_KEY.encode())


def encrypt(plaintext: str) -> str:
    """Encrypt a provider key. Returns an opaque urlsafe token to store in the DB."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    """Decrypt a stored token back to the provider key, or None if it can't be
    decrypted (wrong/rotated KEY_ENCRYPTION_KEY, corruption). Callers treat None
    as 'not connected' and prompt the user to reconnect."""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
