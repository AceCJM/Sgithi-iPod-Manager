"""Tests for the hash72 signing algorithm.

These use synthetic secrets/data (no real device data is checked into the
repo). The algorithm itself was separately validated against a real
iPhone 3G's actual database, reproducing its exact signature byte-for-byte
-- see the project README/session notes; that validation isn't repeated
here since it'd require checking in someone's real music library metadata.
"""

import os

import pytest

from ipodmanager.db import hash72
from ipodmanager.db.itunesdb import ITunesDB

from .helpers import build_empty_mhbd


def make_secret() -> hash72.DeviceSecret:
    return hash72.DeviceSecret(iv=os.urandom(16), rndpart=os.urandom(12))


def test_sign_then_extract_roundtrip():
    secret = make_secret()
    data = build_empty_mhbd()

    signed = hash72.sign(data, secret)
    # hashing_scheme should now read hash72 (2)
    import struct
    scheme = struct.unpack_from("<H", signed, 0x30)[0]
    assert scheme == hash72.CHECKSUM_TYPE_HASH72

    # a fresh consumer should be able to extract the same secret back out
    extracted = hash72.extract_secret_from_signed_db(signed)
    assert extracted.iv == secret.iv
    assert extracted.rndpart == secret.rndpart


def test_signature_changes_if_content_changes():
    secret = make_secret()
    data = build_empty_mhbd()
    signed = hash72.sign(data, secret)

    db = ITunesDB.parse(signed)
    from ipodmanager.db.itunesdb import TrackMeta, relpath_to_ipod_path
    db.add_track(TrackMeta(track_id=0, dbid=0, title="X", ipod_location=relpath_to_ipod_path("iTunes_Control/Music/F00/X.m4a")))
    modified = db.serialize()
    resigned = hash72.sign(modified, secret)

    sig1 = signed[0x72 : 0x72 + hash72.HASH72_LEN]
    sig2 = resigned[0x72 : 0x72 + hash72.HASH72_LEN]
    assert sig1 != sig2
    # but re-extracting from the new signature must still recover the same secret
    assert hash72.extract_secret_from_signed_db(resigned).iv == secret.iv


def test_hash_info_file_roundtrip(tmp_path):
    secret = make_secret()
    uuid20 = os.urandom(20)
    path = tmp_path / "HashInfo"
    hash72.save_hash_info(path, secret, uuid20)
    assert path.stat().st_size == 54

    loaded = hash72.load_hash_info(path, expected_uuid20=uuid20)
    assert loaded.iv == secret.iv
    assert loaded.rndpart == secret.rndpart

    with pytest.raises(ValueError):
        hash72.load_hash_info(path, expected_uuid20=os.urandom(20))


def test_get_or_bootstrap_secret_reads_cache_first(tmp_path):
    secret = make_secret()
    uuid20 = os.urandom(20)
    path = tmp_path / "HashInfo"
    hash72.save_hash_info(path, secret, uuid20)

    # even with garbage "existing_itdb_data", the cache should be used and
    # no extraction attempted
    got = hash72.get_or_bootstrap_secret(path, uuid20, existing_itdb_data=b"garbage")
    assert got.iv == secret.iv
    assert got.rndpart == secret.rndpart


def test_get_or_bootstrap_secret_extracts_when_no_cache(tmp_path):
    secret = make_secret()
    uuid20 = os.urandom(20)
    path = tmp_path / "HashInfo"
    assert not path.exists()

    signed = hash72.sign(build_empty_mhbd(), secret)
    got = hash72.get_or_bootstrap_secret(path, uuid20, existing_itdb_data=signed)
    assert got.iv == secret.iv
    assert got.rndpart == secret.rndpart
    assert path.exists()  # cached for next time
