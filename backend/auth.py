import base64
import hashlib
import hmac
import json
import os
import time

from config import DATA

def _load_secret() -> bytes:
    """SECRET_KEY (required for several replicas, which must all sign with the same key) or a generated file."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"].encode()
    path = os.path.join(DATA, ".secret")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(os.urandom(32))
    return open(path, "rb").read()


SECRET = _load_secret()


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + h.hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:  # unusable passwords ("!...", used by Google-only accounts) never match
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign(payload: dict, ttl: int) -> str:
    """HMAC-signed, expiring token. `payload["p"]` is its purpose (session, verify, reset, gstate)."""
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl}).encode())
    return body + "." + _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())


def unsign(tok: str, purpose: str):
    """Payload dict if the token is genuine, unexpired and was issued for `purpose`; else None.
    Tokens without a purpose are sessions (issued before purposes existed)."""
    try:
        body, sig = tok.split(".")
        good = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data["exp"] > time.time() and data.get("p", "session") == purpose else None
    except Exception:
        return None


def fingerprint(pw_hash: str) -> str:
    """Short digest of the stored password hash. Embedded in session and reset tokens, so changing the
    password invalidates every existing session and makes a reset link single-use."""
    return hashlib.sha256(pw_hash.encode()).hexdigest()[:16]


def make_token(uid: int, pw_hash: str, ttl: int = 7 * 86400) -> str:
    return sign({"uid": uid, "f": fingerprint(pw_hash)}, ttl)
