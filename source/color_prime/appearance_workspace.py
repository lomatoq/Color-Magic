"""Model-scoped appearance workflow. Presets are drafts until Apply is pressed."""
import uuid,json,time
_status_counts={}
from collections import defaultdict
import bpy
from bpy.props import EnumProperty,IntProperty,StringProperty
from .studio_ops import idle
from .utils import objects_from_icon_item
from .family_links import color_source,color_input,shared_socket,parent,ensure_parent


def meshes(item,scene):
    return [o for o in objects_from_icon_item(item,scene) if o.type=='MESH' and not o.get('cp_studio_rig_owner')]


def active_palette(s):
    return s.appearances[s.appearance_index] if 0<=s.appearance_index<len(s.appearances) else None


def source_model(s):return next((i for i in s.icon_sets if i.name==s.appearance_source),None)


def primary(p,family):
    colors=p.main_colors if family=='MAIN' else p.accent_colors
    index=min(p.main_index if family=='MAIN' else p.accent_index,max(0,len(colors)-1))
    if not colors:raise ValueError('Добавь цвет в '+family.title())
    return tuple(colors[index].color)


def signature(p):
    from .export_kernel import digest
    return digest({'main':primary(p,'MAIN'),'accent':primary(p,'ACCENT'),
                   'materials':[(r.family,r.material.name if r.material else '') for r in p.materials]})


def material_roles(objects,s):
    """Read authored family tags first; use color clustering only for unknowns."""
    from .discovery import _cluster
    from .color_math import family_feature
    weights=defaultdict(float)
    for obj in objects:
        for f in obj.data.polygons:
            mat=obj.material_slots[f.material_index].material if f.material_index<len(obj.material_slots) else None
            if mat:weights[mat]+=max(f.area,1e-8)
    rows=[];roles={}
    for mat,weight in weights.items():
        role=mat.get('color_prime_family','')
        if role in {'MAIN','ACCENT','FIXED','IGNORE'}:roles[mat]=role;continue
        if mat.library or mat.name.casefold().startswith(('gold','золот')) or (s.fixed_name_suffix and mat.name.endswith(s.fixed_name_suffix)):
            roles[mat]='FIXED';continue
        source=color_source(mat)
        if source and source.tree.get('cp_family_role') in {'MAIN','ACCENT'}:
            roles[mat]=source.tree['cp_family_role'];continue
        color=source.socket.default_value if source else mat.diffuse_color
        rows.append({'material':mat,'weight':weight,'feature':family_feature(color)})
    _cluster(rows)
    roles.update({r['material']:r['family'] for r in rows})
    return roles,weights


def model_colors(objects,s):
    roles,weights=material_roles(objects,s);out={}
    for family in ('MAIN','ACCENT'):
        candidates=[m for m in weights if roles[m]==family]
        mat=max(candidates,key=lambda m:weights[m]) if candidates else None
        source=color_source(mat) if mat else None
        out[family]=tuple(source.socket.default_value) if source else tuple(mat.diffuse_color) if mat else (.5,.5,.5,1)
    return out,roles


def new_palette(s,name='Новая палитра',colors=None):
    used={p.name for p in s.appearances};base=name;n=2
    while name in used:name=base+' '+str(n);n+=1
    p=s.appearances.add();p.uid=uuid.uuid4().hex;p.name=name
    for family in ('MAIN','ACCENT'):
        row=(p.main_colors if family=='MAIN' else p.accent_colors).add();row.name=family.title()+' 1'
        row.color=(colors or {}).get(family,(.45,.12,.65,1) if family=='MAIN' else (.15,.65,.25,1))
        setattr(p,'main_base' if family=='MAIN' else 'accent_base',row.color)
    s.appearance_index=len(s.appearances)-1
    return p


def source_signature(roles):
    from .export_kernel import digest
    def values(block):
        result={}
        for prop in block.bl_rna.properties:
            if prop.identifier=='rna_type' or prop.is_readonly or prop.type not in {'BOOLEAN','INT','FLOAT','STRING','ENUM'}:continue
            value=getattr(block,prop.identifier)
            if getattr(prop,'is_array',False):value=list(value)
            if isinstance(value,set):value=sorted(value)
            result[prop.identifier]=value
        return result
    def tree_data(tree,visited=()):
        if tree is None or tree.as_pointer() in visited:return None
        visited=visited+(tree.as_pointer(),)
        simple={'RGB','MIX_RGB','BSDF_PRINCIPLED','BSDF_DIFFUSE','EMISSION','OUTPUT_MATERIAL','GROUP','GROUP_INPUT','GROUP_OUTPUT','REROUTE','HUE_SAT','GAMMA','BRIGHTCONTRAST'}
        # Texture/procedural nodes do not invalidate reuse by themselves.
        # Capture their settings and referenced datablocks as part of the key.
        return {'nodes':[(n.bl_idname,values(n),
            [(prop.identifier,getattr(getattr(n,prop.identifier,None),'name_full','')) for prop in n.bl_rna.properties if prop.type=='POINTER' and prop.identifier not in {'rna_type','node_tree'}],
            [(v.identifier,list(v.default_value) if getattr(v,'type','') in {'RGBA','VECTOR'} else v.default_value) for v in list(n.inputs)+list(n.outputs) if hasattr(v,'default_value') and getattr(v,'type','') in {'RGBA','VECTOR','VALUE','INT','BOOLEAN'}],
            tree_data(n.node_tree,visited) if n.type=='GROUP' and n.node_tree else None) for n in tree.nodes],
            'links':[(l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in tree.links]}
    try:return digest([(m.name,r,values(m),tree_data(m.node_tree)) for m,r in sorted(roles.items(),key=lambda x:x[0].name) if r in {'MAIN','ACCENT'}])
    except ValueError:return ''


def capture(s,item,reuse=False):
    objects=meshes(item,s.id_data)
    if not objects:raise ValueError('Модель-источник удалена или не содержит мешей.')
    colors,roles=model_colors(objects,s)
    fingerprint=source_signature(roles)
    if reuse and fingerprint:
        cached=next((p for p in s.appearances if p.source_key==item.name and p.source_signature==fingerprint
                     and all(all(abs(a-b)<1e-6 for a,b in zip(primary(p,f),colors[f])) for f in colors)
                     and all(r.material for r in p.materials)),None)
        if cached:return cached
    p=new_palette(s,'Из '+item.name,colors)
    p.source_key=item.name;p.source_signature=fingerprint
    memo={}
    def copy_tree(tree):
        if tree in memo:return memo[tree]
        clone=tree.copy();memo[tree]=clone
        for n in clone.nodes:
            if n.type=='GROUP' and n.node_tree:n.node_tree=copy_tree(n.node_tree)
        return clone
    for mat,role in sorted(roles.items(),key=lambda row:row[0].name):
        if role not in {'MAIN','ACCENT'}:continue
        clone=mat.copy()
        if clone.node_tree:
            for n in clone.node_tree.nodes:
                if n.type=='GROUP' and n.node_tree:n.node_tree=copy_tree(n.node_tree)
        clone['cp_appearance_template']=p.uid
        record=p.materials.add();record.family=role;record.material=clone
    return p


def status(item,scene):
    from .surface_adapter import region_count
    objects=meshes(item,scene)
    if not objects:return 'Модель удалена',0,0
    key=tuple((o.data.as_pointer(),len(o.data.polygons)) for o in objects)
    cached=_status_counts.get(key)
    if cached:count=cached[1]
    else:
        count=sum(region_count(o) or len({p.material_index for p in o.data.polygons}) for o in objects)
        _status_counts[key]=(time.monotonic(),count)
    has_material=any(slot.material for o in objects for slot in o.material_slots)
    fixed={slot.material for o in objects for slot in o.material_slots if slot.material and slot.material.get('color_prime_family') in {'FIXED','IGNORE'}}
    state=item.zones_origin or ('Готовая разметка материалов' if has_material else 'Зоны найдём при применении')
    if item.applied_appearance:
        state+=' · Применена: '+item.applied_appearance
        p=next((p for p in scene.color_prime.appearances if p.uid==item.applied_appearance_uid),None)
        if p and item.applied_signature!=signature(p)+str(item.applied_materials):state+=' · В палитре есть изменения'
    active=active_palette(scene.color_prime)
    if scene.color_prime.appearance_mode=='PALETTE' and active:
        if not item.applied_appearance_uid:state+=' · Цвета ещё не применены'
        elif item.applied_appearance_uid!=active.uid:state+=' · Выбранная палитра ещё не применена'
    if fixed:state+=' · Зафиксировано материалов: {}'.format(len(fixed))
    return state,len(objects),count


def plan(item,s):
    from .surface_adapter import REGION_ATTRIBUTE,sharp_edges
    from .mesh_regions import segment
    objects=meshes(item,s.id_data);roles,_=material_roles(objects,s)
    targets={'MAIN':{},'ACCENT':{}};labels={};blank=[];preserved=0
    for obj in objects:
        attr=obj.data.attributes.get(REGION_ATTRIBUTE)
        assigned=any(slot.material for slot in obj.material_slots)
        if attr:values=[v.value for v in attr.data];preserved+=1
        elif assigned:values=[p.material_index for p in obj.data.polygons];preserved+=1
        else:
            result=segment([tuple(obj.matrix_world@v.co) for v in obj.data.vertices],
                [tuple(p.vertices) for p in obj.data.polygons],
                [tuple(e.vertices) for e in obj.data.edges if e.use_seam],sharp_edges(obj.data),s.studio.surface_regions,s.studio.region_max)
            values=result.labels or tuple(0 for _ in obj.data.polygons)
            obj['color_prime_region_summary']=json.dumps({'method':result.method,'count':result.count,'aggregate_regions':result.aggregate_regions})
        labels[obj]=values
        for f,label in zip(obj.data.polygons,values):
            mat=obj.material_slots[f.material_index].material if f.material_index<len(obj.material_slots) else None
            if mat and not (obj.get('cp_workspace_auto_regions')):
                family=roles.get(mat,'FIXED')
                if family in targets:targets[family].setdefault(obj,[]).append(f.index)
            else:blank.append((obj,label,f.index,max(f.area,1e-8)))
    areas=defaultdict(float)
    for obj,label,index,area in blank:areas[(obj,label)]+=area
    largest=max(areas,key=areas.get) if areas else None
    from .workspace_actions import secondary_regions
    grouped=defaultdict(list)
    for obj,label,index,area in blank:grouped[obj].append((label,index,area))
    accents={obj:secondary_regions(obj,entries) for obj,entries in grouped.items() if len({label for label,index,area in entries})>1}
    for obj,label,index,area in blank:
        family=('ACCENT' if label in accents[obj] else 'MAIN') if obj in accents else ('MAIN' if (obj,label)==largest else 'ACCENT')
        targets[family].setdefault(obj,[]).append(index)
    origin='Готовые зоны сохранены' if preserved==len(objects) else 'Зоны найдены автоматически' if not preserved else 'Готовые зоны + автопоиск'
    if item.zones_origin and all(o.data.attributes.get(REGION_ATTRIBUTE) for o in objects):origin=item.zones_origin
    return targets,labels,origin


def apply(context,p,items,with_materials=False):
    from . import transaction,selected_colors
    from .surface_adapter import REGION_ATTRIBUTE
    from .family_links import family_node
    s=context.scene.color_prime;scene=context.scene;st=s.studio
    from .runtime import restore_preview
    if s.preview_active:restore_preview(scene)
    if s.preview_active:raise ValueError('Восстанови предыдущий просмотр перед применением.')
    items=[i for i in items if meshes(i,scene)]
    if not items:raise ValueError('Отметь хотя бы одну модель-получателя. Глаз управляет только видимостью.')
    with_materials=bool(with_materials and p.materials)
    colors={f:primary(p,f) for f in ('MAIN','ACCENT')};stamp=signature(p)+str(with_materials)
    # Recoloring an already assigned palette never redistributes its surfaces.
    from .workspace_actions import recolor,prepare,find_zones
    for item in items:
        if item.workspace_auto_zones and not item.workspace_zones_checked:find_zones(context,item)
    transfer=[i for i in items if with_materials and i.name!=p.source_key and not (i.applied_appearance_uid==p.uid and i.applied_materials)]
    if not transfer and not any(o.get('cp_workspace_auto_regions') for i in items for o in meshes(i,scene)) and all(i.applied_appearance_uid for i in items) and recolor(scene,p,items):
        from .workspace_actions import compact_slots
        compact_slots(scene,[o for i in items for o in meshes(i,scene)])
        s.appearance_note='Цвета обновлены. Материалы и зоны сохранены.';s.last_error='';return
    for item in items:
        if item.workspace_auto_zones and not item.workspace_zones_checked:find_zones(context,item)
        else:prepare(context,item)
    plans=[(i,plan(i,s)) for i in items]
    objects=list({o for _,(_,labels,_) in plans for o in labels})
    if not any(picked for _,(targets,_,_) in plans for picked in targets.values()):raise ValueError('На отмеченных моделях все материалы зафиксированы. Выбери части в ручном назначении.')
    # Validate all shaders and geometry before creating any working copies.
    from .shader_endpoints import endpoints
    for obj in objects:
        if obj.library or obj.override_library or obj.data.library:raise ValueError('Модель только для чтения: '+obj.name)
    for _,(targets,_,_) in plans:
        for picked in targets.values():
            for obj,indices in picked.items():
                for index in {obj.data.polygons[i].material_index for i in indices}:
                    mat=obj.material_slots[index].material if index<len(obj.material_slots) else None
                    if mat and mat.use_nodes and len(endpoints(mat))!=1:raise ValueError('Укажи цветовой вход материала «'+mat.name+'» в ручных настройках.')
    # Repeated Apply is a no-op when both stored intent and actual source colors match.
    if all(i.applied_signature==stamp and all(all(abs(a-b)<1e-6 for a,b in zip(model_colors(meshes(i,scene),s)[0][f],colors[f])) for f in colors) for i in items):
        s.appearance_note='Эта палитра уже применена к отмеченным моделям.';return
    before=transaction.snapshot_settings(s);started=st.stage_status=='NONE'
    mats_before={m.as_pointer() for m in bpy.data.materials};groups_before={g.as_pointer() for g in bpy.data.node_groups}
    original_slots={o:[(v.link,v.material) for v in o.material_slots] for o in objects}
    # Operation-local copy provides an atomic rollback even inside a prior stage.
    checkpoint={o:(o.data,[p.material_index for p in o.data.polygons]) for o in objects}
    previous_records={r.object:r.staged_mesh for r in st.backup_objects if r.object}
    previous_backups=len(st.backup_objects);previous_material_backups=len(st.backup_materials)
    try:
        st.reuse_family_library=True
        if started:transaction.start(scene,s,before,objects=objects)
        else:
            transaction.extend(scene,s,objects)
            for o in objects:
                if o in previous_records:
                    o.data=o.data.copy();o.data[transaction.OWNER_KEY]=st.stage_id
                    next(r for r in st.backup_objects if r.object==o).staged_mesh=o.data
        for o,slots in original_slots.items():
            for slot,(link,mat) in zip(o.material_slots,slots):slot.link=link;slot.material=mat
        # A fresh color source is scoped to this application. Unchecked models,
        # including models sharing the old materials, keep their exact source.
        initial,_=model_colors(objects,s)
        if transfer:
            initial={'MAIN':tuple(p.main_base),'ACCENT':tuple(p.accent_base)}
        for family in ('MAIN','ACCENT'):
            setattr(s,'main_parent_material' if family=='MAIN' else 'accent_parent_material',None)
            setattr(s,'main_reference_color' if family=='MAIN' else 'accent_reference_color',initial[family])
            ensure_parent(s,family)
        # Do not mutate preset data while the collection may be relocated by discovery.
        templates=[(r.material,r.family) for r in p.materials if r.material]
        palette_name=p.name;palette_uid=p.uid
        # One assignment pass per family, rather than two full scans per model.
        for family in ('MAIN','ACCENT'):
            picked={o:ids for _,(targets,_,_) in plans for o,ids in targets[family].items()}
            if picked:selected_colors.assign(context,picked,family,force_private=True,refresh=False)
        for item,(targets,labels,origin) in plans:
            for obj,values in labels.items():
                attr=obj.data.attributes.get(REGION_ATTRIBUTE) or obj.data.attributes.new(name=REGION_ATTRIBUTE,type='INT',domain='FACE')
                attr.data.foreach_set('value',values)
                if 'cp_workspace_auto_regions' in obj:del obj['cp_workspace_auto_regions']
            item.zones_origin=origin;item.applied_appearance=palette_name;item.applied_signature=stamp
            item.applied_materials=item in transfer or (item.applied_materials and item.applied_appearance_uid==palette_uid)
            item.applied_appearance_uid=palette_uid
            item.applied_signature=signature(p)+str(item.applied_materials)
        if transfer and templates:
            old_flags={c.material:c.auto_assign for c in s.children if c.material}
            for c in s.children:c.auto_assign=False
            try:
                for template,family in templates:
                    mat=template.copy();mat[transaction.OWNER_KEY]=st.stage_id
                    for key in ('cp_family_master','color_prime_parent','color_prime_generated_name'):
                        if key in mat:del mat[key]
                    selected_colors._link(mat,s,family)
                    mat['color_prime_family']=family;mat['color_prime_family_source']='MANUAL';mat['color_prime_child']=True;mat['color_prime_locked']=True
                    from .material_names import name_material
                    name_material(mat,family)
                    child=s.children.add();child.material=mat;child.family=family;child.profile='SAME';child.auto_assign=True
                from .child_materials import auto_assign
                auto_assign(scene,s,[o for item in transfer for o in meshes(item,scene)],find_missing=False,local_only=True)
            finally:
                for c in s.children:
                    if c.material in old_flags:c.auto_assign=old_flags[c.material]
        from .discovery import scan_material_bindings
        localize=s.auto_localize_legacy;s.auto_localize_legacy=False
        try:scan_material_bindings(scene,s,False,local_only=True)
        finally:s.auto_localize_legacy=localize
        for family,value in colors.items():shared_socket(parent(s,family)).default_value=value
        from .workspace_actions import compact_slots
        compact_slots(scene,objects)
        st.adoption_pending=False;s.last_error=''
        transaction.tag_created(s,mats_before,groups_before)
        s.appearance_note='Применена «{}» → {}.'.format(palette_name,', '.join(i.name for i in items))
        bpy.context.view_layer.update();_status_counts.clear()
    except Exception:
        if started:transaction.tag_created(s,mats_before,groups_before);transaction.rollback(scene,s)
        else:
            for o,(mesh,_) in checkpoint.items():
                o.data=mesh
                for slot,(link,mat) in zip(o.material_slots,original_slots[o]):slot.link=link;slot.material=mat
            while len(st.backup_objects)>previous_backups:st.backup_objects.remove(len(st.backup_objects)-1)
            while len(st.backup_materials)>previous_material_backups:st.backup_materials.remove(len(st.backup_materials)-1)
            for r in st.backup_objects:
                if r.object in previous_records:r.staged_mesh=previous_records[r.object]
            old=s.suppress_callbacks;s.suppress_callbacks=True
            try:transaction.restore_settings(s,before)
            finally:s.suppress_callbacks=old
        raise


class COLORPRIME_OT_appearance(bpy.types.Operator):
    bl_idname='color_prime.appearance';bl_label='Палитра для моделей';bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=tuple((a,a,'') for a in ('NEW','CAPTURE','APPLY','COLOR_ADD','COLOR_REMOVE','COLOR_SELECT','PALETTE_REMOVE','PREVIEW','PREVIEW_CANCEL','PREPARE_MODEL','FIND_ZONES')))
    family:StringProperty(default='MAIN');index:IntProperty(default=0)
    @classmethod
    def description(cls,context,properties):
        return {'PREPARE_MODEL':'Создать коллекцию и Anchor модели, сохранив положение и материалы',
                'FIND_ZONES':'Найти геометрические зоны без смены материалов. Существующие несколько зон сохраняются',
                'PREVIEW':'Быстрый обратимый просмотр Main/Accent. После включения меняй цвета прямо в палитре',
                'APPLY':'Применить цвета к отмеченным моделям; готовые назначения материалов сохраняются',
                'PREVIEW_CANCEL':'Вернуть цвета до начала предпросмотра'}.get(properties.action,'Палитры и материалы выбранных моделей')
    @classmethod
    def poll(cls,context):
        from .guided_state import tr
        s=context.scene.color_prime
        if not idle(context):cls.poll_message_set(tr(s,'Wait for the render to finish.','Дачакайся завяршэння рэндэру.'));return False
        if context.mode!='OBJECT':cls.poll_message_set(tr(s,'Press Tab to finish editing faces.','Націсні Tab, каб скончыць рэдагаванне граняў.'));return False
        if s.studio.stage_status=='RECOVERY':cls.poll_message_set(tr(s,'Restore the interrupted operation first.','Спачатку аднаві перапыненую аперацыю.'));return False
        return True
    def execute(self,context):
        s=context.scene.color_prime
        try:
            p=active_palette(s)
            if self.action in {'PREPARE_MODEL','FIND_ZONES'}:
                from .workspace_actions import prepare,find_zones
                item=s.icon_sets[self.index]
                if self.action=='FIND_ZONES':
                    count=find_zones(context,item);s.appearance_note='{}: {} зон. Материалы сохранены.'.format(item.name,count)
                else:prepare(context,item);s.appearance_note=item.name+': коллекция и Anchor готовы.'
            elif self.action=='PREVIEW_CANCEL':
                from .runtime import restore_preview
                restore_preview(context.scene);s.appearance_note='Предпросмотр отменён.'
            elif self.action=='PREVIEW':
                from .workspace_actions import recolor
                items=[i for i in s.icon_sets if i.enabled and meshes(i,context.scene)]
                if p is None or not items:raise ValueError('Выбери палитру и модели.')
                if not recolor(context.scene,p,items,preview=True):raise ValueError('Сначала нажми «Применить» для подготовки независимых цветов выбранных моделей.')
                s.appearance_note='Предпросмотр цветов. «Применить» — оставить; «Вернуть цвета» — отменить.'
            elif self.action=='NEW':new_palette(s);s.appearance_mode='PALETTE';s.appearance_note='Новая палитра. Задай цвета и нажми «Применить».'
            elif self.action=='CAPTURE':
                item=source_model(s)
                if not item:raise ValueError('Выбери модель-источник.')
                capture(s,item);s.appearance_materials=False;s.appearance_mode='PALETTE';s.appearance_note='Цвета и материалы сохранены в новой палитре.'
            elif self.action=='APPLY':
                source=source_model(s) if s.appearance_mode=='MODEL' else None
                items=[i for i in s.icon_sets if i.enabled and i!=source and meshes(i,context.scene)]
                if not items:raise ValueError('Отметь модели-получатели галочками.')
                if s.appearance_mode=='MODEL':
                    if not source:raise ValueError('Выбери модель-источник.')
                    p=capture(s,source,reuse=True)
                if p is None:raise ValueError('Создай палитру или выбери модель-источник.')
                apply(context,p,items,s.appearance_materials)
            elif p:
                colors=p.main_colors if self.family=='MAIN' else p.accent_colors
                if self.action=='COLOR_ADD':row=colors.add();row.name=self.family.title()+' '+str(len(colors))
                elif self.action=='COLOR_REMOVE' and len(colors)>1:colors.remove(min(self.index,len(colors)-1))
                elif self.action=='COLOR_SELECT':setattr(p,'main_index' if self.family=='MAIN' else 'accent_index',self.index)
                elif self.action=='PALETTE_REMOVE':s.appearances.remove(s.appearance_index);s.appearance_index=max(0,s.appearance_index-1)
            s.last_error='';return {'FINISHED'}
        except Exception as exc:s.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}


class COLORPRIME_UL_appearances(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index=0):
        row=layout.row(align=True);row.prop(item,'enabled',text='');row.prop(item,'name',text='',emboss=False)


def draw(layout,context,s):
    from .workspace_locale import Layout
    layout=Layout(layout,s)
    from .guided_ui import lines,button,_camera
    from .constants import VERSION_STRING
    if s.render_running:
        lines(layout,s.studio.export_progress,context,'RENDER_STILL');layout.operator('color_prime.stop_render',text='Остановить после текущего');return
    header=layout.row(align=True);header.label(text='Color Prime '+VERSION_STRING);header.prop(s.studio,'guide_language',text='')
    from .guided_state import tr
    from .updater import draw as draw_updates
    draw_updates(layout,context,s)
    tabs=layout.row(align=True)
    tabs.prop_enum(s,'workspace_page','WORKSPACE',text=tr(s,'Workspace','Працоўная вобласць'))
    tabs.prop_enum(s,'workspace_page','FAST',text='Fast Track')
    if s.last_error:lines(layout,s.last_error,context,'ERROR')
    if s.workspace_page=='FAST':
        from .fast_track import draw as draw_fast
        return draw_fast(layout,context,s)
    if context.mode!='OBJECT':lines(layout,'Для применения к моделям нажми Tab. Назначение выбранным граням — в ручных инструментах ниже.',context,'INFO')
    if s.studio.stage_status=='RECOVERY':
        lines(layout,'Прерванная операция требует восстановления.',context,'ERROR')
        button(layout,'color_prime.stage_action','Восстановить исходную модель','LOOP_BACK').action='REVERT'
    box=layout.box();box.label(text='1. Цвета и материалы');box.prop(s,'appearance_mode',expand=True)
    p=active_palette(s)
    if s.preview_active:lines(box,'Предпросмотр · нажми «Применить», чтобы оставить цвета.',context,'INFO')
    elif s.appearance_mode=='PALETTE':lines(box,'Палитра готова к применению' if p and p.main_colors and p.accent_colors else 'Выбери или создай палитру',context,'CHECKMARK' if p and p.main_colors and p.accent_colors else 'INFO')
    if s.appearance_mode=='MODEL':
        box.prop_search(s,'appearance_source',s,'icon_sets',text='Источник')
        box.prop(s,'appearance_materials')
        button(box,'color_prime.appearance','Сохранить как палитру').action='CAPTURE'
    else:
        if s.appearances:
            box.template_list('COLORPRIME_UL_appearances','palettes',s,'appearances',s,'appearance_index',rows=min(3,max(2,len(s.appearances))))
            lines(box,'Выделенная палитра — для применения. Галочки — для пакетного экспорта.',context)
        button(box,'color_prime.appearance','Новая палитра','ADD').action='NEW'
        files=box.row(align=True)
        files.operator('color_prime.workspace_palette_file',text='Load JSON',icon='IMPORT').action='LOAD'
        files.operator('color_prime.workspace_palette_file',text='Save JSON',icon='EXPORT').action='SAVE'
        if p:
            for family in ('MAIN','ACCENT'):
                colors=p.main_colors if family=='MAIN' else p.accent_colors
                if colors:box.prop(colors[min(p.main_index if family=='MAIN' else p.accent_index,len(colors)-1)],'color',text=family.title())
            if p.materials:box.prop(s,'appearance_materials')
            box.prop(s,'appearance_details',text='Все цвета и настройки палитры',icon='TRIA_DOWN' if s.appearance_details else 'TRIA_RIGHT',emboss=False)
            if s.appearance_details:
                for family in ('MAIN','ACCENT'):
                    colors=p.main_colors if family=='MAIN' else p.accent_colors
                    box.label(text=family.title())
                    for index,c in enumerate(colors):
                        row=box.row(align=True);row.prop(c,'enabled',text='');row.prop(c,'name',text='');row.prop(c,'color',text='')
                        op=row.operator('color_prime.appearance',text='',icon='RADIOBUT_ON' if index==(p.main_index if family=='MAIN' else p.accent_index) else 'RADIOBUT_OFF');op.action='COLOR_SELECT';op.index=index;op.family=family
                        op=row.operator('color_prime.appearance',text='',icon='X');op.action='COLOR_REMOVE';op.index=index;op.family=family
                    op=box.operator('color_prime.appearance',text='Добавить цвет '+family.title(),icon='ADD');op.action='COLOR_ADD';op.family=family
                button(box,'color_prime.appearance','Удалить палитру','TRASH').action='PALETTE_REMOVE'
    box=layout.box();box.label(text='2. К каким моделям применить')
    row=box.row(align=True)
    for action,label in (('FIND','Найти модели'),('ALL','Все'),('NONE','Ни одной')):row.operator('color_prime.models',text=label).action=action
    source=source_model(s) if s.appearance_mode=='MODEL' else None
    targets=[]
    for index,item in enumerate(s.icon_sets):
        state,count,zones=status(item,context.scene);card=box.box()
        row=card.row(align=True)
        tick=row.row(align=True);tick.enabled=bool(count)
        if item==source:tick.label(text='',icon='EYEDROPPER')
        else:tick.prop(item,'enabled',text='')
        row.prop(item,'name',text='',emboss=False)
        op=row.operator('color_prime.models',text='',icon='HIDE_OFF' if any(not o.hide_get() for o in meshes(item,context.scene)) else 'HIDE_ON');op.action='EYE';op.index=index
        lines(card,('Источник · ' if item==source else '')+state,context,'ERROR' if not count else 'INFO')
        if count:
            lines(card,'{} деталей · {} зон'.format(count,zones),context)
            controls=card.row(align=True)
            prepared=bool(item.collection_root and item.object_root and item.collection_root.get('cp_model_collection'))
            if prepared:
                op=controls.operator('color_prime.models',text='Корень',icon='OUTLINER_COLLECTION');op.action='ROOT';op.index=index
            else:
                op=controls.operator('color_prime.appearance',text='Подготовить',icon='OUTLINER_COLLECTION');op.action='PREPARE_MODEL';op.index=index
            op=controls.operator('color_prime.appearance',text='Найти зоны',icon='MESH_DATA');op.action='FIND_ZONES';op.index=index
            card.prop(item,'workspace_auto_zones',text='Автозоны при применении')
        if count and item.enabled and item!=source:targets.append(item)
    if not s.icon_sets:lines(box,'Импортируй модели и нажми «Найти модели».',context)
    lines(box,'Галочка — применить и экспортировать. Глаз — только видимость.',context)
    if targets:lines(box,'Получатели: '+', '.join(i.name for i in targets),context)
    else:lines(box,'Модели-получатели не отмечены.',context)
    if s.appearance_mode=='PALETTE' and p is None:lines(box,'Сначала нажми «Новая палитра» или выбери «Из модели».',context)
    if s.appearance_mode=='MODEL' and source is None:lines(box,'Укажи модель-источник сверху.',context)
    if source is not None and not meshes(source,context.scene):lines(box,'Модель-источник удалена. Выбери другую.',context,'ERROR')
    available=bool(targets) and (p is not None if s.appearance_mode=='PALETTE' else source is not None and bool(meshes(source,context.scene)))
    if targets and p and s.appearance_mode=='PALETTE':
        applied=sum(i.applied_appearance_uid==p.uid and i.applied_signature==signature(p)+str(i.applied_materials) for i in targets)
        lines(box,'Цвета применены: {} из {}'.format(applied,len(targets)),context,'CHECKMARK' if applied==len(targets) and not s.preview_active else 'INFO')
    label='Применить к 1 модели' if len(targets)==1 else 'Применить к {} моделям'.format(len(targets)) if targets else 'Выберите модели-получатели'
    button(box,'color_prime.appearance',label,'CHECKMARK',available,True).action='APPLY'
    if s.appearance_mode=='PALETTE' and p:
        row=box.row(align=True)
        button(row,'color_prime.appearance','Посмотреть цвета','HIDE_OFF',bool(targets) and all(i.applied_appearance_uid for i in targets)).action='PREVIEW'
        if targets and not all(i.applied_appearance_uid for i in targets):lines(box,'Первое применение подготовит быстрый просмотр цветов.',context)
        if s.preview_active:button(row,'color_prime.appearance','Вернуть цвета','LOOP_BACK').action='PREVIEW_CANCEL'
    if s.preview_active:lines(box,'Предпросмотр: цвета ещё не применены. При сохранении .blend просмотр отменяется.',context,'INFO')
    if s.appearance_note:lines(box,'Последнее действие: '+s.appearance_note,context,'CHECKMARK')
    from .workspace_variants import draw as draw_variants
    draw_variants(layout,context,s)
    lines(layout,'Изменения сразу в сцене. Ctrl+Z — отменить; Ctrl+S — сохранить .blend.',context)
    layout.prop(s,'appearance_camera',text='Камера и свет',icon='TRIA_DOWN' if s.appearance_camera else 'TRIA_RIGHT',emboss=False)
    if s.appearance_camera:_camera(layout,context,s)
    layout.prop(s,'appearance_export',text='4. Экспорт PNG',icon='TRIA_DOWN' if s.appearance_export else 'TRIA_RIGHT',emboss=False)
    if s.appearance_export:
        lines(layout,'Модели рендерятся по одной. Остальные скрываются на время кадра.',context)
        if s.appearances:layout.prop(s,'appearance_palette_export')
        from .export_ui import draw_export
        draw_export(layout,context,s)
    layout.prop(s,'appearance_advanced',text='Выбрать, какие части перекрашивать',icon='TRIA_DOWN' if s.appearance_advanced else 'TRIA_RIGHT',emboss=False)
    if s.appearance_advanced:
        from .selected_colors import draw as selection_draw
        selection_draw(layout,context,s)
        layout.prop(s.studio,'zone_policy',text='Разметка')
        if s.studio.zone_policy=='REPLACE':
            layout.prop(s.studio,'replace_zones');layout.prop(s.studio,'replace_materials')
        layout.prop(s.studio,'match_similar_parts',text='Одинаковые детали — одинаковые материалы')
        if s.studio.stage_status in {'STAGED','RECOVERY'}:
            button(layout,'color_prime.stage_action','Вернуть исходную модель','LOOP_BACK').action='REVERT'


from .workspace_variants import CLASSES as VARIANT_CLASSES
from .workspace_palette_io import CLASSES as FILE_CLASSES
from .fast_track import CLASSES as FAST_CLASSES
from .updater import CLASSES as UPDATE_CLASSES
CLASSES=(COLORPRIME_OT_appearance,COLORPRIME_UL_appearances)+VARIANT_CLASSES+FILE_CLASSES+FAST_CLASSES+UPDATE_CLASSES
