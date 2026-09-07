"""Compact material library for the appearance workspace; explicit assignment."""
import uuid
import bpy
from bpy.props import EnumProperty


def revision(mat):
    import json,hashlib
    rows=[]
    for node in mat.node_tree.nodes:
        if node.type in {'GROUP','OUTPUT_MATERIAL'}:continue
        values=[]
        for socket in node.inputs:
            if socket.is_linked or not hasattr(socket,'default_value'):continue
            value=socket.default_value
            if socket.type in {'RGBA','VECTOR'}:value=list(value)
            if socket.type in {'RGBA','VECTOR','VALUE','INT','BOOLEAN'}:values.append((socket.name,value))
        rows.append((node.type,getattr(node,'blend_type',''),values))
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def recipients(context):
    from .appearance_workspace import meshes,source_model
    s=context.scene.color_prime
    source=source_model(s) if s.appearance_mode=='MODEL' else None
    return [i for i in s.icon_sets if i.enabled and i!=source and meshes(i,context.scene)]


def prepare(context):
    from .appearance_workspace import active_palette,capture,source_model,apply
    s=context.scene.color_prime;items=recipients(context)
    if not items:raise ValueError('Отметь модели в блоке «К каким моделям применить».')
    p=capture(s,source_model(s),reuse=True) if s.appearance_mode=='MODEL' and source_model(s) else active_palette(s)
    if p is None:raise ValueError('Сначала выбери палитру или модель-источник.')
    apply(context,p,items,False)
    return items


def sources(context,items):
    from .appearance_workspace import meshes
    from .workspace_actions import used_materials
    from .family_links import color_source
    result={}
    for item in items:
        for obj in meshes(item,context.scene):
            for mat in sorted(used_materials(obj),key=lambda m:m.name):
                role=mat.get('color_prime_family');src=color_source(mat)
                if role in {'MAIN','ACCENT'} and src and src.tree.get('cp_family_role')==role:result.setdefault(role,src.tree)
    return result


def family_group(context,items,family):
    group=sources(context,items).get(family)
    if group is not None:return group
    from .appearance_workspace import active_palette,capture,source_model,primary
    from .transaction import OWNER_KEY
    s=context.scene.color_prime
    p=capture(s,source_model(s),reuse=True) if s.appearance_mode=='MODEL' and source_model(s) else active_palette(s)
    if p is None:raise ValueError('Сначала выбери палитру.')
    # An unused family still belongs in the library. Never borrow a source
    # belonging to an unchecked model just because it is the global parent.
    for c in s.children:
        if c.material and c.material.get('cp_workspace_variant') and c.family==family:
            from .family_links import color_source
            src=color_source(c.material)
            if src:return src.tree
    group=bpy.data.node_groups.new('Prime '+family.title(),'ShaderNodeTree')
    group['cp_family_role']=family;group[OWNER_KEY]=s.studio.stage_id
    if hasattr(group,'interface'):group.interface.new_socket(name='Color',in_out='OUTPUT',socket_type='NodeSocketColor')
    else:group.outputs.new('NodeSocketColor','Color')
    rgb=group.nodes.new('ShaderNodeRGB');rgb.name='Color';rgb.outputs[0].default_value=primary(p,family)
    output=group.nodes.new('NodeGroupOutput');group.links.new(rgb.outputs[0],output.inputs['Color'])
    return group


def create(context):
    from .material_names import name_material
    from .transaction import OWNER_KEY
    items=prepare(context);s=context.scene.color_prime;family=s.variants_family
    group=family_group(context,items,family)
    styles=('MATTE','GLOSS','METAL','GLOSS','LIGHT','DARK')
    made=[];old_count=len(s.children)
    try:
        for n in range(s.variants_count):
            style=styles[n%len(styles)] if s.variants_style=='VARIED' else 'SAME'
            mat=bpy.data.materials.new('Prime Variant');made.append(mat);mat.use_nodes=True
            mat[OWNER_KEY]=s.studio.stage_id;mat['cp_workspace_variant']=uuid.uuid4().hex
            mat['color_prime_child']=True;mat['color_prime_family']=family;mat['color_prime_family_source']='MANUAL'
            mat['color_prime_locked']=True;mat['color_prime_child_profile']=style
            shader=mat.node_tree.nodes.get('Principled BSDF')
            node=mat.node_tree.nodes.new('ShaderNodeGroup');node.node_tree=group;node['cp_family_link']=True
            mat.node_tree.links.new(node.outputs['Color'],shader.inputs['Base Color'])
            if s.variants_style=='VARIED':
                turn=n//6;shift=.12*turn/max(1,(s.variants_count-1)//6)
                shader.inputs['Roughness'].default_value=min(.98,(.85,.12,.28,.24,.6,.4)[n%6]+shift)
                shader.inputs['Metallic'].default_value=1 if style=='METAL' else 0
                coat=shader.inputs.get('Coat Weight') or shader.inputs.get('Clearcoat')
                if coat:coat.default_value=1 if n%6==3 else 0
            if style in {'LIGHT','DARK'}:
                from .material_controls import tint_node
                tint=tint_node(mat,True);tint.inputs[0].default_value=.18 if style=='LIGHT' else .22
                tint.inputs[2].default_value=(1,1,1,1) if style=='LIGHT' else (0,0,0,1)
            name_material(mat,family,style)
            child=s.children.add();child.material=mat;child.family=family;child.profile=style;child.auto_assign=True
            mat.use_fake_user=True
        s.child_index=old_count
    except Exception:
        while len(s.children)>old_count:s.children.remove(len(s.children)-1)
        for mat in made:bpy.data.materials.remove(mat)
        raise
    s.appearance_note='Создано {} экземпляров. Теперь нажми «Автоназначить» или выбери зоны вручную.'.format(len(made))
    return made


def assign(context):
    from .appearance_workspace import meshes
    from .child_materials import auto_assign
    from .family_links import color_source
    from .authored_setup import _replace_source
    s=context.scene.color_prime
    chosen=[c.material for c in s.children if c.material and c.material.get('cp_workspace_variant') and c.auto_assign]
    if not chosen:raise ValueError('Отметь экземпляры галочками в библиотеке.')
    items=prepare(context);groups=sources(context,items)
    if not any(m.get('color_prime_family') in groups for m in chosen):
        raise ValueError('У отмеченных моделей нет зон выбранной семьи. Назначь экземпляр на зону вручную или выбери другую семью.')
    import hashlib,json
    from array import array
    intent=json.dumps([(m['cp_workspace_variant'],revision(m)) for m in chosen])
    def fingerprint(item):
        h=hashlib.sha256(intent.encode())
        for o in meshes(item,context.scene):
            indices=array('i',[0])*len(o.data.polygons);o.data.polygons.foreach_get('material_index',indices)
            h.update(indices.tobytes());h.update(str(o.data.as_pointer()).encode())
            h.update(str([slot.material.as_pointer() if slot.material else 0 for slot in o.material_slots]).encode())
        return h.hexdigest()
    if all(i.get('cp_variants_assignment')==fingerprint(i) for i in items):
        s.appearance_note='Эти экземпляры уже назначены. Зоны и поверхности не изменены.'
        return {'zones':0,'similar_groups':0,'unchanged':True}
    flags={c.material:c.auto_assign for c in s.children if c.material}
    # Local copies isolate templates and instances already used by unchecked models.
    start=len(s.children)
    made=[]
    objects=[o for i in items for o in meshes(i,context.scene)]
    checkpoint={o:([(slot.link,slot.material) for slot in o.material_slots],[f.material_index for f in o.data.polygons]) for o in objects}
    try:
        for c in s.children:c.auto_assign=False
        for template in chosen:
            family=template.get('color_prime_family');group=groups.get(family)
            if group is None:continue
            from .transaction import OWNER_KEY
            mat=template.copy();made.append(mat);mat.use_fake_user=False;mat[OWNER_KEY]=s.studio.stage_id
            del mat['cp_workspace_variant'];mat['cp_variant_instance']=template['cp_workspace_variant']
            mat['cp_variant_revision']=revision(template)
            src=color_source(mat)
            from .selected_colors import _local_tree
            _local_tree(mat,src.group_path)
            _replace_source(mat,color_source(mat),group,preserve_color=False)
            child=s.children.add();child.material=mat;child.family=family;child.auto_assign=True
        result=auto_assign(context.scene,s,[o for i in items for o in meshes(i,context.scene)],find_missing=False,local_only=True)
        for item in items:item['cp_variants_assignment']=fingerprint(item)
    except Exception:
        for o,(slots,indices) in checkpoint.items():
            o.data.materials.clear()
            for link,mat in slots:o.data.materials.append(mat)
            for slot,(link,mat) in zip(o.material_slots,slots):slot.link=link;slot.material=mat
            o.data.polygons.foreach_set('material_index',indices)
        while len(s.children)>start:s.children.remove(len(s.children)-1)
        for mat in made:bpy.data.materials.remove(mat)
        raise
    finally:
        for c in s.children:c.auto_assign=flags.get(c.material,False)
    s.appearance_note='Экземпляры назначены: {} зон, {} групп похожих деталей. Остальные семьи сохранены.'.format(result['zones'],result['similar_groups'])
    return result


def manual(context,faces=False):
    from .appearance_workspace import meshes
    from .surface_adapter import REGION_ATTRIBUTE
    from .family_links import color_source
    from .selected_colors import _local_tree
    from .authored_setup import _replace_source
    from .child_materials import assign_child
    s=context.scene.color_prime;obj=context.active_object;items=recipients(context)
    if not obj or obj not in [o for i in items for o in meshes(i,context.scene)]:raise ValueError('Выдели меш отмеченной модели-получателя.')
    if not 0<=s.child_index<len(s.children):raise ValueError('Выбери экземпляр в библиотеке.')
    template=s.children[s.child_index].material
    if not template or not template.get('cp_workspace_variant'):raise ValueError('Выбери экземпляр в библиотеке.')
    edit=obj.mode=='EDIT'
    if faces:
        if not edit:raise ValueError('Нажми Tab и выдели грани.')
        import bmesh
        bm=bmesh.from_edit_mesh(obj.data);bm.faces.ensure_lookup_table();bm.faces.index_update()
        indices=[f.index for f in bm.faces if f.select and not f.hide]
    else:
        if edit:raise ValueError('В режиме редактирования используй «На грани».')
        attr=obj.data.attributes.get(REGION_ATTRIBUTE)
        if not attr:raise ValueError('Сначала найди зоны этой модели.')
        indices=[i for i,v in enumerate(attr.data) if v.value==s.studio.region_index-1]
    if not indices:raise ValueError('Выделение пустое или такого номера зоны нет.')
    family=template['color_prime_family'];group=family_group(context,items,family)
    if edit:bpy.ops.object.mode_set(mode='OBJECT')
    mat=None;start=len(s.children)
    try:
        mat=template.copy();mat.use_fake_user=False
        del mat['cp_workspace_variant'];mat['cp_variant_instance']=template['cp_workspace_variant'];mat['cp_variant_revision']=revision(template)
        src=color_source(mat);_local_tree(mat,src.group_path);_replace_source(mat,color_source(mat),group,preserve_color=False)
        child=s.children.add();child.material=mat;child.family=family;child.auto_assign=False
        count,_=assign_child(context.scene,s,obj,mat,indices)
        s.appearance_note='Экземпляр назначен на {} граней. Остальные зоны сохранены.'.format(count)
    except Exception:
        while len(s.children)>start:s.children.remove(len(s.children)-1)
        if mat is not None and mat.users==0:bpy.data.materials.remove(mat)
        raise
    finally:
        if edit:bpy.ops.object.mode_set(mode='EDIT')


class COLORPRIME_OT_workspace_variants(bpy.types.Operator):
    bl_idname='color_prime.workspace_variants';bl_label='Экземпляры материалов';bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=tuple((a,a,'') for a in ('CREATE','ASSIGN','REGION','FACES')))
    @classmethod
    def poll(cls,context):
        from .studio_ops import idle
        return idle(context) and context.mode in {'OBJECT','EDIT_MESH'}
    def execute(self,context):
        try:
            if self.action in {'REGION','FACES'}:manual(context,self.action=='FACES')
            elif context.mode!='OBJECT':raise ValueError('Для создания и автоназначения нажми Tab и выйди из редактирования.')
            else:(create if self.action=='CREATE' else assign)(context)
            context.scene.color_prime.last_error='';return {'FINISHED'}
        except Exception as exc:
            context.scene.color_prime.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}


class COLORPRIME_UL_workspace_variants(bpy.types.UIList):
    def filter_items(self,context,data,propname):
        return [self.bitflag_filter_item if c.material and c.material.get('cp_workspace_variant') else 0 for c in getattr(data,propname)],[]
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index=0):
        row=layout.row(align=True);row.prop(item,'auto_assign',text='');row.label(text=item.family.title())
        if item.material:row.prop(item.material,'name',text='',emboss=False)


def draw(layout,context,s):
    from .guided_ui import lines,button
    from .appearance_workspace import meshes
    variants=[c for c in s.children if c.material and c.material.get('cp_workspace_variant')]
    box=layout.box();box.prop(s,'variants_open',text='3. Экземпляры материалов · '+str(len(variants)),icon='TRIA_DOWN' if s.variants_open else 'TRIA_RIGHT',emboss=False)
    if not s.variants_open:return
    items=recipients(context)
    lines(box,'Цвет — от Main / Accent. Поверхность каждого экземпляра настраивается отдельно.',context)
    row=box.row(align=True);row.prop(s,'variants_family',text='');row.prop(s,'variants_count',text='Штук')
    box.prop(s,'variants_style',text='')
    row=box.row(align=True)
    button(row,'color_prime.workspace_variants','Создать','ADD',bool(items)).action='CREATE'
    button(row,'color_prime.workspace_variants','Автоназначить','MATERIAL',bool(items) and any(c.auto_assign for c in variants)).action='ASSIGN'
    if not items:lines(box,'Отметь модель-получатель выше.',context,'INFO')
    elif any(not i.applied_appearance_uid for i in items):lines(box,'При создании сначала применятся выбранные цвета. Зоны и поверхности сохранятся.',context,'INFO')
    if not variants:return
    box.template_list('COLORPRIME_UL_workspace_variants','',s,'children',s,'child_index',rows=min(4,len(variants)))
    lines(box,'Галочки — какие экземпляры участвуют в автоназначении.',context)
    if not 0<=s.child_index<len(s.children):return
    child=s.children[s.child_index];mat=child.material
    if not mat or not mat.get('cp_workspace_variant'):return
    uid=mat['cp_workspace_variant']
    uses=[];changed=False
    for item in s.icon_sets:
        assigned={slot.material for o in meshes(item,context.scene) for slot in o.material_slots if slot.material and (slot.material==mat or slot.material.get('cp_variant_instance')==uid)}
        if assigned:
            uses.append(item.name)
            changed|=any(m.get('cp_variant_revision')!=revision(mat) for m in assigned)
    lines(box,'На моделях: '+', '.join(uses) if uses else 'В библиотеке · ещё не назначен',context,'CHECKMARK' if uses else 'INFO')
    if changed:lines(box,'Есть изменения поверхности · нажми «Автоназначить».',context,'INFO')
    row=box.row(align=True)
    if context.mode=='EDIT_MESH':button(row,'color_prime.workspace_variants','На выделенные грани','FACESEL').action='FACES'
    else:
        row.prop(s.studio,'region_index',text='Зона')
        button(row,'color_prime.show_region','','RESTRICT_SELECT_OFF',bool(context.active_object and context.active_object.type=='MESH'))
        button(row,'color_prime.workspace_variants','Назначить','MATERIAL',bool(items)).action='REGION'
    from .family_links import principled
    shader=principled(mat)
    if shader:
        for key,label in [('Roughness','Шероховатость'),('Metallic','Металличность')]:
            socket=shader.inputs.get(key)
            if socket and not socket.is_linked:box.prop(socket,'default_value',text=label,slider=True)
    lines(box,'Изменения экземпляра в библиотеке попадут на модели при следующем «Автоназначить».',context)
    box.prop(s,'variants_details',text='Оттенок и шейдер',icon='TRIA_DOWN' if s.variants_details else 'TRIA_RIGHT',emboss=False)
    if s.variants_details:
        from .material_controls import draw as controls
        controls(box,context,s,mat)


CLASSES=(COLORPRIME_OT_workspace_variants,COLORPRIME_UL_workspace_variants)
