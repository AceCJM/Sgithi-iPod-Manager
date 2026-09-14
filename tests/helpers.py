import struct


def build_empty_mhbd() -> bytes:
    """A minimal, format-correct mhbd with one empty tracks + one empty
    playlists section, laid out exactly like mk_mhbd()/mk_mhsd() in
    libgpod (244-byte mhbd header, 96-byte mhsd headers).
    """
    header = bytearray()
    header += struct.pack("<4sIIII", b"mhbd", 244, 0, 1, 0x13)
    header += struct.pack("<I", 2)  # num_children
    header += struct.pack("<Q", 0)  # db_id
    header += struct.pack("<HH", 1, 0)  # platform, unk_0x22
    header += struct.pack("<Q", 0)  # id_0x24
    header += struct.pack("<I", 0)  # unk_0x2c
    header += struct.pack("<H", 0)  # hashing_scheme
    header += b"\x00" * 20  # unk_0x32
    header += b"\x00" * 2  # language_id
    header += struct.pack("<Q", 0)  # db_persistent_id
    header += struct.pack("<II", 0, 0)  # unk_0x50, unk_0x54
    header += b"\x00" * 20  # hash58
    header += struct.pack("<i", 0)  # timezone_offset
    header += struct.pack("<H", 0)  # unk_0x70
    header += b"\x00" * 46  # hash72
    header += struct.pack("<HHHHH", 0, 0, 0, 0, 0)
    header += b"\x00"  # align
    header += b"\x00" * 57  # hashAB
    header += b"\x00" * 16  # dummy space
    assert len(header) == 244

    mhsd_tracks = struct.pack("<4siii", b"mhsd", 96, 96 + 92, 1) + b"\x00" * 80
    mhsd_tracks += struct.pack("<4sii", b"mhlt", 92, 0) + b"\x00" * 80

    mhsd_playlists = struct.pack("<4siii", b"mhsd", 96, 96 + 92, 2) + b"\x00" * 80
    mhsd_playlists += struct.pack("<4sii", b"mhlp", 92, 0) + b"\x00" * 80

    body = bytes(mhsd_tracks) + bytes(mhsd_playlists)
    struct.pack_into("<I", header, 8, len(header) + len(body))
    return bytes(header) + body
