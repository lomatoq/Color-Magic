"""Optional studio and face-region actions, with a guarded user-facing boundary."""
import bpy
from bpy.props import EnumProperty
from .studio_ops import idle
from .guided_state import tr


def _fail(op,context,exc):
    message=str(exc)
    context.scene.color_prime.last_error=message
    op.report({'ERROR'},message)
    return {'CANCELLED'}


class COLORPRIME_OT_studio_setup(bpy.types.Operator):
    bl_idname='color_prime.studio_setup';bl_label='Create / Update Studio';bl_options={'REGISTER','UNDO'}
    bl_description='Opt-in orthographic camera, three softboxes, neutral world and transparent 1024px frame; originals retained'
    @classmethod
    def poll(cls,context):return idle(context) and getattr(context,'mode','OBJECT')=='OBJECT'
    def execute(self,context):
        try:
            from .studio_rig import create
            s=context.scene.color_prime
            create(context.scene,s)
            from .studio_rig import show_view
            show_view(context,s)
            s.last_error='';self.report({'INFO'},s.studio.rig_note)
            return {'FINISHED'}
        except Exception as exc:return _fail(self,context,exc)


class COLORPRIME_OT_studio_remove(bpy.types.Operator):
    bl_idname='color_prime.studio_remove';bl_label='Restore Original Lighting';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return idle(context) and bool(context.scene.color_prime.studio.rig_id)
    def execute(self,context):
        try:
            from .studio_rig import remove
            remove(context.scene,context.scene.color_prime)
            context.scene.color_prime.last_error=''
            self.report({'INFO'},context.scene.color_prime.studio.rig_note)
            return {'FINISHED'}
        except Exception as exc:return _fail(self,context,exc)


class COLORPRIME_OT_region_role(bpy.types.Operator):
    bl_idname='color_prime.region_role';bl_label='Assign Surface Region';bl_options={'REGISTER','UNDO'}
    family:EnumProperty(items=(('MAIN','Main',''),('ACCENT','Accent',''),('FIXED','Unchanged','')),default='ACCENT')
    source:EnumProperty(items=(('REGION','Region Number',''),('FACES','Selected Faces','')),default='REGION')
    @classmethod
    def poll(cls,context):
        obj=getattr(context,'active_object',None)
        return idle(context) and obj is not None and obj.type=='MESH' and context.scene.color_prime.studio.stage_status=='STAGED'
    def execute(self,context):
        s=context.scene.color_prime;obj=context.active_object
        was_edit=getattr(obj,'mode','OBJECT')=='EDIT'
        try:
            from .surface_adapter import REGION_ATTRIBUTE,assign_faces
            if self.source=='FACES':
                if not was_edit:raise ValueError(tr(s,'Tab to Edit Mode and select faces first.','Націсні Tab і выберы грані ў Edit Mode.'))
                import bmesh
                bm=bmesh.from_edit_mesh(obj.data);bm.faces.ensure_lookup_table();bm.faces.index_update()
                selected=[f.index for f in bm.faces if f.select]
                if not selected:raise ValueError(tr(s,'Select at least one face.','Выберы хаця б адну грань.'))
                # Flush Blender's edit representation before touching Mesh RNA.
                bpy.ops.object.mode_set(mode='OBJECT')
            else:
                if was_edit:raise ValueError('Switch to Object Mode to assign a numbered region.')
                attr=obj.data.attributes.get(REGION_ATTRIBUTE)
                if attr is None:raise ValueError('No automatic regions on this mesh. Use selected faces instead.')
                selected=[i for i,v in enumerate(attr.data) if v.value==s.studio.region_index-1]
            count=assign_faces(context.scene,s,obj,selected,self.family)
            from .discovery import scan_material_bindings
            scan_material_bindings(context.scene,s,False)
            from .child_materials import _refresh
            _refresh(context)
            s.last_error='';self.report({'INFO'},'{} faces -> {}; manual role locked'.format(count,self.family))
            return {'FINISHED'}
        except Exception as exc:return _fail(self,context,exc)
        finally:
            if was_edit and getattr(obj,'mode','OBJECT')!='EDIT':
                try:bpy.ops.object.mode_set(mode='EDIT')
                except (RuntimeError,AttributeError):pass


class COLORPRIME_OT_show_region(bpy.types.Operator):
    bl_idname='color_prime.show_region';bl_label='Show Surface Region';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        obj=getattr(context,'active_object',None)
        return idle(context) and obj is not None and obj.type=='MESH' and getattr(context,'mode','OBJECT') in {'OBJECT','EDIT_MESH'}
    def execute(self,context):
        try:
            import bmesh
            from .surface_adapter import REGION_ATTRIBUTE
            s=context.scene.color_prime;obj=context.active_object
            if obj.mode!='EDIT':
                if obj.data.attributes.get(REGION_ATTRIBUTE) is None:raise ValueError('This mesh has no automatic regions. Select faces directly.')
                bpy.ops.object.mode_set(mode='EDIT')
            bm=bmesh.from_edit_mesh(obj.data);bm.faces.ensure_lookup_table()
            layer=bm.faces.layers.int.get(REGION_ATTRIBUTE)
            if layer is None:raise ValueError('This mesh has no automatic regions. Select faces directly.')
            picked=[face for face in bm.faces if face[layer]==s.studio.region_index-1]
            if not picked:raise ValueError('No faces belong to this region number.')
            for face in bm.faces:face.select_set(False)
            for edge in bm.edges:edge.select_set(False)
            for vertex in bm.verts:vertex.select_set(False)
            bm.select_mode={'FACE'}
            for face in picked:face.select_set(True)
            bm.faces.active=picked[0]
            context.tool_settings.mesh_select_mode=(False,False,True)
            bmesh.update_edit_mesh(obj.data,loop_triangles=False,destructive=False)
            self.report({'INFO'},tr(s,'Region highlighted. Choose a face role below.','Зона вылучаная. Выберы ролю граняў ніжэй.'))
            return {'FINISHED'}
        except Exception as exc:return _fail(self,context,exc)


CLASSES=(COLORPRIME_OT_studio_setup,COLORPRIME_OT_studio_remove,COLORPRIME_OT_region_role,COLORPRIME_OT_show_region)
