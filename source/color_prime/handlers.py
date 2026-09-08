"""Lifecycle recovery. Registration only appends handlers; it never reads bpy.data."""
import bpy
from bpy.app.handlers import persistent
from .compat import iter_scenes
from .constants import SCHEMA_VERSION, VERSION_STRING
from .runtime import clear_runtime_state, restore_preview, restore_all_previews
from .utils import log


def _reset_scene_runtime(scene):
    s = getattr(scene, 'color_prime', None)
    if s is None:
        return
    try:
        s.suppress_callbacks = False
        s.render_running = False
        s.render_paused = False
        s.render_stop_requested = False
        s.render_current = 0
        s.render_total = 0
        s.studio.preview_running = False
        s.schema_version = SCHEMA_VERSION
        s.addon_version = VERSION_STRING
        restore_preview(scene)
    except (AttributeError, ReferenceError, RuntimeError) as exc:
        log('Runtime reset: '+str(exc), 'WARNING')


@persistent
def load_pre(_):
    from .material_names import cancel_surface_names
    cancel_surface_names()
    from .guide_runtime import cancel_pending
    cancel_pending()
    from .render_session import shutdown
    shutdown()
    clear_runtime_state()


@persistent
def load_post(_):
    from .scene_guard import recover
    clear_runtime_state()
    for scene in iter_scenes():
        errors = recover(scene)
        _reset_scene_runtime(scene)
        from .default_palette import ensure
        ensure(scene)
        if errors:
            scene.color_prime.last_error = 'Render recovery: ' + '; '.join(errors)
        st = scene.color_prime.studio
        # Persistent object/material pointers remain the actual rollback source.
        if st.stage_status != 'NONE':
            st.stage_note = 'Working setup restored from file. Accept Parts or Revert Setup.'


@persistent
def save_pre(_):
    from .guide_runtime import cancel_pending
    cancel_pending()
    from .render_session import active_job
    job = active_job()
    if job is not None and job.guard is not None:
        # Do not edit scene data while the renderer owns it. Save original-state
        # journal with the .blend so loading this file can restore its baseline.
        job.guard.persist()
        return
    restore_all_previews()
    from .material_names import update_surface_names
    for scene in iter_scenes():
        if hasattr(scene,'color_prime'):update_surface_names(scene.color_prime)


@persistent
def undo_post(_):
    from .guide_runtime import cancel_pending
    cancel_pending()
    from .render_session import active_job
    if active_job() is not None:
        active_job().stop_requested = True
        return
    clear_runtime_state()
    for scene in iter_scenes():
        s = getattr(scene, 'color_prime', None)
        if s is not None:
            try:
                # Undo restores RNA snapshots along with colors. Preserve that
                # durable restoration source rather than forgetting the preview.
                s.preview_active = bool(s.studio.preview_values)
            except (AttributeError, ReferenceError, RuntimeError):
                pass


@persistent
def material_update(scene,depsgraph):
    if not hasattr(scene,'color_prime') or scene.color_prime.render_running:return
    if any(isinstance(update.id,bpy.types.Mesh) for update in depsgraph.updates):
        from .appearance_workspace import _status_counts
        _status_counts.clear()
    if any(isinstance(update.id,(bpy.types.Material,bpy.types.ShaderNodeTree)) for update in depsgraph.updates):
        from .material_names import queue_surface_names
        queue_surface_names()


_HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, material_update),
    (bpy.app.handlers.load_pre, load_pre),
    (bpy.app.handlers.load_post, load_post),
    (bpy.app.handlers.save_pre, save_pre),
    (bpy.app.handlers.undo_post, undo_post),
)


def register_handlers():
    for collection, fn in _HANDLERS:
        if fn not in collection:
            collection.append(fn)


def unregister_handlers():
    from .material_names import cancel_surface_names
    cancel_surface_names()
    for collection, fn in _HANDLERS:
        if fn in collection:
            collection.remove(fn)
