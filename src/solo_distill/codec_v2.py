"""Opt-in lossless core transport. No source selection or semantic normalization."""
from __future__ import annotations

import base64
import binascii
import zlib

from .core_v2 import canonical

ENCODING = "zlib-base64-core-v2-1"
MAX_DECODED_BYTES = 1048576


def pack_core(value):
    raw = canonical(value)
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError("core_decoded_budget")
    return {"encoding": ENCODING, "decoded_bytes": len(raw),
            "data": base64.b64encode(zlib.compress(raw, 9)).decode("ascii")}


def unpack_core(value, loader):
    """Bound expansion before JSON parsing; reject truncated/concatenated streams."""
    if type(value) is not dict or "encoding" not in value:
        return value
    if set(value) != {"encoding", "decoded_bytes", "data"} or value["encoding"] != ENCODING:
        raise ValueError("unsupported_core_encoding")
    size, data = value["decoded_bytes"], value["data"]
    if type(size) is not int or not 1 <= size <= MAX_DECODED_BYTES or type(data) is not str:
        raise ValueError("invalid_core_encoding_fields")
    if len(data) > 4 * ((MAX_DECODED_BYTES + 1024 + 2) // 3):
        raise ValueError("encoded_core_budget")
    try:
        compressed = base64.b64decode(data.encode("ascii"), validate=True)
        if base64.b64encode(compressed).decode("ascii") != data:
            raise ValueError("noncanonical_base64")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, size + 1)
        if (len(raw) != size or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail):
            raise ValueError("core_expansion_or_stream_conflict")
        result = loader(raw)
        if canonical(result) != raw:
            raise ValueError("noncanonical_decoded_core")
        return result
    except (binascii.Error, UnicodeError, zlib.error) as error:
        raise ValueError("invalid_compressed_core") from error
