"""Audio transcoding via ffmpeg.

Classic click-wheel iPods (and the iOS 4.2.1-era Music app) cannot decode
FLAC at all -- the firmware only understands MP3/AAC/ALAC/WAV/AIFF/Audible.
So "upload a FLAC in a way the iPod can natively use" has to mean
transcoding it to ALAC (Apple Lossless), which preserves the original
lossless audio quality bit-for-bit while using a container/codec the
device actually supports.

`to_aac` is a second, optional path used by the "convert to AAC 320" space-
saving setting: for a source that's better than 320kbps AAC (lossless, or a
higher-bitrate lossy format), re-encode down to AAC 320kbps instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class TranscodeError(RuntimeError):
    pass


def flac_to_alac(src: Path, dst: Path) -> None:
    """Transcode FLAC to ALAC (.m4a), carrying over tags and any embedded
    cover art (ffmpeg exposes a FLAC's embedded picture as an attached
    video stream, which we copy through unchanged).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-map", "0:a",
        "-map", "0:v?",
        "-c:a", "alac",
        "-c:v", "copy",
        "-disposition:v", "attached_pic",
        "-movflags", "+faststart",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscodeError(f"ffmpeg failed transcoding {src} -> {dst}:\n{proc.stderr[-4000:]}")


def to_aac(src: Path, dst: Path, bitrate_kbps: int = 320) -> None:
    """Transcode/re-encode anything ffmpeg can decode to AAC (.m4a) at a
    fixed bitrate, for the optional "shrink to AAC 320" quality setting.
    Cover art (if any) is carried over the same way as flac_to_alac.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-map", "0:a",
        "-map", "0:v?",
        "-c:a", "aac", "-b:a", f"{bitrate_kbps}k",
        "-c:v", "copy",
        "-disposition:v", "attached_pic",
        "-movflags", "+faststart",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TranscodeError(f"ffmpeg failed transcoding {src} -> {dst}:\n{proc.stderr[-4000:]}")
