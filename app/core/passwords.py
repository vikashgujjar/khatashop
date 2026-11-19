"""bcrypt password hashes ($2y$, the format Laravel/PHP use) with the standard library.

Checking a password is done by the fastest way available:
  1. Python's `crypt` module (Linux: the system's C bcrypt in libxcrypt) - fast, no install
  2. the `bcrypt` package, if it happens to be installed
  3. a pure-Python bcrypt (correct but slow: a few seconds per check at cost 10)
All three produce and accept the same hashes.
"""
from __future__ import annotations

import hmac
import logging
import os
import warnings

log = logging.getLogger("khatashop.passwords")

_B64 = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_B64_INDEX = {c: i for i, c in enumerate(_B64)}


def _b64_encode(data: bytes) -> str:
    out, i = [], 0
    while i < len(data):
        c1 = data[i]; i += 1
        out.append(_B64[c1 >> 2])
        c1 = (c1 & 0x03) << 4
        if i >= len(data):
            out.append(_B64[c1]); break
        c2 = data[i]; i += 1
        c1 |= c2 >> 4
        out.append(_B64[c1])
        c1 = (c2 & 0x0F) << 2
        if i >= len(data):
            out.append(_B64[c1]); break
        c2 = data[i]; i += 1
        c1 |= c2 >> 6
        out.append(_B64[c1])
        out.append(_B64[c2 & 0x3F])
    return "".join(out)


def _b64_decode(text: str, length: int) -> bytes:
    out, i = bytearray(), 0
    vals = [_B64_INDEX[c] for c in text]
    while i < len(vals) - 1 and len(out) < length:
        c1, c2 = vals[i], vals[i + 1]
        out.append(((c1 << 2) | ((c2 & 0x30) >> 4)) & 0xFF)
        if len(out) >= length or i + 2 >= len(vals):
            break
        c3 = vals[i + 2]
        out.append((((c2 & 0x0F) << 4) | ((c3 & 0x3C) >> 2)) & 0xFF)
        if len(out) >= length or i + 3 >= len(vals):
            break
        c4 = vals[i + 3]
        out.append((((c3 & 0x03) << 6) | c4) & 0xFF)
        i += 4
    return bytes(out[:length])


# ------------------------------------------------------------ pure Python --

def _pi_words(count: int) -> list[int]:
    """The first `count` 32-bit words of pi's fraction (Blowfish's initial P-array and S-boxes)."""
    bits = count * 32 + 64
    one = 1 << bits

    def arctan_inv(x):           # arctan(1/x) in fixed point
        total = term = one // x
        x2, n, sign = x * x, 1, -1
        while term:
            term //= x2
            n += 2
            total += sign * (term // n)
            sign = -sign
        return total

    pi = 16 * arctan_inv(5) - 4 * arctan_inv(239)       # Machin's formula
    frac = (pi - 3 * one) >> 64
    return [(frac >> (32 * (count - 1 - i))) & 0xFFFFFFFF for i in range(count)]


_INIT = None


def _init_state():
    global _INIT
    if _INIT is None:
        words = _pi_words(18 + 4 * 256)
        _INIT = (words[:18], [words[18 + 256 * i: 18 + 256 * (i + 1)] for i in range(4)])
    p, s = _INIT
    return list(p), [list(box) for box in s]


def _stream_words(data: bytes, count: int):
    out, j, n = [], 0, len(data)
    for _ in range(count):
        w = 0
        for _k in range(4):
            w = (w << 8) | data[j]
            j = (j + 1) % n
        out.append(w)
    return out


def _eksblowfish(cost: int, salt: bytes, key: bytes):
    P, (S0, S1, S2, S3) = _init_state()  # noqa: N806
    M = 0xFFFFFFFF  # noqa: N806

    def encrypt(l, r):  # noqa: E741
        for i in range(0, 16, 2):
            l ^= P[i]
            r ^= ((((S0[l >> 24] + S1[(l >> 16) & 255]) & M) ^ S2[(l >> 8) & 255]) + S3[l & 255]) & M
            r ^= P[i + 1]
            l ^= ((((S0[r >> 24] + S1[(r >> 16) & 255]) & M) ^ S2[(r >> 8) & 255]) + S3[r & 255]) & M
        return r ^ P[17], l ^ P[16]

    key_words = _stream_words(key, 18)
    salt_words = _stream_words(salt, 4)

    def expand(data_words):
        for i in range(18):
            P[i] ^= key_words_cur[i]
        l = r = 0  # noqa: E741
        j = 0
        for i in range(0, 18, 2):
            if data_words:
                l ^= data_words[j]; r ^= data_words[j + 1]; j = (j + 2) % len(data_words)
            l, r = encrypt(l, r)
            P[i], P[i + 1] = l, r
        for box in (S0, S1, S2, S3):
            for i in range(0, 256, 2):
                if data_words:
                    l ^= data_words[j]; r ^= data_words[j + 1]; j = (j + 2) % len(data_words)
                l, r = encrypt(l, r)
                box[i], box[i + 1] = l, r

    key_words_cur = key_words
    expand(salt_words)
    salt_as_key = _stream_words(salt, 18)
    for _ in range(1 << cost):
        key_words_cur = key_words
        expand(None)
        key_words_cur = salt_as_key
        expand(None)
    ctext = [0x4F727068, 0x65616E42, 0x65686F6C, 0x64657253, 0x63727944, 0x6F756274]  # "OrpheanBeholderScryDoubt"
    for _ in range(64):
        for i in range(0, 6, 2):
            ctext[i], ctext[i + 1] = encrypt(ctext[i], ctext[i + 1])
    return b"".join(w.to_bytes(4, "big") for w in ctext)


def _pure_hash(password: bytes, setting: str) -> str:
    prefix, cost, salt22 = setting[:4], int(setting[4:6]), setting[7:29]
    salt = _b64_decode(salt22, 16)
    key = (password + b"\x00")[:72]
    raw = _eksblowfish(cost, salt, key)
    return f"{prefix}{cost:02d}${_b64_encode(salt)[:22]}{_b64_encode(raw[:23])}"


# ------------------------------------------------------------ fast paths --

def _try_crypt():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import crypt  # noqa: PLC0415 - Unix only, removed in Python 3.13
    except ImportError:
        return None
    probe = "$2y$04$" + "abcdefghijklmnopqrstuu"
    try:
        if (crypt.crypt("x", probe) or "").startswith("$2y$04$"):
            return crypt.crypt
    except Exception:  # noqa: BLE001
        pass
    return None


def _try_bcrypt_package():
    try:
        import bcrypt  # noqa: PLC0415 - optional
        return bcrypt
    except ImportError:
        return None


_CRYPT = _try_crypt()
_BCRYPT = None if _CRYPT else _try_bcrypt_package()
BACKEND = "crypt" if _CRYPT else ("bcrypt" if _BCRYPT else "pure-python")


def _valid(stored: str) -> bool:
    return (len(stored) == 60 and stored[:4] in ("$2y$", "$2b$", "$2a$") and stored[4:6].isdigit()
            and stored[6] == "$" and all(c in _B64_INDEX for c in stored[7:]))


def _compute(password: bytes, setting: str) -> str:
    """bcrypt of password with the given $2?$NN$<22 salt chars> setting."""
    if _CRYPT:
        try:
            out = _CRYPT(password.decode("utf-8", "surrogateescape"), setting)
            if out and out.startswith(setting[:7]):
                return out
        except Exception:  # noqa: BLE001 - fall through to the other ways
            pass
    if _BCRYPT:
        s = setting.replace("$2y$", "$2b$", 1).encode()
        return _BCRYPT.hashpw(password[:72], s).decode().replace("$2b$", setting[:4], 1)  # bcrypt uses 72 bytes, like PHP
    return _pure_hash(password, setting)


def checkpw(password: str | bytes, stored_hash: str | bytes) -> bool:
    """True if the password matches. Raises ValueError for a hash that is not bcrypt."""
    if isinstance(password, str):
        password = password.encode("utf-8")
    if isinstance(stored_hash, bytes):
        stored_hash = stored_hash.decode("ascii", "replace")
    if not _valid(stored_hash):
        raise ValueError("Not a bcrypt hash")
    return hmac.compare_digest(_compute(password, stored_hash[:29]), stored_hash)


def hash_password(password: str | bytes, rounds: int = 10) -> str:
    """A new $2y$ hash (interchangeable with Laravel/PHP)."""
    if isinstance(password, str):
        password = password.encode("utf-8")
    salt = _b64_encode(os.urandom(16))[:22]
    return _compute(password, f"$2y${rounds:02d}${salt}")
