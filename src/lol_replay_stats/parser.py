"""Binary parser for League of Legends `.rofl` replay files.

Only extracts the JSON metadata blob — we don't touch the encrypted payload
(chunks/keyframes). The scoreboard stats live in the metadata and that's all
we need for Nexus League.

Layout reference: https://github.com/fraxiinus/roflxd.cs
  - `Rofl.Extract.Data/Readers/RoflReader.cs`   (legacy)
  - `Rofl.Extract.Data/Readers/Rofl2Reader.cs`  (14.11+)
  - `Rofl.Extract.Data/Models/Rofl/Lengths.cs`

ROFL (legacy, pre-14.9)
-----------------------
  bytes 0..6     magic        b"RIOT\\x00\\x00"
  bytes 6..262   signature    (256 B, ignored)
  bytes 262..288 Lengths      (26 B, little-endian):
      262..264  uint16  headerLength
      264..268  uint32  fileLength
      268..272  uint32  metadataOffset
      272..276  uint32  metadataLength
      276..280  uint32  payloadHeaderOffset
      280..284  uint32  payloadHeaderLength
      284..288  uint32  payloadOffset
  → seek(metadataOffset), read(metadataLength) → UTF-8 JSON

ROFL2 (14.11+)
--------------
  bytes 0..6     magic         b"RIOT\\x02\\x00"
  bytes 15..29   gameVersion   (14 B UTF-8 string, e.g. "14.11.589.1234")
  last 4 bytes   metadataLen   (int32 LE)
  metadata blob  starts at (filesize - metadataLen - 4), length = metadataLen
  → UTF-8 JSON  (NOTE: ROFL2 metadata JSON does NOT contain gameVersion —
    it's read from the header instead and merged in)

JSON metadata shape (both versions)
-----------------------------------
  {
    "gameLength":     <int ms>,
    "gameVersion":    <str>,            # absent in ROFL2 raw blob
    "lastGameChunkId": <int>,
    "lastKeyFrameId": <int>,
    "statsJson":      <str>             # JSON-encoded array of 10 player dicts
  }

The `statsJson` field is itself a JSON-encoded string — needs a second
`json.loads`. Some numeric fields inside it may be encoded as strings
(scientific notation), so we coerce them lazily downstream.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, BinaryIO, Literal

ROFL_MAGIC = b"RIOT\x00\x00"
ROFL2_MAGIC = b"RIOT\x02\x00"

ROFL_HEADER_TOTAL = 288
ROFL_LENGTHS_OFFSET = 262
ROFL_LENGTHS_SIZE = 26

ROFL2_GAMEVERSION_OFFSET = 15
ROFL2_GAMEVERSION_SIZE = 14
ROFL2_METADATA_LEN_FIELD = 4  # int32 LE at end of file


class ReplayParseError(ValueError):
    """Raised when a .rofl file cannot be parsed."""


def parse_rofl(path: str | Path) -> dict[str, Any]:
    """Read a `.rofl` file and return the decoded metadata.

    Output shape:
        {
            "rofl_version": "ROFL" | "ROFL2",
            "game": {
                "gameLength": int,           # milliseconds
                "gameVersion": str,
                "lastGameChunkId": int,
                "lastKeyFrameId": int,
            },
            "players": list[dict],           # 10 entries from statsJson
        }

    Raises `ReplayParseError` on malformed files.
    """
    p = Path(path)
    if not p.is_file():
        raise ReplayParseError(f"file not found: {p}")

    with p.open("rb") as f:
        version = _detect_version(f)
        if version == "ROFL2":
            blob, game_version_from_header = _read_rofl2_metadata(f)
        else:
            blob = _read_rofl_legacy_metadata(f)
            game_version_from_header = None

    return _decode_metadata_blob(blob, version, game_version_from_header)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------


def _detect_version(stream: BinaryIO) -> Literal["ROFL", "ROFL2"]:
    stream.seek(0)
    magic = stream.read(6)
    if magic == ROFL2_MAGIC:
        return "ROFL2"
    if magic == ROFL_MAGIC:
        return "ROFL"
    raise ReplayParseError(
        f"unknown magic {magic!r} — not a .rofl file (expected RIOT\\x00\\x00 or RIOT\\x02\\x00)"
    )


# ---------------------------------------------------------------------------
# ROFL legacy
# ---------------------------------------------------------------------------


def _read_rofl_legacy_metadata(stream: BinaryIO) -> bytes:
    stream.seek(0)
    header = stream.read(ROFL_HEADER_TOTAL)
    if len(header) < ROFL_HEADER_TOTAL:
        raise ReplayParseError(
            f"ROFL header truncated: got {len(header)} bytes, expected {ROFL_HEADER_TOTAL}"
        )

    lengths_bytes = header[ROFL_LENGTHS_OFFSET : ROFL_LENGTHS_OFFSET + ROFL_LENGTHS_SIZE]
    # < = little-endian, H = uint16, I = uint32 (×6) → 2 + 4*6 = 26 bytes
    (
        _header_len,
        _file_len,
        metadata_offset,
        metadata_length,
        _payload_header_offset,
        _payload_header_len,
        _payload_offset,
    ) = struct.unpack("<HIIIIII", lengths_bytes)

    if metadata_length == 0:
        raise ReplayParseError("ROFL header reports metadataLength=0")

    stream.seek(metadata_offset)
    blob = stream.read(metadata_length)
    if len(blob) != metadata_length:
        raise ReplayParseError(
            f"metadata read short: got {len(blob)} bytes, expected {metadata_length}"
        )
    return blob


# ---------------------------------------------------------------------------
# ROFL2
# ---------------------------------------------------------------------------


def _read_rofl2_metadata(stream: BinaryIO) -> tuple[bytes, str]:
    # 1) game version (UTF-8) at fixed offset
    stream.seek(ROFL2_GAMEVERSION_OFFSET)
    raw_version = stream.read(ROFL2_GAMEVERSION_SIZE)
    if len(raw_version) != ROFL2_GAMEVERSION_SIZE:
        raise ReplayParseError("ROFL2 header truncated before gameVersion field")
    # version field is null-padded — strip trailing NULs and stray whitespace
    game_version = raw_version.rstrip(b"\x00").decode("utf-8", errors="replace").strip()

    # 2) metadata length = last 4 bytes (int32 LE)
    stream.seek(-ROFL2_METADATA_LEN_FIELD, 2)  # 2 = SEEK_END
    (metadata_length,) = struct.unpack("<i", stream.read(ROFL2_METADATA_LEN_FIELD))
    if metadata_length <= 0:
        raise ReplayParseError(f"ROFL2 metadataLength is non-positive: {metadata_length}")

    # 3) metadata blob = the metadata_length bytes preceding the length field
    stream.seek(-(metadata_length + ROFL2_METADATA_LEN_FIELD), 2)
    blob = stream.read(metadata_length)
    if len(blob) != metadata_length:
        raise ReplayParseError(
            f"ROFL2 metadata read short: got {len(blob)} bytes, expected {metadata_length}"
        )
    return blob, game_version


# ---------------------------------------------------------------------------
# JSON decoding (shared)
# ---------------------------------------------------------------------------


def _decode_metadata_blob(
    blob: bytes,
    rofl_version: Literal["ROFL", "ROFL2"],
    game_version_from_header: str | None,
) -> dict[str, Any]:
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ReplayParseError(f"metadata blob is not valid UTF-8: {e}") from e

    try:
        meta = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReplayParseError(f"metadata blob is not valid JSON: {e}") from e

    stats_json_str = meta.get("statsJson")
    if not isinstance(stats_json_str, str):
        raise ReplayParseError("metadata.statsJson is missing or not a string")

    try:
        players_raw = json.loads(stats_json_str)
    except json.JSONDecodeError as e:
        raise ReplayParseError(f"metadata.statsJson is not valid JSON: {e}") from e

    if not isinstance(players_raw, list):
        raise ReplayParseError(
            f"metadata.statsJson did not decode to a list (got {type(players_raw).__name__})"
        )

    game = {
        "gameLength": _to_int(meta.get("gameLength", 0)),
        "gameVersion": game_version_from_header or meta.get("gameVersion"),
        "lastGameChunkId": _to_int(meta.get("lastGameChunkId", 0)),
        "lastKeyFrameId": _to_int(meta.get("lastKeyFrameId", 0)),
    }

    return {
        "rofl_version": rofl_version,
        "game": game,
        "players": players_raw,
    }


def _to_int(value: Any) -> int:
    """Coerce a Riot-encoded number (sometimes a string in scientific notation) to int."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0
        try:
            return int(s)
        except ValueError:
            try:
                return int(float(s))
            except ValueError as e:
                raise ReplayParseError(f"cannot coerce {value!r} to int") from e
    raise ReplayParseError(f"cannot coerce {value!r} ({type(value).__name__}) to int")
