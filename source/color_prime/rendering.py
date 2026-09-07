"""Backward-compatible operator IDs; all renders use the guarded Studio engine."""
import bpy
from bpy.props import BoolProperty


def count_render_tasks(scene, settings):
    from .batch_export import color_pairs
    return (sum(i.enabled for i in settings.icon_sets) * len(color_pairs(settings)) *
            sum(r.enabled for r in settings.resolutions))


def build_render_tasks(scene, settings):
    from .batch_export import make_tasks
    return make_tasks(scene, settings)


class COLORPRIME_OT_render_variations(bpy.types.Operator):
    bl_idname = 'color_prime.render_variations'; bl_label = 'Render All'
    resume: BoolProperty(default=False)
    @classmethod
    def poll(cls, context):
        from .studio_ops import idle
        return idle(context)
    def execute(self, context):
        return bpy.ops.color_prime.studio_render('INVOKE_DEFAULT', mode='EXPORT', resume=self.resume)


class COLORPRIME_OT_pause_render(bpy.types.Operator):
    bl_idname = 'color_prime.pause_render'; bl_label = 'Pause / Resume'
    @classmethod
    def poll(cls, context):
        from .render_session import active_job
        return active_job() is not None
    def execute(self, context):
        from .render_session import active_job
        job = active_job()
        job.settings.render_paused = not job.settings.render_paused
        return {'FINISHED'}


class COLORPRIME_OT_stop_render(bpy.types.Operator):
    bl_idname = 'color_prime.stop_render'; bl_label = 'Stop After Current'
    @classmethod
    def poll(cls, context):
        from .render_session import active_job
        return active_job() is not None
    def execute(self, context):
        from .render_session import active_job
        active_job().settings.render_stop_requested = True
        return {'FINISHED'}


CLASSES = (COLORPRIME_OT_render_variations, COLORPRIME_OT_pause_render, COLORPRIME_OT_stop_render)
