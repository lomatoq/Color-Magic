"""Small workspace operations; color preview never rebuilds geometry or shaders."""
import json
from array import array
import bpy


def used_materials(obj):
    values=array('i',[0])*len(obj.data.polygons)
    obj.data.polygons.foreach_get('material_index',values)
    return {obj.material_slots[i].material for i in set(values) if i<len(obj.material_slots) and obj.material_slots[i].material}


def compact_slots(scene,objects):
    """Remove unused UI slots, never the retained original material datablocks."""
    removed=0
    for obj in objects:
        mesh=obj.data
        if not mesh.polygons or len(mesh.materials)<=1:continue
        if any((getattr(m,'material',-1)>=0 if isinstance(getattr(m,'material',None),int) else False)
               or getattr(m,'material_offset',0)!=0 or getattr(m,'material_offset_rim',0)!=0 for m in obj.modifiers):continue
        if obj.particle_systems:continue
        indices=array('i',[0])*len(mesh.polygons);mesh.polygons.foreach_get('material_index',indices)
        kept=sorted(set(indices))
        if len(kept)==len(mesh.materials) or any(i>=len(mesh.materials) for i in kept):continue
        slots=[(obj.material_slots[i].link,obj.material_slots[i].material) for i in kept]
        active=obj.active_material_index
        if mesh.users>1:
            mesh=mesh.copy();obj.data=mesh
            for rec in scene.color_prime.studio.backup_objects:
                if rec.object==obj:rec.staged_mesh=mesh
        removed+=len(mesh.materials)-len(kept)
        mesh.materials.clear()
        for link,mat in slots:mesh.materials.append(mat)
        for slot,(link,mat) in zip(obj.material_slots,slots):slot.link=link;slot.material=mat
        mapping={old:new for new,old in enumerate(kept)}
        mesh.polygons.foreach_set('material_index',[mapping[i] for i in indices])
        obj.active_material_index=mapping.get(active,0);mesh.update()
    return removed


def secondary_regions(obj,entries):
    """Restore the surface adapter's established secondary-region policy."""
    from collections import defaultdict
    from .mesh_regions import RegionPlan
    from .surface_adapter import _similar_regions
    areas=defaultdict(float)
    for label,index,area in entries:areas[label]+=area
    order=sorted(areas,key=lambda x:(-areas[x],x))
    if len(order)<2:return set()
    remap={label:n for n,label in enumerate(order)}
    summary=json.loads(obj.get('color_prime_region_summary','{}'))
    method=summary.get('method','ISLANDS')
    aggregate=tuple(remap[label] for label in summary.get('aggregate_regions',[]) if label in remap)
    plan=RegionPlan(tuple(remap[label] for label,index,area in entries),tuple(areas[label] for label in order),method,'',1.,aggregate)
    candidates=[n for n in range(1,plan.count) if n not in aggregate]
    if not candidates:return set()
    seed=min(candidates,key=lambda n:(abs(plan.areas[n]/sum(plan.areas)-.22),n))
    picked=_similar_regions([tuple(obj.matrix_world@v.co) for v in obj.data.vertices],
        [tuple(obj.data.polygons[index].vertices) for label,index,area in entries],plan,seed)
    return {order[n] for n in picked}


def fast_sources(scene,items):
    from .appearance_workspace import meshes
    from .family_links import color_source
    objects={o for item in items for o in meshes(item,scene)}
    if not objects:return None
    sources={};protected=set()
    for obj in objects:
        if obj.library or obj.data.library:return None
        if not obj.data.materials or any(s.material is None for s in obj.material_slots):
            # Unused empty slots are allowed, but unpainted faces require setup.
            values=array('i',[0])*len(obj.data.polygons);obj.data.polygons.foreach_get('material_index',values)
            if any(i>=len(obj.material_slots) or obj.material_slots[i].material is None for i in set(values)):return None
        for mat in used_materials(obj):
            role=mat.get('color_prime_family','')
            if role in {'FIXED','IGNORE'} or mat.name.casefold().startswith(('gold','золот')):protected.add(mat);continue
            src=color_source(mat)
            if not src or src.tree.library or src.tree.get('cp_family_role') not in {'MAIN','ACCENT'}:return None
            role=src.tree['cp_family_role']
            sources[src.tree]=(mat,src,role)
    if not sources:return None
    # Reject any source referenced by a fixed part or an unchecked model,
    # including a nested use outside Base Color. The caller will copy on write.
    outsiders=protected|{slot.material for o in bpy.data.objects if o not in objects and o.users_scene for slot in o.material_slots if slot.material}
    outsiders|={r.material for sc in bpy.data.scenes if hasattr(sc,'color_prime') for p in sc.color_prime.appearances for r in p.materials if r.material}
    def overlaps(tree,seen):
        if tree in sources:return True
        if tree is None or tree in seen:return False
        seen.add(tree)
        return any(overlaps(n.node_tree,seen) for n in tree.nodes if n.type=='GROUP' and n.node_tree)
    if any(overlaps(m.node_tree,set()) for m in outsiders):return None
    return list(sources.values())


def recolor(scene,p,items,preview=False):
    from .appearance_workspace import primary,signature
    from . import runtime
    from .targets import TargetSnapshot
    if scene.color_prime.preview_active:
        runtime.restore_preview(scene)
        if scene.color_prime.preview_active:raise ValueError('Не удалось восстановить предыдущий просмотр.')
    sources=fast_sources(scene,items)
    if sources is None:return False
    snaps=[]
    for mat,src,role in sources:
        snaps.append(TargetSnapshot(mat.name,'','PATH_RGB' if src.group_path else 'RGB_OUTPUT',src.node.name,src.socket.name,
            list(src.node.outputs).index(src.socket),'','',tuple(src.socket.default_value),mat,json.dumps(list(src.group_path))))
    if preview:
        runtime._persist_preview(scene.color_prime,snaps)
        scene.color_prime.preview_active=True
        scene.color_prime.workspace_preview=True
    try:
        for mat,src,role in sources:src.socket.default_value=primary(p,role)
    except Exception:
        runtime.restore_snapshots(snaps)
        if preview:runtime._persist_preview(scene.color_prime,[]);scene.color_prime.preview_active=False
        raise
    if not preview:
        for item in items:
            item.applied_appearance=p.name;item.applied_appearance_uid=p.uid
            item.applied_signature=signature(p)+str(item.applied_materials)
    return True


def _refresh_preview():
    from .appearance_workspace import active_palette,meshes
    from .runtime import restore_preview
    for scene in bpy.data.scenes:
        s=getattr(scene,'color_prime',None)
        if not s or not s.workspace_preview or not s.preview_active or s.render_running:continue
        p=active_palette(s);items=[i for i in s.icon_sets if i.enabled and meshes(i,scene)]
        try:
            if s.appearance_mode!='PALETTE' or p is None or not items:restore_preview(scene)
            elif not recolor(scene,p,items,preview=True):s.appearance_note='Для нового выбора моделей сначала нажми «Применить».'
        except Exception as exc:
            restore_preview(scene);s.last_error=str(exc)
    return None


def queue_preview(self,context):
    scene=getattr(self,'id_data',None);s=getattr(scene,'color_prime',None)
    if s and s.workspace_preview and s.preview_active and not s.render_running and not bpy.app.timers.is_registered(_refresh_preview):
        bpy.app.timers.register(_refresh_preview,first_interval=.12)


def prepare(context,item):
    from .model_library import organize
    from .appearance_workspace import meshes
    if not meshes(item,context.scene):raise ValueError('Модель удалена. Импортируй её и нажми «Найти модели».')
    organize(context.scene,context.scene.color_prime,items=[item])
    from .family_links import color_source
    from .material_names import name_material
    for obj in meshes(item,context.scene):
        for mat in used_materials(obj):
            if mat.library or mat.get('color_prime_generated_name'):continue
            src=color_source(mat)
            role=mat.get('color_prime_family') or (src.tree.get('cp_family_role') if src else None)
            if role not in {'MAIN','ACCENT'}:continue
            mat['cp_workspace_original_name']=mat.name
            name_material(mat,role)


def find_zones(context,item):
    from .appearance_workspace import meshes
    from .surface_adapter import REGION_ATTRIBUTE,sharp_edges
    from .mesh_regions import segment
    s=context.scene.color_prime
    objects=meshes(item,context.scene)
    if any(o.library or o.data.library for o in objects):raise ValueError('Сначала сделай модель локальной.')
    pending=[];automatic=False
    for obj in objects:
        existing=obj.data.attributes.get(REGION_ATTRIBUTE)
        materials=used_materials(obj)
        single=len(objects)==1 and len(materials)<=1
        summary=json.loads(obj.get('color_prime_region_summary','{}'))
        repair_aggregate=summary.get('method')=='ISLAND_GROUPS' and item.zones_origin.startswith('Зоны найдены автоматически')
        if existing and not repair_aggregate and (not single or len({v.value for v in existing.data})>1):continue
        # Multiple painted regions or explicit family assignments are authored.
        authored=not repair_aggregate and not single and (len(materials)>1 or any(m.get('color_prime_family') or m.get('cp_adopted') for m in materials))
        if authored:values=[p.material_index for p in obj.data.polygons]
        else:
            region=segment([tuple(obj.matrix_world@v.co) for v in obj.data.vertices],
                [tuple(p.vertices) for p in obj.data.polygons],
                [tuple(e.vertices) for e in obj.data.edges if e.use_seam],sharp_edges(obj.data),
                s.studio.surface_regions if s.studio.surface_regions!='OFF' else 'AUTO',s.studio.region_max)
            if not region.labels:raise ValueError(region.note if hasattr(region,'note') else 'Слишком сложная сетка для автопоиска. Выдели зоны вручную.')
            values=region.labels;automatic=True
            obj['color_prime_region_summary']=json.dumps({'method':region.method,'count':region.count,'aggregate_regions':region.aggregate_regions})
        pending.append((obj,values,not authored))
    prepare(context,item)
    for obj,values,inferred in pending:
        obj.data=obj.data.copy()
        if inferred:obj['cp_workspace_auto_regions']=True
        attr=obj.data.attributes.get(REGION_ATTRIBUTE) or obj.data.attributes.new(name=REGION_ATTRIBUTE,type='INT',domain='FACE');attr.data.foreach_set('value',values)
        if s.studio.stage_status=='STAGED':
            for rec in s.studio.backup_objects:
                if rec.object==obj:rec.staged_mesh=obj.data
    if pending:item.zones_origin='Зоны найдены автоматически' if automatic else 'Готовые зоны сохранены'
    item.workspace_zones_checked=True
    from .appearance_workspace import _status_counts
    _status_counts.clear()
    return sum(len({v.value for v in o.data.attributes[REGION_ATTRIBUTE].data}) for o in objects if o.data.attributes.get(REGION_ATTRIBUTE))
