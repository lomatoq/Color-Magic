"""Append the bundled scooter without replacing the scene."""
from pathlib import Path
import bpy

class COLORPRIME_OT_sample_scooter(bpy.types.Operator):
    bl_idname='color_prime.sample_scooter';bl_label='Add test scooter';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        from .studio_ops import idle
        return context.mode=='OBJECT' and idle(context)
    def execute(self,context):
        path=Path(__file__).parent/'assets'/'Scooter.blend'
        if not path.is_file():
            self.report({'ERROR'},'Bundled scooter is missing. Install the complete ZIP.');return {'CANCELLED'}
        with bpy.data.libraries.load(str(path),link=False) as (source,target):
            target.collections=['Scooter'] if 'Scooter' in source.collections else []
        if not target.collections:
            self.report({'ERROR'},'The sample has no Scooter collection.');return {'CANCELLED'}
        coll=target.collections[0];context.scene.collection.children.link(coll)
        context.view_layer.update()
        objects=[bpy.data.objects[o.name] for o in coll.all_objects]
        for obj in objects:
            obj.hide_render=False;obj.hide_viewport=False;obj.hide_set(False)
        from .model_library import discover
        discover(context.scene,context.scene.color_prime,selected=objects)
        self.report({'INFO'},'Scooter added with 23 editable surface zones.')
        return {'FINISHED'}

CLASSES=(COLORPRIME_OT_sample_scooter,)
