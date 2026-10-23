#!/usr/bin/env python3
"""Reader and writer for the "bplist15" property lists that Safari's processes send to the Web Inspector service.

The format was worked out from captured traffic of iOS 16.5, not from a specification, so only what appears in that
traffic is handled. Layout: the magic, an 8 byte integer (bit 63 set, low bits the number of bytes up to the end of the
root object), a 4 byte integer 0, then the root object. Objects:

  d<n>     dictionary of n pairs (n < 15): n keys, then n values
  a<n>     array of n items (n < 15, not seen in the traffic yet)
  7<n>     string of n bytes, UTF-8 (n < 15); 7f: the length follows as an integer object
  6<n>     UTF-16BE string of n characters (read only, as in bplist00)
  4<n>     data of n bytes (n < 15); 4f: the length follows as an integer object
  0x08 / 0x09   false / true
  0x1<k>   integer of 2**k bytes, little endian; the top bit of the last byte is a flag that is always set

    python3 -m ios_safari_mcp.inspector.bplist15 HEX     decode a frame given in hex
"""
import sys


class Bad(Exception):
    pass


def obj(b, i):
    m = b[i]
    hi, lo = m >> 4, m & 15
    if hi == 0xd:
        n = lo
        if n == 15:
            n, i = integer(b, i + 1)
            i -= 0
        else:
            i += 1
        keys = []
        for _ in range(n):
            k, i = obj(b, i)
            keys.append(k)
        out = {}
        for k in keys:
            v, i = obj(b, i)
            out[k] = v
        return out, i
    if hi == 0xa:
        n = lo
        i += 1
        if n == 15:
            n, i = integer(b, i)
        items = []
        for _ in range(n):
            v, i = obj(b, i)
            items.append(v)
        return items, i
    if hi == 6:   # UTF-16 string as in bplist00 (not seen in the traffic; read so that one odd title does not lose a listing)
        n = lo
        i += 1
        if n == 15:
            n, i = integer(b, i)
        return b[i:i + 2 * n].decode('utf-16-be', 'replace'), i + 2 * n
    if hi == 7:
        n = lo
        i += 1
        if n == 15:
            n, i = integer(b, i)
        return b[i:i + n].decode('utf-8', 'replace'), i + n
    if hi == 1:
        return integer(b, i)
    if hi == 4:  # data
        n = lo
        i += 1
        if n == 15:
            n, i = integer(b, i)
        return bytes(b[i:i + n]), i + n
    if m == 0x08:
        return False, i + 1
    if m == 0x09:
        return True, i + 1
    raise Bad(f'marker {m:#x} at {i}')


def integer(b, i):
    m = b[i]
    if m >> 4 != 1:
        raise Bad(f'integer expected at {i}, got {m:#x}')
    size = 1 << (m & 15)
    v = int.from_bytes(b[i + 1:i + 1 + size], 'little')
    v &= (1 << (size * 8 - 1)) - 1   # the top bit is a flag
    return v, i + 1 + size


def decode(blob):
    if blob[:8] != b'bplist15':
        raise Bad('magic')
    # root object: the first dictionary marker after the 22 byte preamble seen in every blob
    return obj(blob, 22)[0]


def _num(v):
    """integer object: marker 0x10 + k, then 2**k bytes, little endian; the top bit of the last byte is a flag that is
    always set (so a 1 byte integer holds 0 to 127)"""
    for k, size in enumerate((1, 2, 4, 8)):
        if v < (1 << (size * 8 - 1)):
            return bytes([0x10 + k]) + (v | (1 << (size * 8 - 1))).to_bytes(size, 'little')
    raise Bad('integer too large')


def encode_obj(o):
    if isinstance(o, bool):
        return b'\x09' if o else b'\x08'
    if isinstance(o, int):
        return _num(o)
    if isinstance(o, str):
        raw = o.encode()
        return (bytes([0x70 + len(raw)]) if len(raw) < 15 else b'\x7f' + _num(len(raw))) + raw
    if isinstance(o, (bytes, bytearray)):
        return (bytes([0x40 + len(o)]) if len(o) < 15 else b'\x4f' + _num(len(o))) + bytes(o)
    if isinstance(o, dict):
        if len(o) >= 15:
            raise Bad('dictionaries of 15 and more are not seen in the traffic yet')
        return bytes([0xd0 + len(o)]) + b''.join(encode_obj(k) for k in o) + b''.join(encode_obj(v) for v in o.values())
    raise Bad(f'cannot encode {type(o)}')


def encode(root):
    """The format the apps send: magic, an 8 byte integer (length of what follows the magic, with bit 63 set), a 4 byte
    integer 0, the root object."""
    body = encode_obj(root)
    total = 8 + 9 + 5 + len(body)
    return b'bplist15' + b'\x13' + (total | (1 << 63)).to_bytes(8, 'little') + b'\x12\x00\x00\x00\x00' + body


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(decode(bytes.fromhex(sys.argv[1])), indent=1, ensure_ascii=False, default=lambda b: b.hex()))
