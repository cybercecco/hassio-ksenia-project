"""CRC-16 checksum for the Ksenia Lares 4.0 WebSocket protocol.

The Lares panel expects every JSON message to carry a ``CRC_16`` field
whose value is a CRC-16/XMODEM checksum (polynomial 0x1021, init 0xFFFF)
computed over all the bytes up to and including the ``"CRC_16"`` field
name. The checksum must be serialized as a lowercase ``0xHHHH`` string.
"""

from __future__ import annotations


def _utf8_bytes(text: str) -> list[int]:
    """Return the UTF-8 bytes of *text* as a list of ints.

    Reproduces the byte-stream layout the panel expects (matches a JS
    ``TextEncoder`` result for every code point, including surrogate pairs).
    """
    out: list[int] = []
    i = 0
    while i < len(text):
        code = ord(text[i])
        if code < 0x80:
            out.append(code)
        elif code < 0x800:
            out.append(0xC0 | (code >> 6))
            out.append(0x80 | (code & 0x3F))
        elif code < 0xD800 or code >= 0xE000:
            out.append(0xE0 | (code >> 12))
            out.append(0x80 | ((code >> 6) & 0x3F))
            out.append(0x80 | (code & 0x3F))
        else:
            i += 1
            low = ord(text[i])
            code = 0x10000 + (((code & 0x3FF) << 10) | (low & 0x3FF))
            out.append(0xF0 | (code >> 18))
            out.append(0x80 | ((code >> 12) & 0x3F))
            out.append(0x80 | ((code >> 6) & 0x3F))
            out.append(0x80 | (code & 0x3F))
        i += 1
    return out


def crc16(message: str) -> str:
    """Compute the Ksenia CRC-16 value for *message*.

    The CRC is computed over all bytes up to (and including) the
    ``"CRC_16"`` field marker. Returns a lowercase ``0xHHHH`` string.
    """
    data = _utf8_bytes(message)
    marker = '"CRC_16"'
    cut = message.rfind(marker) + len(marker)
    # Convert character-offset to byte-offset (they differ only for
    # multibyte characters, which are rare in our JSON payloads).
    cut += len(data) - len(message)

    crc = 0xFFFF
    for byte_idx in range(cut):
        mask = 0x80
        byte = data[byte_idx]
        while mask:
            high_bit = bool(crc & 0x8000)
            crc = (crc << 1) & 0xFFFF
            if byte & mask:
                crc |= 0x01
            if high_bit:
                crc ^= 0x1021
            mask >>= 1
    return f"0x{crc:04x}"


def add_crc(json_string: str) -> str:
    """Insert the computed CRC into *json_string* (which must have a
    ``"CRC_16":"0x0000"`` placeholder) and return the finalized JSON.
    """
    marker = '"CRC_16":"'
    idx = json_string.rfind(marker) + len(marker)
    return json_string[:idx] + crc16(json_string) + '"}'
