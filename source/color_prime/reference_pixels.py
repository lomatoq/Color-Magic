"""Read-only adapter from a Blender pixel buffer to straight linear-sRGB RGBA.

RNA Image.pixels copies float buffers, but only divides byte buffers by 255; it
is NOT a universal scene-linear color conversion. AUTO supports the stock sRGB /
linear-Rec.709 workflow. Other input profiles require an explicit interpretation
or external conversion; they must not silently produce inaccurate colors.

This adapter is lazy: only sampled pixels are decoded, not an entire 4K image.
No Image datablock, filepath, colorspace or source pixel is modified.
"""
import math
import operator
from .color_math import clamp, srgb_to_linear_channel

_SRGB_NAMES = {'srgb', 'srgb - texture', 'utility - srgb - texture'}
_LINEAR_NAMES = {'linear', 'linear rec.709', 'linear rec709', 'linear srgb', 'scene linear',
                 'utility - linear - srgb'}


def image_buffer_encoding(image, override='AUTO'):
    override = str(override or 'AUTO').upper()
    if override in {'SRGB', 'LINEAR'}:
        return override
    if override != 'AUTO':
        raise ValueError('Unknown reference buffer encoding: ' + override)
    name = str(getattr(getattr(image, 'colorspace_settings', None), 'name', '')).strip().casefold()
    is_float = bool(getattr(image, 'is_float', False))
    if name in _SRGB_NAMES:
        # In the stock workflow a populated float ImBuf is scene-linear;
        # the byte ImBuf preserves the sRGB transfer curve.
        return 'LINEAR' if is_float else 'SRGB'
    if name in _LINEAR_NAMES:
        return 'LINEAR'
    raise ValueError(
        "Reference color space '{}' is not supported in AUTO. Convert a COPY to sRGB, "
        "or set Reference Buffer explicitly when you know the buffer encoding. "
        "This setting interprets values; it does not convert Display P3/ACES primaries.".format(name or 'unknown'))


class LinearReferencePixels:
    """Indexable RGBA view over 1-, 2-, 3- or 4-channel source pixels."""
    def __init__(self, pixels, width, height, channels=4, encoding='LINEAR', premultiplied=False):
        self._pixels = pixels
        self._count = int(width) * int(height)
        self._channels = int(channels)
        self._encoding = str(encoding).upper()
        self._premultiplied = bool(premultiplied)
        if self._count <= 0 or self._channels not in {1, 2, 3, 4}:
            raise ValueError('Unsupported reference image dimensions/channels')
        if self._encoding not in {'LINEAR', 'SRGB'}:
            raise ValueError('Reference buffer must be LINEAR or SRGB')
        if len(pixels) != self._count * self._channels:
            raise ValueError('Reference pixel buffer length does not match dimensions and channels')
        self._cached_index = -1
        self._cached_rgba = None

    def __len__(self):
        return self._count * 4

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        index = operator.index(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError('Reference pixel index out of range')
        pixel_index, channel = divmod(index, 4)
        if pixel_index != self._cached_index:
            self._decode(pixel_index)
        return self._cached_rgba[channel]

    def _decode(self, index):
        offset = index * self._channels
        values = [float(self._pixels[offset + c]) for c in range(self._channels)]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Reference contains non-finite pixel values')
        if self._channels == 1:
            rgb, alpha = values * 3, 1.0
        elif self._channels == 2:
            rgb, alpha = [values[0]] * 3, values[1]
        else:
            rgb, alpha = values[:3], values[3] if self._channels == 4 else 1.0
        alpha = clamp(alpha)
        if self._premultiplied:
            rgb = [v / alpha for v in rgb] if alpha > 1e-8 else [0.0, 0.0, 0.0]
        if self._encoding == 'SRGB':
            rgb = [srgb_to_linear_channel(v) for v in rgb]
        self._cached_index = index
        self._cached_rgba = (rgb[0], rgb[1], rgb[2], alpha)
