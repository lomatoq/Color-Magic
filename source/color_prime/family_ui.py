"""Visible family masters, palette libraries and actual child-material usage."""
import bpy
from .guided_state import tr
from .guided_ui import lines,button
from .studio_ops import idle
from .family_links import ready,parent,shared_socket,upgrade,set_colors


def usage(material,settings,scene):
    from .utils import all_icon_objects
    from .surface_adapter import REGION_ATTRIBUTE
    count=0
    for obj in all_icon_objects(settings,scene,True):
        if obj.type!='MESH':continue
        indices={i for i,slot in enumerate(obj.material_slots) if slot.material==material}
        if not indices:continue
        if obj.mode=='EDIT':
            import bmesh
            bm=bmesh.from_edit_mesh(obj.data);layer=bm.faces.layers.int.get(REGION_ATTRIBUTE)
            count+=len({face[layer] if layer else 0 for face in bm.faces if face.material_index in indices})
        else:
            attr=obj.data.attributes.get(REGION_ATTRIBUTE)
            count+=len({attr.data[face.index].value if attr else 0 for face in obj.data.polygons if face.material_index in indices})
    return count


class COLORPRIME_OT_link_families(bpy.types.Operator):
    bl_idname='color_prime.link_families';bl_label='Connect Prime Main / Accent';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return idle(context) and context.mode=='OBJECT' and context.scene.color_prime.studio.stage_status=='STAGED'
    def execute(self,context):
        try:
            s=context.scene.color_prime
            if s.studio.adoption_pending:
                from .authored_setup import adopt
                report=adopt(context.scene,s)
            else:report=upgrade(context.scene,s)
            s.studio.workflow_block='COLORS';s.studio.color_tools='MATERIALS';s.last_error=''
            self.report({'INFO'},'{} linked materials; {} duplicate base materials consolidated.'.format(report['linked'],report['collapsed']))
            return {'FINISHED'}
        except Exception as exc:
            from .child_materials import _error
            return _error(self,context,exc)


class COLORPRIME_OT_apply_palettes(bpy.types.Operator):
    bl_idname='color_prime.apply_palettes';bl_label='Apply Selected Palette Colors';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return idle(context) and bool(context.scene.color_prime.main_colors) and bool(context.scene.color_prime.accent_colors)
    def execute(self,context):
        try:
            from .utils import active_item
            s=context.scene.color_prime
            main=active_item(s.main_colors,s.main_color_index);accent=active_item(s.accent_colors,s.accent_color_index)
            if main is None or accent is None:raise ValueError('Select one Main and one Accent color.')
            set_colors(context.scene,s,main.color,accent.color);s.last_error=''
            return {'FINISHED'}
        except Exception as exc:
            from .child_materials import _error
            return _error(self,context,exc)


class COLORPRIME_OT_show_child_parts(bpy.types.Operator):
    bl_idname='color_prime.show_child_parts';bl_label='Highlight Material Parts';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return idle(context) and context.mode=='OBJECT' and bool(context.scene.color_prime.children)
    def execute(self,context):
        from .utils import all_icon_objects,active_item
        s=context.scene.color_prime;item=active_item(s.children,s.child_index)
        if item is None or item.material is None:return {'CANCELLED'}
        objects=[o for o in all_icon_objects(s,context.scene,True) if o.type=='MESH' and any(slot.material==item.material for slot in o.material_slots)
                 and any(o.material_slots[f.material_index].material==item.material for f in o.data.polygons)]
        if not objects:self.report({'INFO'},'This material has not been assigned.');return {'CANCELLED'}
        for o in context.view_layer.objects:o.select_set(False)
        for obj in objects:obj.select_set(True)
        context.view_layer.objects.active=objects[0]
        if len(objects)==1:
            obj=objects[0];bpy.ops.object.mode_set(mode='EDIT')
            import bmesh
            bm=bmesh.from_edit_mesh(obj.data)
            for face in bm.faces:face.select_set(False)
            for edge in bm.edges:edge.select_set(False)
            for vertex in bm.verts:vertex.select_set(False)
            for face in bm.faces:
                if obj.material_slots[face.material_index].material==item.material:face.select_set(True)
            context.tool_settings.mesh_select_mode=(False,False,True)
            bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}


class COLORPRIME_OT_add_library_model(bpy.types.Operator):
    bl_idname='color_prime.add_library_model';bl_label='Add Selected Model to Material Library';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return idle(context) and context.mode=='OBJECT' and ready(context.scene.color_prime) and context.scene.color_prime.studio.stage_status!='RECOVERY'
    def execute(self,context):
        from .model_library import discover
        s=context.scene.color_prime
        try:
            discover(context.scene,s,context.selected_objects)
            result=bpy.ops.color_prime.models(action='PREPARE')
            if result=={'FINISHED'}:return bpy.ops.color_prime.workflow_regions()
            return result
        except Exception as exc:
            from .child_materials import _error
            return _error(self,context,exc)



def draw_colors(layout,context,s):
    st=s.studio
    if not ready(s) or st.adoption_pending:
        if st.adoption_pending:
            lines(layout,'Предложение семейств. Назначения и исходные цвета пока сохранены.',context)
            for binding in s.bindings:
                if binding.material:
                    row=layout.row(align=True);row.label(text=binding.material.name);row.prop(binding,'family',text='')
                    row.operator('color_prime.adoption_target',text='',icon='NODETREE').material_name=binding.material.name
            if not ready(s):
                layout.prop(s,'main_parent_material',text='Main: исходный материал')
                layout.prop(s,'accent_parent_material',text='Accent: исходный материал')
        lines(layout,tr(s,'This model needs shared family links.','Гэтай мадэлі патрэбна агульнае наследаванне.'),context)
        button(layout,'color_prime.link_families','Принять семьи и подключить наследование' if st.adoption_pending else tr(s,'Connect Prime Main / Accent','Звязаць Prime Main / Accent'),'LINKED',context.mode=='OBJECT',True)
        return
    for family in ('MAIN','ACCENT'):
        row=layout.row();row.scale_y=1.35;row.enabled=not s.preview_active
        row.prop(shared_socket(parent(s,family)),'default_value',text='Prime '+family.title())
    lines(layout,tr(s,'These two sources drive every linked material.','Гэтыя два колеры кіруюць усімі нашчадкамі.'),context)
    if s.preview_active:
        lines(layout,tr(s,'A temporary color-pair preview is active.','Актыўны часовы прагляд пары колераў.'),context)
        button(layout,'color_prime.restore_preview',tr(s,'Return to My Colors','Вярнуць мае колеры'),'LOOP_BACK')
    layout.prop(st,'color_tools',expand=True)
    if st.color_tools=='PALETTES':
        from .ui import _palette
        _palette(layout,s,'MAIN');_palette(layout,s,'ACCENT')
        layout.prop(st,'live_preview',text=tr(s,'Apply palette edits live','Адразу ўжываць змены палітры'))
        button(layout,'color_prime.apply_palettes',tr(s,'Apply Selected Colors','Ужыць выбраныя колеры'),'COLOR',large=True)
        layout.prop(s,'combination_mode',text=tr(s,'Palette combinations','Спалучэнні колераў'))
        if st.workflow_options:
            button(layout,'color_prime.guide_reference',tr(s,'Colors from Image…','Колеры з выявы…'),'FILE_FOLDER').source='FILE'
            layout.template_list('COLORPRIME_UL_looks','reference_looks',st,'looks',st,'look_index',rows=3)
            button(layout,'color_prime.select_look',tr(s,'Preview Selected Pair','Паказаць выбраную пару'),'HIDE_OFF')
        layout.prop(st,'workflow_options',text=tr(s,'Reference & color-pair previews','Рэф і прагляд пар колераў'),icon='TRIA_DOWN' if st.workflow_options else 'TRIA_RIGHT',emboss=False)
    else:
        button(layout,'color_prime.create_children',tr(s,'Create Child Materials…','Стварыць матэрыялы-нашчадкі…'),'DUPLICATE',context.mode=='OBJECT',True)
        if s.children:
            layout.template_list('COLORPRIME_UL_children','family_materials',s,'children',s,'child_index',rows=min(6,max(3,len(s.children))))
            item=s.children[s.child_index] if 0<=s.child_index<len(s.children) else None
            if item and item.material:
                lines(layout,tr(s,'Inherits: ','Наследуе: ')+parent(s,item.family).name,context,'LINKED')
                count=usage(item.material,s,context.scene)
                lines(layout,tr(s,'Assigned to {} zones / parts','Прызначаны зонам / часткам: {}').format(count),context)
                from .family_links import principled
                shader=principled(item.material)
                if shader:
                    row=layout.row(align=True)
                    row.prop(shader.inputs['Roughness'],'default_value',text=tr(s,'Roughness','Шурпатасць'))
                    row.prop(shader.inputs['Metallic'],'default_value',text=tr(s,'Metallic','Металічнасць'))
                from .material_controls import draw
                draw(layout,context,s,item.material)
                button(layout,'color_prime.show_child_parts',tr(s,'Highlight Its Parts','Паказаць яго часткі'),'FACESEL',bool(count) and context.mode=='OBJECT')
            button(layout,'color_prime.auto_assign_children',tr(s,'Assign Checked Materials…','Прызначыць адзначаныя матэрыялы…'),'MATERIAL',context.mode=='OBJECT')
        else:lines(layout,tr(s,'Base colors are assigned. Create children for different surfaces.',
            'Асноўныя колеры прызначаны. Ствары нашчадкаў для розных паверхняў.'),context)
        if context.mode=='EDIT_MESH':button(layout,'color_prime.workflow_object_mode',tr(s,'Finish Inspecting','Скончыць прагляд'),'CHECKMARK')


CLASSES=(COLORPRIME_OT_link_families,COLORPRIME_OT_apply_palettes,COLORPRIME_OT_show_child_parts,COLORPRIME_OT_add_library_model)
