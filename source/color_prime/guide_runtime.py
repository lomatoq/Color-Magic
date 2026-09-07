"""Coalesced, main-thread live color preview; no threads or scene reads at import.

Edits schedule one timer instead of recoloring RNA from a property callback.
Save/load/undo/unregister and starting a render cancel pending preview requests.
"""
import bpy

_PENDING = {}
_ARMED = False


def _allowed(scene):
    from .render_session import active_job, blender_is_rendering
    try:
        s = getattr(scene, 'color_prime', None)
        return bool(s is not None and not s.suppress_callbacks and
                    s.studio.live_preview and s.studio.ui_mode == 'GUIDED' and
                    not s.render_running and not s.studio.adoption_pending and s.studio.stage_status != 'RECOVERY' and
                    active_job() is None and not blender_is_rendering() and s.bindings)
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def request(scene,source='LOOK'):
    """Only remember the scene; resolve the currently selected look on the tick."""
    global _ARMED
    if not _allowed(scene):
        return
    timers = getattr(bpy.app, 'timers', None)
    if timers is None:
        return  # background/stub contexts can still use the explicit Preview button
    try:
        _PENDING[scene.as_pointer()] = (scene,source)
        if not _ARMED:
            timers.register(_flush, first_interval=.12)
            _ARMED = True
    except (AttributeError, ReferenceError, RuntimeError):
        _PENDING.clear()
        _ARMED = False


def _flush():
    global _ARMED
    _ARMED = False
    pending = tuple(_PENDING.values())
    _PENDING.clear()
    from .lookbook import preview
    for scene,source in pending:
        try:
            if not _allowed(scene):
                continue
            if bpy.data.scenes.get(scene.name) != scene:
                continue
            s = scene.color_prime
            if source=='PALETTE':
                from .family_links import ready,set_colors
                from .utils import active_item
                main=active_item(s.main_colors,s.main_color_index);accent=active_item(s.accent_colors,s.accent_color_index)
                if ready(s) and main and accent:set_colors(scene,s,main.color,accent.color)
            elif s.studio.looks:preview(scene, s)
            s.studio.guide_notice = ''
            wm = getattr(bpy.context, 'window_manager', None)
            for window in getattr(wm, 'windows', ()):
                for area in window.screen.areas:
                    area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError, ValueError) as exc:
            try:
                scene.color_prime.last_error = str(exc)
            except (AttributeError, ReferenceError):
                pass
    return None


def cancel_pending():
    global _ARMED
    _PENDING.clear()
    timers = getattr(bpy.app, 'timers', None)
    try:
        if timers is not None and timers.is_registered(_flush):
            timers.unregister(_flush)
    except (AttributeError, ReferenceError, RuntimeError, ValueError):
        pass
    _ARMED = False
