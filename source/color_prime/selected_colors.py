"""One-click opt-in color inheritance for selected objects or faces."""
import bpy
from bpy.props import EnumProperty
from .transaction import OWNER_KEY
from .studio_ops import idle


def selection(context):
    if context.mode=='EDIT_MESH':
        import bmesh
        result={}
        # Blender puts only one user of a linked Mesh into objects_in_mode.
        # Explicitly selected instances share its face selection; unselected
        # instances must continue using the original mesh and material.
        candidates=set(context.objects_in_mode)|set(context.selected_objects)
        for obj in candidates:
            if obj.type!='MESH' or not obj.data.is_editmode:continue
            bm=bmesh.from_edit_mesh(obj.data);bm.faces.ensure_lookup_table();bm.faces.index_update()
            indices=[f.index for f in bm.faces if f.select and not f.hide]
            if indices:result[obj]=indices
        return result
    from .guided_state import mesh_descendants
    return {obj:list(range(len(obj.data.polygons))) for root in context.selected_objects
            for obj in mesh_descendants(root) if not obj.get('cp_studio_rig_owner') and obj.data.polygons}


def _source_material(obj,index):
    return obj.material_slots[index].material if index<len(obj.material_slots) else None


def _local_tree(mat,path):
    tree=mat.node_tree
    for name in path:
        node=tree.nodes[name];node.node_tree=node.node_tree.copy()
        node.node_tree[OWNER_KEY]=mat.get(OWNER_KEY,'');tree=node.node_tree
    return tree


def _link(mat,settings,family):
    from .family_links import color_source,family_node,parent
    from .authored_setup import _replace_source
    source=color_source(mat);group=family_node(parent(settings,family)).node_tree
    if source:
        # Clone only this material's path before replacing its source. Preserve
        # imported correction nodes and never change shared authored groups.
        native=source.tree.get('cp_family_role') in {'MAIN','ACCENT'}
        _local_tree(mat,source.group_path)
        # A native family's current palette color is not a material tint.
        # Recalibrating blue against a new red parent would zero its red channel.
        # Keep authored downstream corrections but do not bake the old palette.
        _replace_source(mat,color_source(mat),group,preserve_color=not native)
    else:
        from .shader_endpoints import endpoints
        target,tree,path,_=endpoints(mat)[0]
        _local_tree(mat,path);target,tree,_,_=endpoints(mat)[0]
        original=target.links[0].from_socket if target.is_linked else None
        node=tree.nodes.new('ShaderNodeGroup');node.node_tree=group;node['cp_family_link']=True
        output=node.outputs['Color'];base=tuple(group.nodes['Color'].outputs[0].default_value)
        for mode,value in (('MULTIPLY',tuple(1/v if abs(v)>1e-6 else 1 for v in base[:3])+(1.,)),
                           ('ADD',tuple(0 if abs(v)>1e-6 else 1-v for v in base[:3])+(1.,))):
            correct=tree.nodes.new('ShaderNodeMixRGB');correct.blend_type=mode;correct.use_clamp=False
            correct['cp_family_shade']=True;correct.inputs[0].default_value=1;correct.inputs[2].default_value=value
            tree.links.new(output,correct.inputs[1]);output=correct.outputs[0]
        mix=tree.nodes.new('ShaderNodeMixRGB');mix.name='Prime Texture Tint';mix.blend_type='MULTIPLY';mix.use_clamp=False
        mix['cp_selection_texture_tint']=True;mix.inputs[0].default_value=1
        if original:tree.links.new(original,mix.inputs[1])
        else:mix.inputs[1].default_value=target.default_value
        tree.links.new(output,mix.inputs[2]);tree.links.new(mix.outputs[0],target)
    mat['cp_adopted']=True


def assign(context,picked,family,force_private=False,refresh=True):
    """Atomic preflight, then copy-on-write scoped mesh/face assignment."""
    from . import transaction
    from .family_links import color_source,color_input,parent,ensure_parent,disconnect
    from .utils import data_block_identity,objects_from_icon_item
    from .shader_endpoints import endpoints
    scene=context.scene;s=scene.color_prime;st=s.studio
    if not picked:raise ValueError('Выдели детали или грани, которым нужно назначить цвет.')
    if st.stage_status=='RECOVERY':raise ValueError('Сначала восстанови незавершённую операцию.')
    for obj,indices in picked.items():
        if obj.library or obj.override_library or obj.data.library:raise ValueError('Сначала сделай выбранную модель локальной: '+obj.name)
        if any(i<0 or i>=len(obj.data.polygons) for i in indices):raise ValueError('Выделение граней изменилось.')
        for i in {obj.data.polygons[n].material_index for n in indices}:
            mat=_source_material(obj,i)
            if mat and family!='FIXED' and mat.use_nodes and len(endpoints(mat))!=1:
                raise ValueError('У материала «'+mat.name+'» несколько шейдеров: укажи цветовой вход в настройках материала.')
    from .runtime import restore_preview
    restore_preview(scene)
    if s.preview_active:raise ValueError('Не удалось восстановить текущий просмотр цветов.')
    before=transaction.snapshot_settings(s);started=st.stage_status=='NONE'
    prior_slots={o:[(slot.link,slot.material) for slot in o.material_slots] for o in picked}
    mats_before={m.as_pointer() for m in bpy.data.materials};groups_before={g.as_pointer() for g in bpy.data.node_groups}
    # Retain operation-local assignments for errors inside an existing stage.
    indices_before={o:[p.material_index for p in o.data.polygons] for o in picked}
    try:
        st.reuse_family_library=True
        if started:transaction.start(scene,s,before,objects=list(picked))
        else:transaction.extend(scene,s,list(picked))
        # The transaction protects the mesh. Only selected face materials need
        # new IDs; keep all other effective slots exactly as they were.
        for obj,slots in prior_slots.items():
            for slot,(link,mat) in zip(obj.material_slots,slots):slot.link=link;slot.material=mat
        from .model_library import discover
        if refresh:discover(scene,s,list(picked))
        for item in s.icon_sets:
            if set(objects_from_icon_item(item,scene))&set(picked):item.selective_colors=True
        if family!='FIXED':
            current=parent(s,family)
            if current is None or not current.get('cp_family_master',''):
                mat=next((_source_material(o,o.data.polygons[ids[0]].material_index) for o,ids in picked.items()),None)
                src=color_source(mat) if mat else None
                reference=tuple(src.socket.default_value) if src else (1.,1.,1.,1.)
                setattr(s,'main_reference_color' if family=='MAIN' else 'accent_reference_color',reference)
                setattr(s,'main_parent_material' if family=='MAIN' else 'accent_parent_material',None)
            ensure_parent(s,family)
            ensure_parent(s,'ACCENT' if family=='MAIN' else 'MAIN')
        made={};count=0
        for obj,indices in picked.items():
            mesh=obj.data;slots={}
            # Slot zero also represents unassigned faces on a fresh mesh.
            # Reserve it before appending a selective material.
            if not mesh.materials:mesh.materials.append(None)
            for index in {mesh.polygons[i].material_index for i in indices}:
                source=_source_material(obj,index)
                if not force_private and source and source.get('cp_selective_child') and source.get('color_prime_family')==family:
                    slots[index]=index;continue
                identity=data_block_identity(source) if source else '__empty__'
                key=(identity,family)
                mat=made.get(key)
                if mat is None and not force_private:
                    mat=next((c.material for c in s.children if c.material and c.material.get('cp_selection_source')==identity
                              and c.material.get('cp_selective_child') and c.family==family),None)
                if mat is None:
                    mat=source.copy() if source else bpy.data.materials.new('Selected Color')
                    if not mat.use_nodes:
                        color=tuple(mat.diffuse_color);mat.use_nodes=True
                        color_input(mat).default_value=color
                    mat[OWNER_KEY]=st.stage_id
                    for keyname in ('cp_family_master','color_prime_parent','color_prime_generated_name'):
                        if keyname in mat:del mat[keyname]
                    if family=='FIXED':disconnect(mat)
                    else:_link(mat,s,family)
                    mat['cp_selective_child']=True;mat['cp_selection_source']=identity
                    mat['color_prime_family']=family;mat['color_prime_family_source']='MANUAL';mat['color_prime_locked']=True
                    mat['color_prime_child']=family!='FIXED';mat['color_prime_child_profile']='SAME'
                    from .material_names import name_material
                    name_material(mat,family)
                    if family!='FIXED':
                        item=s.children.add();item.material=mat;item.family=family;item.profile='SAME'
                made[key]=mat
                slot=next((i for i,v in enumerate(obj.material_slots) if v.material==mat),None)
                if slot is None:mesh.materials.append(mat);slot=len(mesh.materials)-1
                slots[index]=slot
            for index in indices:mesh.polygons[index].material_index=slots[mesh.polygons[index].material_index];count+=1
            obj['color_prime_authored_slots']=True;mesh.update()
        from .discovery import scan_material_bindings
        localize=s.auto_localize_legacy;s.auto_localize_legacy=False
        try:
            if refresh:scan_material_bindings(scene,s,False)
        finally:s.auto_localize_legacy=localize
        st.adoption_pending=False;st.workflow_block='ASSIGN'
        st.assignment_summary='Назначено: {} граней · {} деталей → {}'.format(count,len(picked),family.title() if family!='FIXED' else 'цвет закреплён')
        s.last_error='';transaction.tag_created(s,mats_before,groups_before)
        return count
    except Exception:
        if started and st.stage_status!='NONE':
            transaction.tag_created(s,mats_before,groups_before);transaction.rollback(scene,s)
        else:
            for obj,indices in indices_before.items():
                for p,value in zip(obj.data.polygons,indices):p.material_index=value
                while len(obj.data.materials)>len(prior_slots[obj]):obj.data.materials.pop(index=len(obj.data.materials)-1)
                for slot,(link,mat) in zip(obj.material_slots,prior_slots[obj]):slot.link=link;slot.material=mat
            transaction.restore_settings(s,before)
        raise


class COLORPRIME_OT_selected_color(bpy.types.Operator):
    bl_idname='color_prime.selected_color';bl_label='Цвет выбранных частей';bl_options={'REGISTER','UNDO'}
    bl_description='Подключить только выделенные детали или грани; сохранить исходный вид. Ctrl+Z — отмена'
    family:EnumProperty(items=(('MAIN','Менять с Main',''),('ACCENT','Менять с Accent',''),('FIXED','Оставить цвет','')),default='MAIN')
    @classmethod
    def poll(cls,context):return idle(context) and context.mode in {'OBJECT','EDIT_MESH'} and context.scene.color_prime.studio.stage_status!='RECOVERY'
    def execute(self,context):
        was_edit=context.mode=='EDIT_MESH'
        try:
            picked=selection(context)
            if was_edit:bpy.ops.object.mode_set(mode='OBJECT')
            assign(context,picked,self.family)
            self.report({'INFO'},context.scene.color_prime.studio.assignment_summary)
            return {'FINISHED'}
        except Exception as exc:
            context.scene.color_prime.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}
        finally:
            if was_edit and context.mode!='EDIT_MESH':bpy.ops.object.mode_set(mode='EDIT')


def draw(layout,context,s):
    from .guided_ui import lines
    lines(layout,'Выдели детали или грани → выбери, как менять цвет.',context)
    row=layout.row(align=True)
    for family,label in (('MAIN','Менять с Main'),('ACCENT','Менять с Accent')):
        row.operator('color_prime.selected_color',text=label).family=family
    layout.operator('color_prime.selected_color',text='Оставить цвет',icon='LOCKED').family='FIXED'
    lines(layout,'Остальные части сохранят свои цвета. Отмена — Ctrl+Z.',context)
    from .family_links import ready,parent,shared_socket
    if ready(s):
        for family in ('MAIN','ACCENT'):layout.prop(shared_socket(parent(s,family)),'default_value',text='Prime '+family.title())
    if s.studio.assignment_summary.startswith('Назначено:'):lines(layout,s.studio.assignment_summary,context,'CHECKMARK')


CLASSES=(COLORPRIME_OT_selected_color,)
