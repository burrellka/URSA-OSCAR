"""Password hashing — Phase 6.4 Decision 1.

Argon2id via passlib with default parameters (OWASP-recommended for
2025). passlib's ``argon2.using(type="ID")`` returns a hasher that
emits self-describing hash strings:

    $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>

Verification reads the parameters from the hash itself, so changes
to the default cost parameters don't break existing stored hashes.
"""
from __future__ import annotations

from passlib.hash import argon2


# Default cost parameters are well-tuned for modern hardware (passlib
# updates these in lockstep with OWASP guidance). Documented here so
# future tuning has a baseline:
#   memory_cost = 65536 KB = 64 MB
#   time_cost   = 3 iterations
#   parallelism = 4 lanes
_ARGON2 = argon2.using(type="ID")


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password. Returns the full self-describing
    Argon2id hash string suitable for storage in /data/auth.json."""
    return _ARGON2.hash(plaintext)


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Constant-time verify of a candidate password against a stored
    Argon2id hash. Returns False on malformed hash strings too —
    callers don't need to distinguish "wrong password" from "corrupt
    hash file" at the auth boundary."""
    try:
        return _ARGON2.verify(plaintext, stored_hash)
    except Exception:
        # passlib raises on malformed hash strings. Treat as a failed
        # verification — the caller's response is the same (401).
        return False
