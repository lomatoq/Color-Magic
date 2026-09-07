"""Scene model registry, physical collections, independent visibility and rollback."""
import json
import bpy
from bpy.props import EnumProperty,IntProperty
from .utils import objects_from_icon_item
from .studio_ops import idle


def eligible(obj):
    return obj.type in {'MESH','EMPTY','ARMATURE','CURVE','SURFACE','FONT','LATTICE'} and not obj.get('cp_studio_rig_owner','') and not (obj.type=='EMPTY' and obj.data)


def discover(scene,settings,selected=None):
    from .discovery import add_icon_root
    covered={o for i in settings.icon_sets for o in objects_from_icon_item(i,scene) if o.type=='MESH'}
    if selected is not None:
        roots=[]
        for obj in selected:
            if not eligible(obj):continue
            root=obj
            while root.parent and eligible(root.parent):root=root.parent
            if root not in roots:roots.append(root)
    else:
        # A dedicated collection wins; unrelated objects in a generic Collection
        # do not become one giant model simply because Blender imported them there.
        from .utils import iter_collection_tree
        for coll in iter_collection_tree(scene.collection):
            meshes={o for o in coll.all_objects if o.type=='MESH' and eligible(o)}
            if coll!=scene.collection and coll.get('color_prime_icon_set') and meshes and not meshes&covered:
                add_icon_root(settings,collection=coll);covered.update(meshes)
        roots=[o for o in scene.objects if eligible(o) and o.type=='EMPTY' and o.parent is None]
        roots+= [o for o in scene.objects if eligible(o) and o.type=='MESH']
    for root in sorted(roots,key=lambda o:(o.type!='EMPTY',o.name)):
        meshes={o for o in [root]+list(root.children_recursive) if o.type=='MESH' and eligible(o) and scene.objects.get(o.name)==o}
        if not meshes or meshes&covered:continue
        add_icon_root(settings,obj=root,auto_detected=selected is None);covered.update(meshes)
    settings.studio.guide_scope='SAVED'
    refresh(scene,settings)
    return len(settings.icon_sets)


def refresh(scene,settings):
    for item in settings.icon_sets:
        objects=objects_from_icon_item(item,scene)
        meshes=[o for o in objects if o.type=='MESH' and eligible(o)]
        if not meshes:item.status='Нет геометрии'
        elif any(o.library or o.override_library or o.data.library for o in meshes):item.status='Библиотека: только чтение'
        else:item.status='Подготовлена' if item.collection_root and item.object_root and item.collection_root.get('cp_model_collection',False) else 'Найдена'


def organize(scene,settings,items=None):
    from mathutils import Matrix
    from .utils import iter_collection_tree
    reachable=set(iter_collection_tree(scene.collection))
    st=settings.studio
    for item in (settings.icon_sets if items is None else items):
        if items is None and not item.enabled:continue
        members=[o for o in objects_from_icon_item(item,scene) if eligible(o)]
        meshes=[o for o in members if o.type=='MESH']
        if not meshes:continue
        if any(o.library or o.override_library or (o.data and o.data.library) for o in members):
            raise ValueError('Model is read-only: '+item.name)
        if item.collection_root and item.object_root and item.collection_root.get('cp_model_collection',False):
            item.object_root.name='Anchor_'+item.name
            continue
        for obj in members:
            if any(b.object==obj for b in st.model_backups):continue
            rec=st.model_backups.add();rec.object=obj;rec.parent=obj.parent;rec.original_name=obj.name
            rec.parent_type=obj.parent_type;rec.parent_bone=obj.parent_bone
            rec.matrix_basis=[v for row in obj.matrix_basis for v in row]
            rec.parent_inverse=[v for row in obj.matrix_parent_inverse for v in row]
            for coll in obj.users_collection:
                link=rec.collections.add()
                if coll==scene.collection:link.scene_root=True
                else:link.collection=coll
        # Reuse an existing collection only if it contains exactly this model.
        coll=item.collection_root if item.root_kind=='COLLECTION' else None
        if coll is None:
            coll=next((c for c in meshes[0].users_collection if c in reachable and set(c.all_objects)==set(members) and c!=scene.collection),None)
        created=st.model_created.add()
        if coll is None:
            coll=bpy.data.collections.new(item.name);scene.collection.children.link(coll);created.new_collection=True
        elif coll.users==1 and scene.collection.children.get(coll.name)!=coll:
            # Old guided selections were unlinked logical collections.
            scene.collection.children.link(coll)
        created.collection=coll;created.original_name=coll.name
        created.original_tags=json.dumps({k:coll[k] for k in ('cp_model_collection','color_prime_icon_set') if k in coll})
        if coll.name=='Collection' or coll.get('cp_guided_scope',False):coll.name=item.name
        coll['cp_model_collection']=True;coll['color_prime_icon_set']=True
        root=item.object_root if item.object_root and item.object_root.type=='EMPTY' else None
        if root is None:
            candidates=[o for o in members if o.type=='EMPTY' and o.parent not in members]
            root=candidates[0] if len(candidates)==1 else None
        if root is None:
            root=bpy.data.objects.new('Anchor_'+item.name,None);coll.objects.link(root);created.object=root
            points=[o.matrix_world.translation for o in meshes]
            root.location=sum(points,points[0]*0)/len(points)
            bpy.context.view_layer.update()
        root.name='Anchor_'+item.name
        for obj in members:
            world=obj.matrix_world.copy()
            if coll.objects.get(obj.name)!=obj:coll.objects.link(obj)
            for old in list(obj.users_collection):
                if old!=coll and old in reachable:old.objects.unlink(obj)
            if obj!=root and obj.parent not in members:
                obj.parent=root;obj.matrix_world=world
        item.root_kind='COLLECTION';item.collection_root=coll;item.object_root=root
    bpy.context.view_layer.update();refresh(scene,settings)


def restore_hierarchy(settings):
    from mathutils import Matrix
    st=settings.studio
    def matrix(values):return Matrix([values[i:i+4] for i in range(0,16,4)])
    for rec in st.model_backups:
        obj=rec.object
        if obj is None:continue
        if rec.original_name:obj.name=rec.original_name
        for saved in rec.collections:
            coll=settings.id_data.collection if saved.scene_root else saved.collection
            if coll and coll.objects.get(obj.name)!=obj:coll.objects.link(obj)
        originals={settings.id_data.collection if s.scene_root else s.collection for s in rec.collections}
        for coll in list(obj.users_collection):
            if coll not in originals:coll.objects.unlink(obj)
        obj.parent=rec.parent;obj.parent_type=rec.parent_type;obj.parent_bone=rec.parent_bone
        obj.matrix_parent_inverse=matrix(list(rec.parent_inverse));obj.matrix_basis=matrix(list(rec.matrix_basis))
    st.model_backups.clear()
    for rec in st.model_created:
        if rec.object and not rec.object.children:bpy.data.objects.remove(rec.object,do_unlink=True)
        if rec.collection:
            if rec.new_collection and not rec.collection.all_objects:bpy.data.collections.remove(rec.collection)
            elif not rec.new_collection:
                coll=rec.collection;coll.name=rec.original_name
                for key in ('cp_model_collection','color_prime_icon_set'):
                    if key in coll:del coll[key]
                for key,value in json.loads(rec.original_tags).items():coll[key]=value
    st.model_created.clear();bpy.context.view_layer.update()


class COLORPRIME_OT_models(bpy.types.Operator):
    bl_idname='color_prime.models';bl_label='Управление моделями';bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=tuple((x,x,'') for x in ('FIND','ADD','ALL','NONE','SELECT','ROOT','EYE','SOLO','RESTORE','REMOVE','PREPARE')))
    index:IntProperty(default=-1)
    @classmethod
    def poll(cls,context):return idle(context) and context.mode=='OBJECT'
    def execute(self,context):
        s=context.scene.color_prime;st=s.studio
        try:
            if self.action in {'FIND','ADD'}:
                discover(context.scene,s,None if self.action=='FIND' else context.selected_objects)
            elif self.action in {'ALL','NONE'}:
                for item in s.icon_sets:item.enabled=self.action=='ALL'
            elif self.action=='RESTORE':
                for name,value in json.loads(st.visibility_journal or '{}').items():
                    obj=context.scene.objects.get(name)
                    if obj:obj.hide_set(value)
                st.visibility_journal=''
            elif self.action=='PREPARE':
                from .family_links import ready,upgrade
                from . import transaction
                if st.stage_status=='NONE' and not ready(s):
                    st.guide_scope='SAVED';return bpy.ops.color_prime.workflow_prepare()
                from .transaction import extend
                from .utils import all_icon_objects
                from .authored_setup import mark_originals,analyze
                if st.stage_status=='RECOVERY':raise ValueError('Restore the interrupted setup first.')
                if st.stage_status=='NONE':
                    objects=[o for i in s.icon_sets if i.enabled and not (i.collection_root and i.collection_root.get('cp_model_collection',False)) for o in objects_from_icon_item(i,context.scene) if o.type=='MESH']
                    if not objects:raise ValueError('All checked models are already prepared. Add a new model first.')
                    before=transaction.snapshot_settings(s);st.reuse_family_library=True
                    transaction.start(context.scene,s,before,objects=objects)
                else:objects=extend(context.scene,s,all_icon_objects(s,context.scene,True))
                organize(context.scene,s)
                mark_originals(s,objects)
                from .scene_intelligence import auto_prepare_materials
                saved=st.surface_regions;st.surface_regions='OFF'
                try:auto_prepare_materials(context.scene,s,objects=[o for o in objects if not o.get('color_prime_authored_slots',False)])
                finally:st.surface_regions=saved
                analyze(context.scene,s,objects)
                from .family_links import ready,upgrade
                if ready(s) and not st.adoption_pending:upgrade(context.scene,s,objects)
                st.workflow_block='ZONES'
            elif 0<=self.index<len(s.icon_sets):
                s.icon_set_index=self.index;item=s.icon_sets[self.index]
                objects=objects_from_icon_item(item,context.scene)
                if self.action=='REMOVE':s.icon_sets.remove(self.index)
                elif self.action in {'EYE','SOLO'}:
                    if not st.visibility_journal:st.visibility_journal=json.dumps({o.name:o.hide_get() for o in context.view_layer.objects})
                    hidden=any(not o.hide_get() for o in objects)
                    targets={o for i in s.icon_sets for o in objects_from_icon_item(i,context.scene)} if self.action=='SOLO' else objects
                    for o in targets:
                        if context.view_layer.objects.get(o.name)==o:o.hide_set(o not in objects if self.action=='SOLO' else hidden)
                elif self.action in {'SELECT','ROOT'}:
                    for o in context.selected_objects:o.select_set(False)
                    chosen=[item.object_root] if self.action=='ROOT' and item.object_root else [o for o in objects if o.type=='MESH']
                    for o in chosen:
                        if context.view_layer.objects.get(o.name)==o:o.hide_set(False);o.select_set(True)
                    if chosen:context.view_layer.objects.active=chosen[0]
            refresh(context.scene,s);s.last_error='';return {'FINISHED'}
        except Exception as exc:
            s.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}


class COLORPRIME_UL_models(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index=0):
        row=layout.row(align=True);row.prop(item,'enabled',text='');row.prop(item,'name',text='',emboss=False)
        meshes=[o for o in objects_from_icon_item(item,context.scene) if o.type=='MESH']
        row.label(text=str(len(meshes)),icon='MESH_DATA')
        visible=any(not o.hide_get() for o in meshes)
        op=row.operator('color_prime.models',text='',icon='HIDE_OFF' if visible else 'HIDE_ON');op.action='EYE';op.index=index


def draw_models(layout,context,s):
    row=layout.row(align=True)
    for action,label in (('FIND','Найти модели'),('ADD','Добавить выбранное')):row.operator('color_prime.models',text=label).action=action
    if not s.icon_sets:return
    layout.template_list('COLORPRIME_UL_models','models',s,'icon_sets',s,'icon_set_index',rows=min(5,max(2,len(s.icon_sets))))
    row=layout.row(align=True)
    for action,label in (('ALL','Все'),('NONE','Ни одной'),('RESTORE','Вернуть видимость')):row.operator('color_prime.models',text=label).action=action
    if 0<=s.icon_set_index<len(s.icon_sets):
        item=s.icon_sets[s.icon_set_index]
        layout.label(text=(item.collection_root.name+' · ' if item.collection_root else '')+item.status)
        row=layout.row(align=True)
        for action,label in (('SELECT','Выделить'),('ROOT','Корень'),('SOLO','Только эта'),('REMOVE','Убрать')):
            op=row.operator('color_prime.models',text=label);op.action=action;op.index=s.icon_set_index
    layout.label(text='Галочка — обработка и экспорт; глаз — видимость.')
    layout.operator('color_prime.models',text='Подготовить отмеченные модели',icon='OUTLINER_COLLECTION').action='PREPARE'


CLASSES=(COLORPRIME_OT_models,COLORPRIME_UL_models)
