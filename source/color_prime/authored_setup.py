"""Adopt authored assignments before any geometry inference. Working IDs only."""
import json
import bpy
from .transaction import OWNER_KEY, protected


def isolate_graphs(settings, materials):
    # One copy per graph for the entire transaction preserves real sharing.
    memo={}
    for g in bpy.data.node_groups:
        if g.get(OWNER_KEY,'')==settings.studio.stage_id and g.get('cp_original_tree',''):
            memo[g['cp_original_tree']]=g
    def copy_tree(tree):
        if tree.get(OWNER_KEY,'')==settings.studio.stage_id:return tree
        from .utils import data_block_identity
        key=data_block_identity(tree)
        if key in memo:return memo[key]
        clone=tree.copy();memo[key]=clone
        clone[OWNER_KEY]=settings.studio.stage_id;clone['cp_original_tree']=key
        for n in clone.nodes:
            if n.type=='GROUP' and n.node_tree:n.node_tree=copy_tree(n.node_tree)
        return clone
    for mat in materials:
        if mat is None or protected(mat,settings) or mat.get(OWNER_KEY,'')!=settings.studio.stage_id:continue
        if mat.node_tree:
            for n in mat.node_tree.nodes:
                if n.type=='GROUP' and n.node_tree:n.node_tree=copy_tree(n.node_tree)


def mark_originals(settings, objects):
    originals={r.object:r for r in settings.studio.backup_objects}
    for obj in objects:
        rec=originals.get(obj)
        if rec and 'color_prime_authored_slots' not in obj:
            obj['color_prime_authored_slots']=any(s.material and s.material.get('color_prime_family_source','')!='AUTO_GEOMETRY' for s in rec.slots)


def analyze(scene,settings,objects):
    from .family_links import color_source,ready
    from .discovery import scan_material_bindings
    mats={slot.material for obj in objects for slot in obj.material_slots if slot.material}
    isolate_graphs(settings,mats)
    sources={m:color_source(m) for m in mats if not protected(m,settings)}
    sources={m:v for m,v in sources.items() if v is not None}
    branches={}
    for mat,src in sources.items():
        if src.group_path and src.is_output:
            branches.setdefault((src.tree.as_pointer(),src.node.name,src.socket.identifier),[]).append(mat)
    structural=bool(sources) and len(branches)==2 and sum(map(len,branches.values()))==len(sources)
    if structural:
        weights={m:0. for m in mats}
        for obj in objects:
            for face in obj.data.polygons:
                if face.material_index<len(obj.material_slots):
                    mat=obj.material_slots[face.material_index].material
                    if mat:weights[mat]+=face.area
        groups=sorted(branches.values(),key=lambda ms:(-sum(weights[m] for m in ms),ms[0].name))
        hints=[]
        for ms in groups:
            roles={m.get('color_prime_family','') for m in ms}-{''}
            text=' '.join(m.name+' '+sources[m].tree.name for m in ms).casefold()
            hints.append(next(iter(roles)) if len(roles)==1 else 'ACCENT' if 'accent' in text else 'MAIN' if 'main' in text else '')
        roles=['MAIN','ACCENT']
        if hints[0]=='ACCENT' or hints[1]=='MAIN':roles.reverse()
        for ms,role in zip(groups,roles):
            for mat in ms:
                mat['color_prime_family']=role;mat['color_prime_family_source']='MANUAL'
    previous=settings.auto_localize_legacy;settings.auto_localize_legacy=False
    try:report=scan_material_bindings(scene,settings,True)
    finally:settings.auto_localize_legacy=previous
    from .family_links import shared_socket
    settings.studio.adoption_pending=bool(any(o.get('color_prime_authored_slots',False) for o in objects) and any(not shared_socket(m) for m in sources))
    if structural:
        adopt(scene,settings,objects)
    return report


def _replace_source(mat,source,group,preserve_color=True):
    tree=source.tree
    node=tree.nodes.new('ShaderNodeGroup');node.node_tree=group
    node.name='Prime Inherited Color';node['cp_family_link']=True
    node.location=(source.node.location.x-230,source.node.location.y-120)
    color=tuple(source.socket.default_value);base=tuple(group.nodes['Color'].outputs[0].default_value)
    output=node.outputs['Color']
    if preserve_color and any(abs(a-b)>1e-7 for a,b in zip(color[:3],base[:3])):
        gain=tuple(color[i]/base[i] if abs(base[i])>1e-6 else 1. for i in range(3))+(1.,)
        bias=tuple(0. if abs(base[i])>1e-6 else color[i]-base[i] for i in range(3))+(1.,)
        for mode,value in (('MULTIPLY',gain),('ADD',bias)):
            correct=tree.nodes.new('ShaderNodeMixRGB');correct.blend_type=mode;correct.inputs[0].default_value=1.
            correct.inputs[2].default_value=value;correct['cp_family_shade']=True
            tree.links.new(output,correct.inputs[1]);output=correct.outputs[0]
    if source.is_output:
        targets=[l.to_socket for l in source.socket.links]
        for target in targets:tree.links.new(output,target)
    else:tree.links.new(output,source.socket)


def adopt(scene,settings,objects=None):
    from .utils import all_icon_objects,data_block_identity
    from .family_links import color_source,ensure_parent,parent,family_node,shared_socket,connect
    from .discovery import scan_material_bindings
    from .material_names import name_material
    objects=list(objects if objects is not None else all_icon_objects(settings,scene,True))
    if settings.studio.stage_status!='STAGED':raise ValueError('Prepare the model first.')
    mats={slot.material for o in objects if o.type=='MESH' for slot in o.material_slots if slot.material}
    roles={b.material:b.family for b in settings.bindings if b.material in mats and b.enabled}
    records=[]
    for mat,role in roles.items():
        if role not in {'MAIN','ACCENT'} or protected(mat,settings):continue
        src=color_source(mat)
        if src is None:continue
        if mat.get(OWNER_KEY,'')!=settings.studio.stage_id and not shared_socket(mat):continue
        records.append((mat,role,src,tuple(src.socket.default_value)))
    # Capture all initial colors before changing a shared dependency.
    for family in ('MAIN','ACCENT'):
        ensure_parent(settings,family)
    done={};linked=0
    for mat,role,src,color in records:
        if mat.get('cp_family_master',''):continue
        if shared_socket(mat) and mat.get('cp_adopted',False):
            linked+=1;continue
        group=family_node(parent(settings,role)).node_tree
        key=(src.tree.as_pointer(),src.node.name,src.socket.identifier,src.is_output)
        if key in done and done[key]!=role:raise ValueError('One shared color source cannot belong to both Main and Accent. Choose one role for its family.')
        if key not in done and not shared_socket(mat):
            if src.group_path or src.is_output:
                # Retain every downstream authored correction and shader.
                _replace_source(mat,src,group)
            else:
                from .family_links import attach_preserving_color
                attach_preserving_color(mat,group,color)
            done[key]=role
        mat['cp_adopted']=True;mat['cp_link_profile']='SAME'
        mat['cp_original_material_name']=mat.get('cp_original_material_name',mat.name)
        mat['color_prime_family']=role;mat['color_prime_family_source']='MANUAL'
        mat['color_prime_child']=True;mat['color_prime_child_profile']='SAME'
        if not mat.get('color_prime_generated_name',''):name_material(mat,role)
        if not any(c.material==mat for c in settings.children):
            child=settings.children.add();child.material=mat;child.family=role;child.profile='SAME'
            child.follow_surface=False;child.auto_assign=False
        linked+=1
    for binding in settings.bindings:
        if binding.material:binding.material_identity=data_block_identity(binding.material)
    settings.studio.adoption_pending=False
    scan_material_bindings(scene,settings,False)
    settings.studio.adoption_note='Connected {} materials; existing assignments and shader corrections retained.'.format(linked)
    return {'linked':linked,'collapsed':0,'skipped':[]}


def process_zones(scene,settings,objects):
    from .surface_adapter import existing_regions,auto_regions,region_count,REGION_ATTRIBUTE
    from .family_links import upgrade,ready,ensure_parent,parent
    from .scene_intelligence import auto_prepare_materials
    st=settings.studio;preserved=created=0
    if st.zone_policy=='REPLACE' and not (st.replace_zones or st.replace_materials):
        raise ValueError('Choose Rebuild zones and/or Replace materials.')
    for obj in objects:
        if obj.type!='MESH' or obj.data.get(OWNER_KEY,'')!=st.stage_id:continue
        authored=obj.get('color_prime_authored_slots',False)
        if st.zone_policy=='PRESERVE' and authored:
            if not region_count(obj):existing_regions(obj)
            preserved+=region_count(obj)
            created+=fill_unassigned(obj,settings)
            continue
        if st.zone_policy=='REPLACE' and st.replace_zones:
            attr=obj.data.attributes.get(REGION_ATTRIBUTE)
            if attr:obj.data.attributes.remove(attr)
            if not st.replace_materials:
                # Partition labels only; retain slots, face assignments and shaders.
                from .mesh_regions import segment
                mesh=obj.data
                plan=segment([tuple(obj.matrix_world@v.co) for v in mesh.vertices],[tuple(p.vertices) for p in mesh.polygons],
                             [tuple(e.vertices) for e in mesh.edges if e.use_seam],[],st.surface_regions,max_regions=st.region_max)
                attr=mesh.attributes.new(name=REGION_ATTRIBUTE,type='INT',domain='FACE');attr.data.foreach_set('value',plan.labels)
                created+=plan.count;continue
        if st.zone_policy=='REPLACE' and st.replace_materials:
            ensure_parent(settings,'MAIN');ensure_parent(settings,'ACCENT')
            if not st.replace_zones and not region_count(obj):existing_regions(obj)
            attr=obj.data.attributes.get(REGION_ATTRIBUTE)
            labels=[v.value for v in attr.data] if attr else [0]*len(obj.data.polygons)
            areas={i:0. for i in set(labels)}
            for face,label in zip(obj.data.polygons,labels):areas[label]+=face.area
            main=max(areas,key=areas.get) if areas else 0
            obj.data.materials.clear()
            obj.data.materials.append(parent(settings,'MAIN'));obj.data.materials.append(parent(settings,'ACCENT'))
            for face,label in zip(obj.data.polygons,labels):face.material_index=0 if label==main else 1
            if st.replace_zones:
                # Geometry inference may freely replace the working assignment.
                obj.data.materials.clear()
                for face in obj.data.polygons:face.material_index=0
        before=region_count(obj)
        if not before:auto_regions(obj,settings,explicit=True)
        if not region_count(obj):existing_regions(obj)
        created+=region_count(obj) if not before else 0
    if st.zone_policy=='REPLACE' and st.replace_materials:st.adoption_pending=False
    if not st.adoption_pending:upgrade(scene,settings,objects)
    mats={slot.material for o in objects for slot in o.material_slots if slot.material}
    st.region_note='{} parts · {} zones · {} materials | preserved: {}; found: {}'.format(len(objects),sum(region_count(o) for o in objects),len(mats),preserved,created)
    return {'parts':len(objects),'preserved':preserved,'created':created,'materials':len(mats)}


def fill_unassigned(obj,settings):
    """Infer only faces without a material; authored faces and slots are immutable."""
    from .surface_adapter import REGION_ATTRIBUTE
    from .mesh_regions import segment
    from .scene_intelligence import _new_family_material
    from .family_links import ready,connect
    mesh=obj.data;slots=list(obj.material_slots)
    missing=[f for f in mesh.polygons if f.material_index>=len(slots) or slots[f.material_index].material is None]
    if not missing or settings.studio.surface_regions=='OFF':return 0
    vertex_ids=sorted({i for f in missing for i in f.vertices});mapping={v:i for i,v in enumerate(vertex_ids)}
    points=[tuple(obj.matrix_world@mesh.vertices[i].co) for i in vertex_ids]
    plan=segment(points,[[mapping[i] for i in f.vertices] for f in missing],[],[],settings.studio.surface_regions,max_regions=settings.studio.region_max)
    attr=mesh.attributes.get(REGION_ATTRIBUTE)
    start=max((x.value for x in attr.data),default=-1)+1
    main=max(range(plan.count),key=lambda i:plan.areas[i])
    material_slots={}
    for zone in range(plan.count):
        family='MAIN' if zone==main else 'ACCENT'
        if family not in material_slots:
            mat=_new_family_material(settings,family);mat[OWNER_KEY]=settings.studio.stage_id
            if ready(settings):connect(mat,settings,family)
            mesh.materials.append(mat);material_slots[family]=len(mesh.materials)-1
        for face,label in zip(missing,plan.labels):
            if label==zone:face.material_index=material_slots[family];attr.data[face.index].value=start+zone
    # Keep IDs dense for the numbered-zone picker; never alter material indices.
    remap={v:i for i,v in enumerate(sorted({d.value for d in attr.data}))}
    values=[remap[d.value] for d in attr.data];attr.data.foreach_set('value',values)
    mesh.update();return plan.count
