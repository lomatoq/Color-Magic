"""Bridge between Blender Image datablocks and the pure palette engine."""
from typing import Optional

from .palette_engine import analyze_rgba_pixels
from .reference_pixels import LinearReferencePixels, image_buffer_encoding


def analyze_reference_image(image, settings):
    if image is None:
        raise ValueError('Choose a reference image first')
    try:
        width, height = int(image.size[0]), int(image.size[1])
    except Exception:
        width, height = 0, 0
    if width <= 0 or height <= 0:
        try:
            image.reload()
            width, height = int(image.size[0]), int(image.size[1])
        except Exception:
            pass
    if width <= 0 or height <= 0:
        raise ValueError('Reference image has no loaded pixel buffer')
    raw_pixels = image.pixels
    if raw_pixels is None:
        raise ValueError('Reference image pixel buffer is unavailable')
    encoding = image_buffer_encoding(image, getattr(settings, 'reference_input_encoding', 'AUTO'))
    alpha_mode = str(getattr(image, 'alpha_mode', '')).upper()
    buffer_alpha = str(getattr(settings, 'reference_buffer_alpha', 'AUTO')).upper()
    # Blender's file alpha_mode is not itself a guarantee of buffer layout.
    # Native byte ImBuf is straight; native float ImBuf is premultiplied, except
    # channel-packed/no-alpha data. Explicit overrides cover generated buffers.
    premultiplied = (bool(getattr(image, 'is_float', False)) and alpha_mode not in {'CHANNEL_PACKED', 'NONE'}
                     if buffer_alpha == 'AUTO' else buffer_alpha == 'PREMUL')
    pixels = LinearReferencePixels(raw_pixels, width, height,
                                   channels=int(getattr(image, 'channels', 4)),
                                   encoding=encoding, premultiplied=premultiplied)
    from .spatial_reference import sample_reference
    studio = getattr(settings,'studio',None)
    spatial = sample_reference(width, height, pixels, budget=settings.reference_max_samples,
        roi=getattr(studio,'reference_roi',(0,0,1,1)),
        alpha_threshold=settings.reference_alpha_threshold,
        remove_border=bool(getattr(studio,'reference_spatial_background',True)) and settings.reference_background_mode != 'KEEP',
        tolerance=getattr(studio,'reference_background_tolerance',.045),
        force=settings.reference_background_mode == 'FORCE_BORDER')
    analysis = analyze_rgba_pixels(
        spatial['width'],
        spatial['height'],
        spatial['pixels'],
        max_samples=settings.reference_max_samples,
        cluster_count=settings.reference_cluster_count,
        alpha_threshold=settings.reference_alpha_threshold,
        clean_strength=settings.reference_clean_strength,
        background_mode='KEEP',
        premultiplied=False,
    )
    settings.reference_pairs.clear()
    for proposal in analysis.proposals:
        item = settings.reference_pairs.add()
        item.name = proposal.name
        item.variant = proposal.variant
        item.main_color = proposal.main
        item.accent_color = proposal.accent
        item.score = proposal.score
        item.confidence = proposal.confidence
        item.main_coverage = proposal.main_coverage
        item.accent_coverage = proposal.accent_coverage
        item.reason = proposal.reason
    settings.reference_pair_index = 0 if settings.reference_pairs else -1
    settings.reference_last_summary = analysis.summary + '; ' + spatial['reason'] + '; buffer: ' + encoding + (' premultiplied' if premultiplied else ' straight')
    return analysis


def selected_reference_pair(settings):
    index = int(settings.reference_pair_index)
    return settings.reference_pairs[index] if 0 <= index < len(settings.reference_pairs) else None


def apply_pair_to_palettes(settings, pair=None, append=False):
    pair = pair or selected_reference_pair(settings)
    if pair is None:
        return False

    def put(collection, index_property, name, color):
        if append or not collection:
            item = collection.add()
            item.name = name
            item.enabled = True
            item.color = color
            setattr(settings, index_property, len(collection) - 1)
        else:
            index = getattr(settings, index_property)
            if not 0 <= index < len(collection):
                index = 0
            item = collection[index]
            item.name = name
            item.enabled = True
            item.color = color
            setattr(settings, index_property, index)

    suffix = pair.name or 'Reference'
    put(settings.main_colors, 'main_color_index', '{} Main'.format(suffix), pair.main_color)
    put(settings.accent_colors, 'accent_color_index', '{} Accent'.format(suffix), pair.accent_color)
    settings.palette_initialized_from_scene = True
    return True
