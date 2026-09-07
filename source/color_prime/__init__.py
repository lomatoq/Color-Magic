bl_info = {
    "name": "Color Prime Studio 3.5.2",
    "author": "Color Prime contributors",
    "version": (3, 5, 2),
    "blender": (3, 0, 0),
    "location": "3D View > Sidebar > Color Prime; Output Properties > Color Prime",
    "description": "Offline reference-palette extraction, automatic material roles, inheritance and batch icon rendering",
    "category": "Render",
}

# Blender reloads the package entry point, not its children. An upgrade from
# 3.0.x could therefore pair the NEW scene_guard with OLD compat in sys.modules.
# This bootstrap deliberately precedes every relative import.
def _prepare_bundle():
    import hashlib
    import importlib
    import json
    from pathlib import Path
    import sys
    import types

    root = Path(__file__).resolve().parent
    manifest_path = root / 'package_integrity.json'
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            bad = []
            for filename, expected in manifest['files'].items():
                path = root / filename
                if (Path(filename).name != filename or
                        not path.is_file() or
                        hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                    bad.append(filename)
            if bad:
                raise ValueError('Different or missing files: ' + ', '.join(bad[:6]))
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
            raise ImportError(
                'Color Prime installation is mixed or incomplete. '
                'Close Blender, remove the old Color Prime add-on in Preferences, '
                'then install the complete new ZIP. Your .blend files are not changed. '
                + str(exc)
            ) from exc

    prefix = __name__ + '.'
    children = [name for name in tuple(sys.modules) if name.startswith(prefix)]
    if not children:
        return
    session = sys.modules.get(prefix + 'render_session')
    if session is not None:
        active = getattr(session, 'active_job', None)
        if callable(active) and active() is not None:
            raise RuntimeError(
                'Color Prime is still rendering or restoring a scene. '
                'Stop its render, let scene restoration finish, and restart Blender '
                'before updating. No running modules were replaced.'
            )
    # Script reloads can occur while the old entry point is registered. Use its
    # own cleanup first; never unregister another add-on's classes by name.
    if globals().get('_REGISTERED', False):
        previous_unregister = globals().get('unregister')
        if not callable(previous_unregister):
            raise RuntimeError('Disable Color Prime and restart Blender before updating.')
        previous_unregister()
        if globals().get('_REGISTERED', False):
            raise RuntimeError('Color Prime cleanup is pending. Restart Blender after rendering.')

    # Remove only OUR package's children and their root attributes. Clearing
    # sys.modules alone is insufficient: `from . import foo` can reuse foo on
    # the parent package even when the sys.modules entry has been deleted.
    for name in sorted(children, key=len, reverse=True):
        sys.modules.pop(name, None)
    for key, value in tuple(globals().items()):
        if isinstance(value, types.ModuleType) and value.__name__.startswith(prefix):
            globals().pop(key, None)
    importlib.invalidate_caches()


_prepare_bundle()

import bpy
from bpy.props import PointerProperty
from . import handlers, operators, properties, rendering, runtime, ui, studio_ops, studio_ui, guided_ops, guided_ui, model_ops, child_materials, workflow_ui, family_ui, export_ui, model_library, material_controls, palette_sets, selected_colors, appearance_workspace
from .constants import VERSION_STRING

_REGISTERED = False
_REGISTERED_CLASSES = []
_DEFERRED_UNREGISTER = False


def _register_class(cls):
    bpy.utils.register_class(cls)
    _REGISTERED_CLASSES.append(cls)


def _rollback_registration():
    handlers.unregister_handlers()
    if hasattr(bpy.types.Scene, "color_prime"):
        try:
            del bpy.types.Scene.color_prime
        except Exception:
            pass
    while _REGISTERED_CLASSES:
        cls = _REGISTERED_CLASSES.pop()
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def register():
    """Register without touching bpy.data.

    Blender intentionally exposes a restricted bpy.data while add-ons are being
    enabled.  Scene/object/material datablocks must therefore only be accessed
    later from operators or file handlers, never from register().
    """
    global _REGISTERED
    if _REGISTERED:
        return
    # A separately installed Extension and a legacy add-on must not overwrite
    # each other's Scene pointer or RNA registrations.
    if hasattr(bpy.types.Scene, 'color_prime'):
        raise RuntimeError('Another Color Prime copy is enabled. Disable it and restart Blender, then enable this version.')
    try:
        for cls in properties.CLASSES:
            _register_class(cls)
        bpy.types.Scene.color_prime = PointerProperty(type=properties.ColorPrimeSettings)
        for cls in operators.CLASSES + rendering.CLASSES + studio_ops.CLASSES + ui.CLASSES + studio_ui.CLASSES + guided_ops.CLASSES + guided_ui.CLASSES + model_ops.CLASSES + child_materials.CLASSES + workflow_ui.CLASSES + family_ui.CLASSES + export_ui.CLASSES + model_library.CLASSES + material_controls.CLASSES + palette_sets.CLASSES + selected_colors.CLASSES + appearance_workspace.CLASSES:
            if issubclass(cls,bpy.types.Panel) and cls not in studio_ui.CLASSES:continue
            if issubclass(cls,bpy.types.Operator):
                from .workspace_locale import operator_description
                cls.description=classmethod(operator_description)
            _register_class(cls)
        handlers.register_handlers()
        from .tooltip_locale import sync_language
        if not bpy.app.timers.is_registered(sync_language):bpy.app.timers.register(sync_language,first_interval=.1,persistent=True)
        _REGISTERED = True
        print("Color Prime {} registered on Blender {}".format(VERSION_STRING, getattr(bpy.app, "version_string", "unknown")))
    except Exception:
        _rollback_registration()
        _REGISTERED = False
        raise


def unregister():
    from .updater import shutdown as stop_update
    stop_update()
    from .tooltip_locale import sync_language
    if bpy.app.timers.is_registered(sync_language):bpy.app.timers.unregister(sync_language)
    from .tooltip_locale import clear
    clear()
    global _REGISTERED, _DEFERRED_UNREGISTER
    from .render_session import active_job, blender_is_rendering
    job = active_job()
    if job is not None and blender_is_rendering():
        job.stop_requested = True
        if not _DEFERRED_UNREGISTER:
            def finish_unregister():
                global _DEFERRED_UNREGISTER
                if blender_is_rendering():
                    return .25
                _DEFERRED_UNREGISTER = False
                unregister()
                return None
            bpy.app.timers.register(finish_unregister, first_interval=.25)
            _DEFERRED_UNREGISTER = True
        print('Color Prime: disabling deferred until the active render ends safely')
        return
    from .guide_runtime import cancel_pending
    cancel_pending()
    from .render_session import shutdown
    shutdown()
    handlers.unregister_handlers()
    # restore_all_previews is itself restriction-safe; if Blender is currently
    # in restricted registration state it simply has no scenes to traverse.
    try:
        runtime.restore_all_previews()
    except Exception:
        runtime.clear_runtime_state()
    if hasattr(bpy.types.Scene, "color_prime"):
        try:
            del bpy.types.Scene.color_prime
        except Exception:
            pass
    had_registered_classes = bool(_REGISTERED_CLASSES)
    while _REGISTERED_CLASSES:
        cls = _REGISTERED_CLASSES.pop()
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    # Fallback for normal Blender module reloads where this module-level list
    # may have been recreated but Blender still owns the classes.
    if not had_registered_classes:
        for cls in reversed(properties.CLASSES + operators.CLASSES + rendering.CLASSES + studio_ops.CLASSES + ui.CLASSES + studio_ui.CLASSES + guided_ops.CLASSES + guided_ui.CLASSES + model_ops.CLASSES + child_materials.CLASSES + workflow_ui.CLASSES + family_ui.CLASSES + export_ui.CLASSES + model_library.CLASSES + material_controls.CLASSES + palette_sets.CLASSES + selected_colors.CLASSES + appearance_workspace.CLASSES):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    _REGISTERED = False
