"""Validate normalized cover PNGs without an image framework or remote fetches."""
import base64
import hashlib
import re
import struct
import zlib

MAX_COVER_BYTES = 2 * 1024 * 1024
MAX_COVER_REQUEST = 3 * 1024 * 1024


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Invalid cover ID.')


def validate_png(data, purpose='cover'):
    limit = 900000 if purpose == 'body' else MAX_COVER_BYTES
    if not isinstance(data, bytes) or not 0 < len(data) <= limit or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Cover must be a PNG of at most 2MB.')
    offset, parts, channels, ended = 8, [], None, False
    while offset + 12 <= len(data):
        size = struct.unpack('!I', data[offset:offset+4])[0]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError('Truncated PNG.')
        kind, value = data[offset+4:offset+8], data[offset+8:offset+8+size]
        crc = struct.unpack('!I', data[end-4:end])[0]
        if zlib.crc32(kind + value) & 0xffffffff != crc:
            raise ValueError('Invalid PNG checksum.')
        if offset == 8 and kind != b'IHDR':
            raise ValueError('PNG header missing.')
        if kind == b'IHDR':
            if channels is not None or len(value) != 13:
                raise ValueError('Invalid PNG header.')
            w, h, depth, color, compression, filtering, interlace = struct.unpack('!2I5B', value)
            dimensions_ok = (1 <= w <= 900 and 1 <= h <= 1800) if purpose == 'body' else (w,h)==(900,500)
            if not dimensions_ok or (depth,compression,filtering,interlace) != (8,0,0,0) or color not in (2,6):
                raise ValueError('Cover must be normalized to 900x500 RGB/RGBA PNG.')
            channels = 3 if color == 2 else 4
        elif kind == b'IDAT':
            parts.append(value)
        elif kind == b'IEND':
            if size or end != len(data):
                raise ValueError('Invalid PNG end.')
            ended = True
            break
        elif kind[:1].isupper():
            raise ValueError('Unsupported PNG chunk.')
        offset = end
    if not ended or not channels or not parts:
        raise ValueError('Incomplete PNG.')
    expected = (w * channels + 1) * h
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(b''.join(parts), expected + 1)
    except zlib.error:
        raise ValueError('Invalid PNG compression.') from None
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid PNG image data.')
    if any(raw[row * (w * channels + 1)] > 4 for row in range(h)):
        raise ValueError('Invalid PNG filter.')
    return hashlib.sha256(data).hexdigest()


def decode_upload(data, purpose='cover'):
    encoded = data.get('png_base64')
    if not isinstance(encoded, str) or len(encoded) > MAX_COVER_REQUEST:
        raise ValueError('Invalid cover payload.')
    raw = base64.b64decode(encoded, validate=True)
    return validate_png(raw, purpose), raw


def description(cover_id, raw, purpose='cover'):
    validate_id(cover_id)
    if validate_png(raw, purpose) != cover_id:
        raise ValueError('Cover integrity check failed.')
    width, height = struct.unpack('!2I', raw[16:24])
    return {'image_id' if purpose == 'body' else 'cover_id': cover_id,
            'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode(),
            'width': width, 'height': height}
