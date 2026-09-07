"""Streaming PNG integrity validation before publishing an output file.

Checks chunk CRCs, dimensions, zlib EOF and exact decompressed scanline size.
Memory is bounded independently of the image dimensions. This is validation of
Blender's PNG output, not a general-purpose image decoder.
"""
import struct
import zlib


def _row_bytes(width, bits_per_pixel):
    return (width * bits_per_pixel + 7) // 8 + 1


def _decoded_size(width, height, channels, bit_depth, interlace):
    if not interlace:
        return height * _row_bytes(width, channels * bit_depth)
    total = 0
    for x0, y0, dx, dy in ((0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)):
        w = max(0, (width-x0+dx-1)//dx); h = max(0, (height-y0+dy-1)//dy)
        if w and h:
            total += h * _row_bytes(w, channels*bit_depth)
    return total


def validate_png(path, expected_width=None, expected_height=None):
    saw_header = saw_data = saw_end = False
    decoded = expected = 0
    decoder = zlib.decompressobj()
    width = height = 0
    with open(path, 'rb') as stream:
        if stream.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Invalid PNG signature')
        while not saw_end:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('Truncated PNG chunk header')
            length, kind = struct.unpack('>I4s', header)
            if not saw_header and kind != b'IHDR':
                raise ValueError('PNG header is not first')
            if kind == b'IHDR' and (saw_header or length != 13):
                raise ValueError('Invalid or repeated PNG header')
            crc = zlib.crc32(kind)
            remaining = length
            ihdr = bytearray()
            while remaining:
                block = stream.read(min(remaining, 1024*1024))
                if not block:
                    raise ValueError('Truncated PNG chunk data')
                remaining -= len(block); crc = zlib.crc32(block, crc)
                if kind == b'IHDR':
                    ihdr.extend(block)
                elif kind == b'IDAT':
                    if not saw_header:
                        raise ValueError('Image data before PNG header')
                    saw_data = True
                    pending = block
                    while pending:
                        output = decoder.decompress(pending, 1024*1024)
                        decoded += len(output)
                        if decoded > expected:
                            raise ValueError('PNG contains more scanline data than its dimensions allow')
                        pending = decoder.unconsumed_tail
                        if decoder.unused_data:
                            raise ValueError('Unexpected trailing data in PNG compressed stream')
            raw_crc = stream.read(4)
            if len(raw_crc) != 4 or struct.unpack('>I', raw_crc)[0] != (crc & 0xffffffff):
                raise ValueError('PNG chunk CRC mismatch')
            if kind == b'IHDR':
                width,height,depth,color,compression,filter_method,interlace = struct.unpack('>IIBBBBB',ihdr)
                channels = {0:1,2:3,3:1,4:2,6:4}.get(color)
                allowed = {0:{1,2,4,8,16},2:{8,16},3:{1,2,4,8},4:{8,16},6:{8,16}}
                if not width or not height or channels is None or depth not in allowed[color] or compression or filter_method or interlace not in (0,1):
                    raise ValueError('Unsupported or invalid PNG header')
                if expected_width is not None and (width,height) != (expected_width,expected_height):
                    raise ValueError('PNG dimensions do not match the frozen task')
                expected = _decoded_size(width,height,channels,depth,interlace)
                saw_header = True
            elif kind == b'IEND':
                if length or not saw_data:
                    raise ValueError('Invalid PNG end chunk')
                saw_end = True
        if stream.read(1):
            raise ValueError('Unexpected data after PNG end chunk')
    if not decoder.eof or decoded != expected:
        raise ValueError('Truncated PNG compressed data or scanline count mismatch')
    return width,height
