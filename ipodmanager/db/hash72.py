"""hash72: the iTunesDB signature required by iPhone/iPod Touch/Nano 3-5G
generation devices before they'll accept a modified database.

Ported and verified line-by-line from libgpod's src/itdb_hash72.c (GNU
LGPL-2.1+, https://github.com/fadingred/libgpod) -- see that file's
hash_generate()/hash_extract() for the original C. The crypto primitive
underneath is plain AES-128-CBC, so this uses the `cryptography` package
instead of porting libgpod's bundled Rijndael implementation.

Critically, this algorithm does NOT let you compute a valid signature out
of nothing: it lets you *extract* a device-specific secret (a 16-byte IV
and 12 random bytes) from a signature the device already has -- one that
real iTunes put there during a genuine sync -- and then reuse that secret
to sign future databases as if iTunes had written them. A device that has
never been synced with real iTunes has no bootstrap material to extract,
and this module can't help there.

Confirmed against a real iPhone 3G (iOS 4.2.1, hash scheme hash72) whose
library had previously been written by a libgpod-based tool: the packaged
HashInfo file plus this module's hash_generate() reproduces the *exact*
hash72 bytes already stored in that device's live database, byte for byte.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_KEY = bytes([0x61, 0x8c, 0xa1, 0x0d, 0xc7, 0xf5, 0x7f, 0xd3, 0xb4, 0x72, 0x3e, 0x08, 0x15, 0x74, 0x63, 0xd7])

# mhbd header field offsets (see db/itunesdb.py's module docstring for how
# these were verified)
_OFF_DB_ID = 0x18
_OFF_HASH58 = 0x58
_OFF_HASH72 = 0x72
_OFF_HASHING_SCHEME = 0x30
HASH72_LEN = 46
CHECKSUM_TYPE_HASH72 = 2

HASH_INFO_MAGIC = b"HASHv0"


def _aes_ecb_block(key: bytes, block: bytes, encrypt: bool) -> bytes:
    algo = algorithms.AES(key)
    mode = modes.ECB()
    cipher = Cipher(algo, mode)
    ctx = cipher.encryptor() if encrypt else cipher.decryptor()
    return ctx.update(block) + ctx.finalize()


def _aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()


@dataclass
class DeviceSecret:
    """The per-device secret extracted from (or bootstrapped for) a real
    iTunes-signed database -- cache this and reuse it for every future
    write to this device."""

    iv: bytes  # 16 bytes
    rndpart: bytes  # 12 bytes

    def to_hash_info_bytes(self, uuid20: bytes) -> bytes:
        assert len(uuid20) == 20
        return HASH_INFO_MAGIC + uuid20 + self.rndpart + self.iv

    @classmethod
    def from_hash_info_bytes(cls, data: bytes, expected_uuid20: Optional[bytes] = None) -> "DeviceSecret":
        if len(data) != 54:
            raise ValueError(f"HashInfo must be 54 bytes, got {len(data)}")
        if data[0:6] != HASH_INFO_MAGIC:
            raise ValueError("bad HashInfo magic")
        uuid20 = data[6:26]
        if expected_uuid20 is not None and uuid20 != expected_uuid20:
            raise ValueError("HashInfo is for a different device")
        rndpart = data[26:38]
        iv = data[38:54]
        return cls(iv=iv, rndpart=rndpart)


def udid_to_bytes(udid_hex: str) -> bytes:
    return bytes.fromhex(udid_hex)


def hash_generate(sha1_20: bytes, secret: DeviceSecret) -> bytes:
    """Build the 46-byte hash72 signature for a database whose (specially
    zeroed, see compute_itunesdb_sha1) SHA1 is sha1_20."""
    assert len(sha1_20) == 20
    plaintext = sha1_20 + secret.rndpart  # 32 bytes
    ciphertext = _aes_cbc_encrypt(AES_KEY, secret.iv, plaintext)  # 32 bytes
    return bytes([0x01, 0x00]) + secret.rndpart + ciphertext


def hash_extract(signature: bytes, sha1_20: bytes) -> DeviceSecret:
    """Inverse of hash_generate: recover the device secret from a
    signature the device already has (e.g. one iTunes wrote)."""
    if len(signature) != HASH72_LEN:
        raise ValueError(f"hash72 signature must be {HASH72_LEN} bytes")
    if signature[0] != 0x01 or signature[1] != 0x00:
        raise ValueError("invalid hash72 signature prefix")
    rndpart = signature[2:14]
    ciphertext0 = signature[14:30]
    # iv = sha1[0:16] XOR AES_ECB_decrypt(ciphertext0)   (see module tests
    # for the CBC-decrypt derivation this simplifies from)
    decrypted_block = _aes_ecb_block(AES_KEY, ciphertext0, encrypt=False)
    iv = bytes(a ^ b for a, b in zip(sha1_20[0:16], decrypted_block))
    return DeviceSecret(iv=iv, rndpart=rndpart)


def compute_itunesdb_sha1(itdb_data: bytes) -> bytes:
    """SHA1 of the whole serialized database, with db_id/hash58/hash72
    zeroed first -- exactly what libgpod's itdb_hash72_compute_itunesdb_sha1
    does before signing or verifying.
    """
    if len(itdb_data) < 0x6C:
        raise ValueError("buffer too small to be an iTunesDB")
    if itdb_data[0:4] != b"mhbd":
        raise ValueError("not an iTunesDB (missing mhbd header)")
    buf = bytearray(itdb_data)
    buf[_OFF_DB_ID : _OFF_DB_ID + 8] = b"\x00" * 8
    buf[_OFF_HASH58 : _OFF_HASH58 + 20] = b"\x00" * 20
    buf[_OFF_HASH72 : _OFF_HASH72 + HASH72_LEN] = b"\x00" * HASH72_LEN
    return hashlib.sha1(bytes(buf)).digest()


def sign(itdb_data: bytes, secret: DeviceSecret) -> bytes:
    """Return a copy of itdb_data with hashing_scheme and hash72 set.

    Order matters: libgpod's itdb_hash72_write_hash sets hashing_scheme
    BEFORE computing the SHA1 (compute_itunesdb_sha1 doesn't zero that
    field), so the SHA1 that gets signed includes it. Computing the SHA1
    first would sign a different digest than a verifier recomputes from
    the final (hashing_scheme-set) buffer, silently breaking every
    signature.
    """
    buf = bytearray(itdb_data)
    struct.pack_into("<H", buf, _OFF_HASHING_SCHEME, CHECKSUM_TYPE_HASH72)
    sha1_20 = compute_itunesdb_sha1(bytes(buf))
    signature = hash_generate(sha1_20, secret)
    buf[_OFF_HASH72 : _OFF_HASH72 + HASH72_LEN] = signature
    return bytes(buf)


def extract_secret_from_signed_db(itdb_data: bytes) -> DeviceSecret:
    """Bootstrap: pull the device secret out of a database that's already
    validly signed (i.e. one a real iTunes sync produced)."""
    if itdb_data[0:4] != b"mhbd":
        raise ValueError("not an iTunesDB (missing mhbd header)")
    signature = itdb_data[_OFF_HASH72 : _OFF_HASH72 + HASH72_LEN]
    sha1_20 = compute_itunesdb_sha1(itdb_data)
    return hash_extract(signature, sha1_20)


def load_hash_info(path: Path, expected_uuid20: Optional[bytes] = None) -> DeviceSecret:
    return DeviceSecret.from_hash_info_bytes(path.read_bytes(), expected_uuid20)


def save_hash_info(path: Path, secret: DeviceSecret, uuid20: bytes) -> None:
    path.write_bytes(secret.to_hash_info_bytes(uuid20))


def get_or_bootstrap_secret(hash_info_path: Path, uuid20: bytes, existing_itdb_data: bytes) -> DeviceSecret:
    """Load the cached per-device secret, or bootstrap it by extracting it
    from a database the device already has a valid hash72 signature on
    (put there by a real iTunes sync, directly or via a prior libgpod-based
    tool) -- and cache it to hash_info_path for next time.

    Raises ValueError if there's no cached secret and existing_itdb_data
    isn't validly signed (e.g. this device has never been synced with real
    iTunes) -- there's no way to derive a fresh signature from nothing.
    """
    if hash_info_path.exists():
        try:
            return DeviceSecret.from_hash_info_bytes(hash_info_path.read_bytes(), expected_uuid20=uuid20)
        except ValueError:
            pass  # corrupt, or cached for a different device -- fall through and re-bootstrap
    secret = extract_secret_from_signed_db(existing_itdb_data)
    save_hash_info(hash_info_path, secret, uuid20)
    return secret
