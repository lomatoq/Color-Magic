"""Family child library and explicit assignment on reversible working meshes.

Color follows Main/Accent at every preview/export. Surface inheritance is an
explicit Sync operation so editing a parent never silently rewrites a shader.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from .studio_ops import idle
from .guided_state import tr
from .transaction import OWNER_KEY, protected

PROFILES = (('SAME','Same as parent','Same family color and surface'),
            ('SHADES','Light / dark shades','Alternating lighter and darker family children'),
            ('SURFACES','Surface collection','Alternating matte, glossy and metallic children'),
            ('MATTE','Matte','Roughness 0.8'), ('GLOSS','Glossy','Roughness 0.18'),
            ('METAL','Metallic','Metallic 1, roughness 0.28'))


def _principled(mat):
    from .family_links import principled
    return principled(mat)

def child_color(material, value):
    profile = material.get('color_prime_child_profile', 'SAME')
    if profile not in {'LIGHT', 'DARK'}:
        return value
    return tuple(v*.82+.18 if profile=='LIGHT' else v*.78 for v in value[:3])+(value[3],)


def _surface(child, parent, profile):
    dest, source = _principled(child), _principled(parent)
    if dest is None:
        return
    overrides = {'MATTE': {'Roughness': .8}, 'GLOSS': {'Roughness': .18},
                 'METAL': {'Metallic': 1., 'Roughness': .28}}.get(profile, {})
    for inp in source.inputs if source else ():
        target = dest.inputs.get(inp.name)
        if inp.name == 'Base Color' or target is None or inp.is_linked or target.is_linked:
            continue
        if hasattr(inp, 'default_value') and hasattr(target, 'default_value'):
            target.default_value = inp.default_value
    for name, value in overrides.items():
        socket = dest.inputs.get(name)
        if socket and socket.is_linked:
            raise ValueError('Parent drives {} with a node; choose Same as parent or a simpler parent.'.format(name))
        if socket:
            socket.default_value = value


def _ready(settings, obj=None):
    if settings.studio.stage_status=='NONE':
        from . import transaction
        from .family_links import ready
        if ready(settings):
            before=transaction.snapshot_settings(settings);settings.studio.reuse_family_library=True
            transaction.start(settings.id_data,settings,before)
    if settings.studio.stage_status != 'STAGED':
        raise ValueError('Prepare the model first to retain its original materials.')
    if obj is not None and (obj.type != 'MESH' or obj.data.get(OWNER_KEY,'') != settings.studio.stage_id):
        raise ValueError('Select a mesh included in the current prepared model.')


def create_children(scene, settings, family, count, profile, prefix='', shader='KEEP'):
    from .runtime import restore_preview
    from .targets import find_material_target
    from .discovery import scan_material_bindings
    _ready(settings)
    if not 1 <= count <= 64:
        raise ValueError('Choose between 1 and 64 children.')
    restore_preview(scene)
    if settings.preview_active:
        raise ValueError('Restore the previous preview before creating children.')
    from .material_names import name_material,normalize_generated_names
    normalize_generated_names(settings)
    from .family_links import upgrade,connect
    upgrade(scene,settings)
    parent_attr = 'main_parent_material' if family == 'MAIN' else 'accent_parent_material'
    parent = getattr(settings, parent_attr)
    created_parent = False
    made = []
    old_count = len(settings.children)
    try:
        if parent is None:
            reference = settings.main_reference_color if family == 'MAIN' else settings.accent_reference_color
            parent = bpy.data.materials.new('{} Parent'.format(family.title()))
            name_material(parent,family,'PARENT')
            parent.use_nodes = True
            _principled(parent).inputs['Base Color'].default_value = reference
            parent[OWNER_KEY] = settings.studio.stage_id
            parent['color_prime_parent'] = family
            setattr(settings, parent_attr, parent)
            created_parent = True
        if protected(parent,settings) or parent.get('color_prime_child',False):
            raise ValueError('Choose an unprotected parent material, not another child.')
        if not find_material_target(parent,False).writable:
            raise ValueError('Parent color is texture driven or ambiguous; select a writable family parent.')
        for index in range(count):
            style = (('LIGHT','DARK')[index % 2] if profile == 'SHADES' else
                     ('MATTE','GLOSS','METAL')[index % 3] if profile == 'SURFACES' else profile)
            mat = parent.copy(); made.append(mat)
            name_material(mat,family,style,prefix)
            mat[OWNER_KEY] = settings.studio.stage_id
            mat['color_prime_family'] = family
            mat['color_prime_family_source'] = 'MANUAL'
            mat['color_prime_locked'] = True
            mat['color_prime_child'] = True
            mat['color_prime_child_profile'] = style
            connect(mat,settings,family,style)
            if shader!='KEEP':
                from .material_controls import change_shader
                change_shader(mat,settings,family,shader)
            # Resolve/localize only this copy. Nested shared color groups must
            # not become cross-child write targets.
            find_material_target(mat,True)
            if style in {'MATTE','GLOSS','METAL'} and shader in {'KEEP','PRINCIPLED'}:
                _surface(mat,parent,style)
            item = settings.children.add()
            item.material, item.family, item.profile = mat, family, style
        scan_material_bindings(scene, settings, False)
        for binding in settings.bindings:
            if binding.material in made:
                binding.inheritance_mode = 'DIRECT'
        settings.child_index = old_count
        return made
    except Exception:
        while len(settings.children) > old_count:
            settings.children.remove(len(settings.children)-1)
        # Clear bindings referencing these allocations before reclaiming them.
        for index in reversed(range(len(settings.bindings))):
            if settings.bindings[index].material in made:
                settings.bindings.remove(index)
        for mat in made:
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        if created_parent:
            setattr(settings,parent_attr,None)
            if parent.users == 0:
                bpy.data.materials.remove(parent)
        raise


def assign_child(scene, settings, obj, material, indices):
    from .runtime import restore_preview
    _ready(settings,obj)
    if material is None or not any(c.material == material for c in settings.children):
        raise ValueError('Select a child from this model library.')
    restore_preview(scene)
    if settings.preview_active:
        raise ValueError('Previous preview could not be restored.')
    mesh = obj.data
    faces = [mesh.polygons[i] for i in sorted(set(indices)) if 0 <= i < len(mesh.polygons)]
    allowed = []
    protection={slot.material:protected(slot.material,settings) for slot in obj.material_slots if slot.material}
    for face in faces:
        source = obj.material_slots[face.material_index].material if face.material_index < len(obj.material_slots) else None
        if source is None or not protection.get(source,False):
            allowed.append(face)
    if not allowed:
        raise ValueError('No assignable faces: selection is empty or all selected materials are protected.')
    # Use the slot's effective material, including OBJECT-linked slots.
    slot = next((i for i,s in enumerate(obj.material_slots) if s.material == material),None)
    if slot is None:
        mesh.materials.append(material); slot = len(mesh.materials)-1
    for face in allowed:
        face.material_index = slot
    obj.active_material_index = slot
    mesh.update()
    return len(allowed), len(faces)-len(allowed)


def _refresh(context):
    from .discovery import scan_material_bindings
    from .lookbook import preview
    s = context.scene.color_prime
    scan_material_bindings(context.scene,s,False)
    from .family_links import ready
    if s.studio.looks and not ready(s) and not s.studio.adoption_pending:preview(context.scene,s)
    for area in context.screen.areas if context.screen else ():
        area.tag_redraw()


def _error(op,context,exc):
    context.scene.color_prime.last_error = str(exc)
    op.report({'ERROR'},str(exc))
    return {'CANCELLED'}


class COLORPRIME_OT_create_children(bpy.types.Operator):
    from .material_controls import SHADERS
    shader: EnumProperty(name='Тип шейдера',items=SHADERS,default='PRINCIPLED')
    bl_idname='color_prime.create_children'; bl_label='Create Child Materials'; bl_options={'REGISTER','UNDO'}
    family: EnumProperty(name='Parent family',items=(('MAIN','Main',''),('ACCENT','Accent','')),default='MAIN')
    count: IntProperty(name='Number of children',default=3,min=1,max=64)
    profile: EnumProperty(name='Material surfaces',items=PROFILES,default='SURFACES')
    prefix: StringProperty(name='Name prefix',default='')
    assign_now: BoolProperty(name='Assign to model after creating',default=True)
    @classmethod
    def poll(cls,context):
        from .family_links import ready
        s=context.scene.color_prime
        return idle(context) and s.studio.stage_status!='RECOVERY' and (s.studio.stage_status=='STAGED' or ready(s)) and context.mode=='OBJECT'
    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self,width=420)
    def draw(self,context):
        layout=self.layout
        for field in ('family','count','shader','profile','prefix','assign_now'):
            layout.prop(self,field)
        s=context.scene.color_prime
        layout.label(text=tr(s,'Materials appear in the list with their assigned zone count.',
            'Матэрыялы з’явяцца ў спісе з колькасцю прызначаных зон.'),icon='INFO')
        if self.assign_now:layout.prop(s.studio,'assignment_scope',text=tr(s,'Apply to','Куды'))
    def execute(self,context):
        try:
            mats=create_children(context.scene,context.scene.color_prime,self.family,self.count,self.profile,self.prefix,self.shader)
            s=context.scene.color_prime
            if self.assign_now:auto_assign(context.scene,s,assignment_objects(context),find_missing=False)
            _refresh(context);s.studio.color_tools='MATERIALS';s.studio.workflow_block='COLORS'
            s.last_error=''
            self.report({'INFO'},'Created {} {} materials{}.'.format(len(mats),self.family,' and assigned them' if self.assign_now else ' in the material list'))
            return {'FINISHED'}
        except Exception as exc:
            return _error(self,context,exc)


class COLORPRIME_OT_assign_child(bpy.types.Operator):
    bl_idname='color_prime.assign_child'; bl_label='Assign Child Material'; bl_options={'REGISTER','UNDO'}
    source: EnumProperty(items=(('REGION','Selected region',''),('FACES','Selected faces',''),('OBJECT','Whole active mesh','')),default='REGION')
    @classmethod
    def poll(cls,context):
        return idle(context) and context.active_object is not None and context.active_object.type=='MESH' and bool(context.scene.color_prime.children)
    def execute(self,context):
        s=context.scene.color_prime; obj=context.active_object; was_edit=obj.mode=='EDIT'
        try:
            if not 0 <= s.child_index < len(s.children):
                raise ValueError('Select a child material.')
            if self.source=='FACES':
                if not was_edit:
                    raise ValueError('Tab into Edit Mode and select faces or highlight a region first.')
                import bmesh
                bm=bmesh.from_edit_mesh(obj.data); bm.faces.ensure_lookup_table(); bm.faces.index_update()
                indices=[f.index for f in bm.faces if f.select and not f.hide]
                bpy.ops.object.mode_set(mode='OBJECT')
            elif was_edit:
                raise ValueError('Use Selected Faces in Edit Mode.')
            elif self.source=='OBJECT':
                indices=range(len(obj.data.polygons))
            else:
                from .surface_adapter import REGION_ATTRIBUTE
                attr=obj.data.attributes.get(REGION_ATTRIBUTE)
                if attr is None:
                    raise ValueError('Find regions first, or use selected faces.')
                indices=[i for i,v in enumerate(attr.data) if v.value==s.studio.region_index-1]
            count,skipped=assign_child(context.scene,s,obj,s.children[s.child_index].material,indices)
            _refresh(context); s.last_error=''
            self.report({'INFO'},'Assigned {} faces; {} protected faces kept.'.format(count,skipped))
            return {'FINISHED'}
        except Exception as exc:
            return _error(self,context,exc)
        finally:
            if was_edit and obj.mode!='EDIT':
                bpy.ops.object.mode_set(mode='EDIT')


class COLORPRIME_OT_find_regions(bpy.types.Operator):
    bl_idname='color_prime.find_regions'; bl_label='Find Regions on Active Mesh'; bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return idle(context) and context.mode=='OBJECT' and context.active_object is not None and context.active_object.type=='MESH' and context.scene.color_prime.studio.stage_status=='STAGED'
    def execute(self,context):
        try:
            from .surface_adapter import auto_regions,region_count
            from .runtime import restore_preview
            s=context.scene.color_prime; obj=context.active_object
            _ready(s,obj)
            if region_count(obj):
                self.report({'INFO'},'Existing regions retained. Use Highlight and assign a child.')
                return {'FINISHED'}
            restore_preview(context.scene)
            if s.preview_active:
                raise ValueError('Restore the previous preview first.')
            s.studio.region_note=''
            count=auto_regions(obj,s,explicit=True)
            _refresh(context)
            self.report({'INFO' if count else 'WARNING'},s.studio.region_note or 'Enable automatic regions first.')
            return {'FINISHED'}
        except Exception as exc:
            return _error(self,context,exc)


class COLORPRIME_OT_sync_children(bpy.types.Operator):
    bl_idname='color_prime.sync_children'; bl_label='Sync Parent Surfaces'; bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return idle(context) and context.mode=='OBJECT' and bool(context.scene.color_prime.children)
    def execute(self,context):
        try:
            s=context.scene.color_prime
            from .runtime import restore_preview
            restore_preview(context.scene)
            if s.preview_active:
                raise ValueError('Restore the previous preview first.')
            done=0; skipped=0
            for item in s.children:
                if not item.material or not item.follow_surface:
                    continue
                parent=s.main_parent_material if item.family=='MAIN' else s.accent_parent_material
                if parent is None or parent==item.material or _principled(parent) is None or _principled(item.material) is None:
                    skipped+=1; continue
                _surface(item.material,parent,item.profile); done+=1
            _refresh(context); s.last_error=''
            self.report({'INFO'},'Synced {} children; {} need a compatible parent.'.format(done,skipped))
            return {'FINISHED'}
        except Exception as exc:
            return _error(self,context,exc)


def assignment_objects(context):
    from .utils import all_icon_objects
    s=context.scene.color_prime
    objects=(context.selected_objects if s.studio.assignment_scope=='SELECTED' else all_icon_objects(s,context.scene,True))
    return sorted([o for o in objects if o.type=='MESH' and o.data.get(OWNER_KEY,'')==s.studio.stage_id],key=lambda o:o.name)


def assignment_plan(settings,objects):
    """Read-only proposals on existing region labels, with stable group ownership."""
    from collections import defaultdict
    from .surface_adapter import REGION_ATTRIBUTE
    from .shape_matching import describe,group_shapes
    meshes=sorted([o for o in objects if o.type=='MESH' and o.data.get(OWNER_KEY,'')==settings.studio.stage_id],key=lambda o:o.name)
    library={family:[c.material for c in settings.children if c.material and c.family==family and c.auto_assign]
             for family in ('MAIN','ACCENT')}
    roles={b.material:b.family for b in settings.bindings if b.material and b.enabled and b.target_kind!='NONE'}
    units=[];skipped=0;uncovered=set()
    for obj in meshes:
        attr=obj.data.attributes.get(REGION_ATTRIBUTE)
        groups=defaultdict(list)
        protection={slot.material:protected(slot.material,settings) for slot in obj.material_slots if slot.material}
        for face in obj.data.polygons:
            mat=obj.material_slots[face.material_index].material if face.material_index<len(obj.material_slots) else None
            if mat is not None and protection.get(mat,False):skipped+=1;continue
            family=roles.get(mat,'MAIN' if mat is None else '')
            if family not in library or not library[family]:
                skipped+=1
                if family in library:uncovered.add(family)
                continue
            region=attr.data[face.index].value if attr is not None else face.material_index
            groups[(region,family)].append(face.index)
        import numpy as np
        vertices=np.asarray([tuple(obj.matrix_world@v.co) for v in obj.data.vertices])
        for (region,family),indices in groups.items():
            shape=describe(vertices,[tuple(obj.data.polygons[i].vertices) for i in indices]) if settings.studio.match_similar_parts else None
            area=shape.area if shape is not None else sum(obj.data.polygons[i].area for i in indices)
            units.append({'object':obj,'region':region,'family':family,'faces':indices,'area':area,'shape':shape})
    units.sort(key=lambda u:(-u['area'],u['object'].name,u['region'],u['family']))
    operations=[];cluster_rows=[];comparisons=0;limit=False
    object_area=defaultdict(float)
    for unit in units:object_area[unit['object']]+=unit['area']
    for family in ('MAIN','ACCENT'):
        selected=[u for u in units if u['family']==family]
        # Tiny islands remain distinct selectable zones. Give them the family
        # base surface instead of cycling random finishes across mesh debris.
        # Do not run quadratic shape fitting on sub-percent fragments.
        detail=[i for i,u in enumerate(selected) if u['area']<object_area[u['object']]*.005] if settings.studio.match_similar_parts else []
        major=[i for i in range(len(selected)) if i not in detail]
        groups,info=group_shapes([selected[i]['shape'] for i in major]) if settings.studio.match_similar_parts else (
            [[i] for i in range(len(selected))],{'comparisons':0,'limit_reached':False})
        if settings.studio.match_similar_parts:
            groups=[[major[i] for i in g] for g in groups]
            if detail:groups.append(detail)
        comparisons+=info['comparisons'];limit=limit or info['limit_reached']
        for group_index,group in enumerate(groups):
            mat=library[family][0 if group==detail else group_index%len(library[family])]
            members=[]
            for index in group:
                unit=selected[index]
                operations.append((unit['object'],mat,unit['faces']))
                members.append({'object':unit['object'].name,'region':unit['region']+1,'faces':len(unit['faces'])})
            cluster_rows.append({'family':family,'material':mat.name,'members':members,'detail_fallback':group==detail})
    return operations,{'meshes':len(meshes),'zones':len(units),'groups':len(cluster_rows),
        'similar_groups':sum(len(g['members'])>1 and not g['detail_fallback'] for g in cluster_rows),'faces':sum(len(u['faces']) for u in units),
        'skipped_faces':skipped,'uncovered_families':sorted(uncovered),'comparisons':comparisons,
        'limit_reached':limit,'assignments':cluster_rows}


def auto_assign(scene,settings,objects,find_missing=True,local_only=False):
    """Use authored zones first, inferred regions second, whole mesh otherwise.

    Family classification is a separate stage: geometry never reclassifies an
    existing explicit material family. Cycle checked children within each family.
    """
    from .surface_adapter import auto_regions,region_count,REGION_ATTRIBUTE
    from .discovery import scan_material_bindings
    from .runtime import restore_preview
    _ready(settings)
    meshes=[o for o in objects if o.type=='MESH' and o.data.get(OWNER_KEY,'')==settings.studio.stage_id]
    if not meshes:
        raise ValueError('Select meshes from the prepared model.')
    library={family:[c.material for c in settings.children if c.material and c.family==family and c.auto_assign]
             for family in ('MAIN','ACCENT')}
    if not any(library.values()):
        raise ValueError('Check at least one child for automatic assignment.')
    restore_preview(scene)
    if settings.preview_active:raise ValueError('Restore the previous preview first.')
    if find_missing:
        for obj in meshes:
            if not region_count(obj):auto_regions(obj,settings,explicit=True)
    scan_material_bindings(scene,settings,False,local_only=local_only)
    operations,result=assignment_plan(settings,meshes)
    for obj,material,indices in operations:
        assign_child(scene,settings,obj,material,indices)
    from .family_links import compact_slots
    for obj in meshes:compact_slots(obj)
    import json
    settings.studio.assignment_details=json.dumps(result)
    settings.studio.assignment_summary=tr(settings,'{} zones · {} similar groups kept together'.format(result['zones'],result['similar_groups']),
        '{} зон · падобных груп разам: {}'.format(result['zones'],result['similar_groups']))
    return result


class COLORPRIME_OT_auto_assign_children(bpy.types.Operator):
    bl_idname='color_prime.auto_assign_children';bl_label='Apply Distribution';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return idle(context) and context.mode=='OBJECT' and context.scene.color_prime.studio.stage_status=='STAGED' and bool(context.scene.color_prime.children)
    def invoke(self,context,event):
        try:
            s=context.scene.color_prime
            objects=assignment_objects(context)
            if not objects:raise ValueError(tr(s,'Select prepared meshes, or choose Prepared model.','Выберы падрыхтаваныя mesh або ўсю мадэль.'))
            _,self._proposal=assignment_plan(s,objects)
            if not self._proposal['faces']:raise ValueError(tr(s,'Check children for the families used on these parts.','Адзнач нашчадкаў патрэбнага сямейства.'))
            return context.window_manager.invoke_props_dialog(self,width=440)
        except Exception as exc:return _error(self,context,exc)
    def draw(self,context):
        s=context.scene.color_prime;p=getattr(self,'_proposal',{})
        for en,be in [('Meshes: {}','Mesh: {}'),('Zones: {}','Зоны: {}'),('Groups with matching parts: {}','Групы падобных частак: {}')]:
            key=('meshes' if en.startswith('Meshes') else 'zones' if en.startswith('Zones') else 'similar_groups')
            self.layout.label(text=tr(s,en,be).format(p.get(key,0)))
        if p.get('uncovered_families'):
            self.layout.label(text=tr(s,'No children — keep: ','Без нашчадкаў, пакідаем: ')+', '.join(p['uncovered_families']),icon='INFO')
        self.layout.label(text=tr(s,'Matching parts receive the same variant.','Падобныя часткі атрымаюць адзін варыянт.'))
        self.layout.label(text=tr(s,'Different shapes cycle through checked variants.','Розным формам — адзначаныя варыянты па чарзе.'))
        self.layout.label(text=tr(s,'Fixed materials remain unchanged.','Абароненыя матэрыялы застануцца.'))
        from collections import Counter
        counts=Counter()
        for row in p.get('assignments',[]):counts[row['material']]+=len(row['members'])
        self.layout.separator()
        for name,count in list(counts.items())[:12]:
            self.layout.label(text='{}: {} {}'.format(name,count,tr(s,'parts','частак')))
        if len(counts)>12:self.layout.label(text=tr(s,'And {} more materials','І яшчэ {} матэрыялаў').format(len(counts)-12))
        if p.get('limit_reached'):
            self.layout.label(text=tr(s,'Matching limit reached; remaining parts stay separate.',
                'Ліміт параўнання: астатнія часткі асобна.'),icon='INFO')
    def execute(self,context):
        try:
            s=context.scene.color_prime
            objects=assignment_objects(context)
            # The visible proposal must not secretly create additional zones.
            result=auto_assign(context.scene,s,objects,find_missing=False)
            _refresh(context);s.last_error=''
            self.report({'INFO'},'{} zones, {} meshes, {} faces assigned; families without checked children kept.'.format(result['zones'],result['meshes'],result['faces']))
            return {'FINISHED'}
        except Exception as exc:return _error(self,context,exc)


class COLORPRIME_UL_children(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index=0):
        layout.prop(item,'auto_assign',text='')
        if item.material:
            layout.prop(item.material,'name',text='',emboss=False,icon='MATERIAL')
        else:
            layout.label(text='Missing material',icon='ERROR')
        from .family_ui import usage
        count=usage(item.material,context.scene.color_prime,context.scene) if item.material else 0
        layout.label(text=str(count) if count else tr(context.scene.color_prime,'Unused','Не прызн.'))


def draw_library(layout,context):
    from .guided_ui import lines
    from .surface_adapter import region_count
    s=context.scene.color_prime; st=s.studio; obj=context.active_object
    layout.operator('color_prime.create_children',text=tr(s,'Create Children…','Стварыць нашчадкаў…'),icon='DUPLICATE')
    if st.stage_status!='STAGED':
        lines(layout,tr(s,'Prepare the model to create and assign children.','Спачатку падрыхтуй мадэль.'),context)
        return
    parents=layout.column(align=True)
    parents.prop(s,'main_parent_material',text='Main')
    parents.prop(s,'accent_parent_material',text='Accent')
    layout.operator('color_prime.sync_children',text=tr(s,'Sync Parent Surfaces','Абнавіць уласцівасці бацькоў'),icon='FILE_REFRESH')
    if s.children:
        layout.template_list('COLORPRIME_UL_children','',s,'children',s,'child_index',rows=4)
        layout.operator('color_prime.auto_assign_children',text=tr(s,'Auto Assign Checked Children','Аўта: прызначыць адзначаных'),icon='MATERIAL')
        lines(layout,tr(s,'Uses existing zones or separate meshes first. Finds geometric zones when missing. Unchecked children are excluded.',
                         'Выкарыстоўвае існыя зоны і асобныя mesh. Калі зон няма — шукае па геаметрыі. Неадзначаныя нашчадкі выключаныя.'),context)
        if 0 <= s.child_index < len(s.children):
            item=s.children[s.child_index]
            layout.label(text='{} · {}'.format(item.family.title(),item.profile.title()))
            layout.prop(item,'follow_surface',text=tr(s,'Inherit surface on Sync','Наследаваць паверхню пры абнаўленні'))
            binding=next((b for b in s.bindings if b.material==item.material),None)
            if binding:
                layout.prop(binding,'enabled',text=tr(s,'Follow family color','Наследаваць колер сямейства'))
    if obj is None or obj.type!='MESH':
        lines(layout,tr(s,'Select the model mesh to assign children.','Выберы mesh мадэлі для прызначэння.'),context)
        return
    zones=region_count(obj)
    box=layout.box()
    box.prop(st,'region_max',text=tr(s,'Maximum regions','Максімум зон'))
    box.operator('color_prime.find_regions',text=tr(s,'Find Regions','Знайсці зоны'),icon='MESH_DATA')
    if zones:
        box.label(text=tr(s,'{} automatic regions'.format(zones),'{} аўтаматычных зон'.format(zones)))
        box.prop(st,'region_index',text=tr(s,'Region','Зона'))
        box.operator('color_prime.show_region',text=tr(s,'Highlight Region','Вылучыць зону'),icon='FACESEL')
    lines(box,tr(s,'Largest surface is suggested as Main. Accent is a smaller region. Review with Highlight.',
                   'Найбольшая паверхня прапануецца як Main, меншая зона — Accent. Правер праз вылучэнне.'),context)
    if s.children:
        for scope,en,be in [('REGION','Assign to Region','Прызначыць зоне'),('FACES','Assign to Selected Faces','Прызначыць выбраным граням'),('OBJECT','Assign to Whole Mesh','Прызначыць усяму mesh')]:
            row=layout.row()
            row.enabled=(context.mode=='EDIT_MESH' if scope=='FACES' else context.mode=='OBJECT' and (bool(zones) if scope=='REGION' else True))
            row.operator('color_prime.assign_child',text=tr(s,en,be)).source=scope
    lines(layout,tr(s,'Color follows Main / Accent live. Surface changes use Sync. Creating children alone does not assign faces.',
                     'Колер змяняецца разам з Main / Accent. Уласцівасці паверхні — праз абнаўленне. Стварэнне не прызначае грані.'),context)


class COLORPRIME_PT_children(bpy.types.Panel):
    bl_label='Main / Accent · Child Materials'; bl_idname='COLORPRIME_PT_children'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Color Prime'
    @classmethod
    def poll(cls,context):return context.scene.color_prime.studio.ui_mode=='EXPERT'
    def draw(self,context):
        self.layout.enabled=idle(context)
        draw_library(self.layout,context)


CLASSES=(COLORPRIME_OT_create_children,COLORPRIME_OT_assign_child,COLORPRIME_OT_find_regions,
         COLORPRIME_OT_sync_children,COLORPRIME_OT_auto_assign_children,COLORPRIME_UL_children,COLORPRIME_PT_children)
