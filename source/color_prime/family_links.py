"""Persistent Blender shader links: one color source per family, no timer sync.

Every family material references the same node-group ID. Palette preview writes
that source once; Blender evaluates all users, including with this add-on disabled.
"""
import bpy
from .transaction import OWNER_KEY,protected


def principled(material):
    socket=color_input(material)
    return socket.node if socket is not None and socket.node.type=='BSDF_PRINCIPLED' else None

def family_node(material):
    source=color_source(material)
    if source is None or source.tree.get('cp_family_role','') not in {'MAIN','ACCENT'}:return None
    tree=material.node_tree;node=None
    for name in source.group_path:
        node=tree.nodes.get(name)
        if node is None or node.type!='GROUP':return None
        tree=node.node_tree
    return node


def color_input(material):
    from .shader_endpoints import endpoints
    found=endpoints(material)
    return found[0][0] if len(found)==1 else None


def color_source(material):
    from .color_graph import trace_color_socket
    from .shader_endpoints import endpoints
    inputs=endpoints(material)
    sources=[]
    for socket,tree,path,callers in inputs:
        found=trace_color_socket(socket,tree,path,callers)
        if len(found)!=1:return None
        sources+=found
    unique={(v.tree.as_pointer(),v.node.name,v.socket.identifier,v.is_output):v for v in sources}
    return next(iter(unique.values())) if len(unique)==1 else None


def shared_socket(material):
    source=color_source(material)
    return source.socket if source and source.tree.get('cp_family_role','') in {'MAIN','ACCENT'} else None


def parent(settings,family):
    return getattr(settings,'main_parent_material' if family=='MAIN' else 'accent_parent_material')


def ready(settings):
    return all(parent(settings,f) and parent(settings,f).get('cp_family_master','')==f and shared_socket(parent(settings,f)) for f in ('MAIN','ACCENT'))


def _attach(material,group,profile='SAME'):
    target=color_input(material);shader=target.node if target else None
    if shader is None:raise ValueError('Choose a shader color input for '+material.name)
    tree=material.node_tree
    for node in list(tree.nodes):
        if node.get('cp_family_link',False) or node.get('cp_family_shade',False):tree.nodes.remove(node)
    source=tree.nodes.new('ShaderNodeGroup');source.node_tree=group
    source.name='Prime Color';source.label=group.name;source['cp_family_link']=True
    source.location=(shader.location.x-240,shader.location.y+100)
    output=source.outputs['Color']
    if True:
        shade=tree.nodes.new('ShaderNodeMixRGB');shade.name='Family Shade';shade.label=profile.title()+' from '+group.name
        shade['cp_family_shade']=True;shade.blend_type='MIX'
        shade.inputs[0].default_value=.18 if profile=='LIGHT' else .22 if profile=='DARK' else 0.
        shade.inputs[2].default_value=(0,0,0,1) if profile=='DARK' else (1,1,1,1)
        shade.location=(source.location.x+20,source.location.y-210)
        tree.links.new(output,shade.inputs[1]);output=shade.outputs[0]
    tree.links.new(output,target)


def attach_preserving_color(material,group,color):
    _attach(material,group)
    tree=material.node_tree;node=family_node(material)
    base=tuple(group.nodes['Color'].outputs[0].default_value)
    # Per-channel affine calibration exactly retains the imported initial RGB,
    # including a zero channel in the parent's starting color.
    gain=tuple(color[i]/base[i] if abs(base[i])>1e-6 else 1. for i in range(3))+(1.,)
    bias=tuple(0. if abs(base[i])>1e-6 else color[i]-base[i] for i in range(3))+(1.,)
    output=node.outputs['Color'];destinations=[l.to_socket for l in output.links]
    for mode,value in (('MULTIPLY',gain),('ADD',bias)):
        correct=tree.nodes.new('ShaderNodeMixRGB');correct.blend_type=mode;correct.use_clamp=False
        correct.name='Imported '+mode.title();correct['cp_family_shade']=True
        correct.inputs[0].default_value=1.;correct.inputs[2].default_value=value
        tree.links.new(output,correct.inputs[1]);output=correct.outputs[0]
    for target in destinations:tree.links.new(output,target)


def ensure_parent(settings,family,template=None):
    current=parent(settings,family)
    if current and current.get('cp_family_master','')==family and shared_socket(current):
        # A new reversible setup isolates its source once, not once per child.
        node=family_node(current)
        if settings.studio.stage_status!='STAGED' or settings.studio.reuse_family_library or (current.get(OWNER_KEY,'')==settings.studio.stage_id and node.node_tree.get(OWNER_KEY,'')==settings.studio.stage_id):return current
    source=template or current
    if source and protected(source,settings):source=None
    if source and color_input(source) is not None and color_input(source).id_data==source.node_tree:
        material=source.copy()
        from .targets import find_material_target
        color=tuple(shared_socket(source).default_value) if shared_socket(source) else find_material_target(source,False).color
    else:
        material=bpy.data.materials.new('Prime '+family.title());material.use_nodes=True
        source_color=color_source(source) if source else None
        color=tuple(source_color.socket.default_value) if source_color else tuple(settings.main_reference_color if family=='MAIN' else settings.accent_reference_color)
    # Blender 3.6 may displace an existing unsuffixed ID when renaming a copy.
    # Choose an unused name first: transaction snapshots retain the old ID name.
    stem='Prime '+family.title();name=stem;index=1
    while bpy.data.materials.get(name) not in {None,material}:
        name='{}.{:03d}'.format(stem,index);index+=1
    material.name=name
    for key in ('color_prime_child','color_prime_child_profile','cp_material_region','color_prime_generated_name'):
        if key in material:del material[key]
    material['cp_family_master']=family;material['color_prime_parent']=family
    material['color_prime_family']=family;material['color_prime_family_source']='MANUAL'
    material[OWNER_KEY]=settings.studio.stage_id
    group=bpy.data.node_groups.new('Prime '+family.title(),'ShaderNodeTree')
    group['cp_family_role']=family;group[OWNER_KEY]=settings.studio.stage_id
    if hasattr(group,'interface'):group.interface.new_socket(name='Color',in_out='OUTPUT',socket_type='NodeSocketColor')
    else:group.outputs.new('NodeSocketColor','Color')
    rgb=group.nodes.new('ShaderNodeRGB');rgb.name='Color';rgb.label='Prime '+family.title();rgb.outputs[0].default_value=color
    output=group.nodes.new('NodeGroupOutput');output.location=(220,0);group.links.new(rgb.outputs[0],output.inputs['Color'])
    _attach(material,group)
    setattr(settings,'main_parent_material' if family=='MAIN' else 'accent_parent_material',material)
    return material


def connect(material,settings,family,profile=None):
    master=ensure_parent(settings,family)
    if material==master:return master
    profile=profile or material.get('color_prime_child_profile','SAME')
    desired=family_node(master).node_tree;existing=family_node(material)
    if 'cp_family_master' in material:del material['cp_family_master']
    if 'color_prime_parent' in material:del material['color_prime_parent']
    if existing and existing.node_tree==desired and material.get('cp_link_profile','SAME')==profile:return material
    if existing and material.get('cp_adopted',False):
        from .color_graph import localize_source
        src=color_source(material);localize_source(material,src)
        existing=family_node(material);existing.node_tree=desired
        material['cp_link_profile']=profile
        return material
    _attach(material,desired,profile);material['cp_link_profile']=profile
    return material


def disconnect(material):
    source=color_source(material)
    if source is None or shared_socket(material) is None:return
    # Localize only this path, then freeze its root RGB. Downstream calibration,
    # authored curves and tint remain intact, so disabling inheritance never jumps.
    from .color_graph import localize_source
    localize_source(material,source)
    node=family_node(material)
    tree=node.id_data
    rgb=tree.nodes.new('ShaderNodeRGB');rgb.name='Retained Color'
    rgb.outputs[0].default_value=shared_socket(material).default_value
    for target in [l.to_socket for l in node.outputs['Color'].links]:tree.links.new(rgb.outputs[0],target)
    tree.nodes.remove(node)
    for key in ('cp_family_master','color_prime_parent'):
        if key in material:del material[key]


def _surface_key(material):
    shader=principled(material)
    if shader is None:return None
    for node in material.node_tree.nodes:
        if node.get('cp_family_shade',False) and node.type=='MIX_RGB' and node.blend_type=='MIX' and not node.inputs[0].is_linked and node.inputs[0].default_value==0.:continue
        if node not in {shader} and node.type!='OUTPUT_MATERIAL' and not node.get('cp_family_link',False):return None
    values=[]
    for socket in shader.inputs:
        if socket.name=='Base Color':continue
        if socket.is_linked:return None
        if hasattr(socket,'default_value'):
            value=socket.default_value
            try:value=tuple(round(float(v),7) for v in value)
            except TypeError:value=round(float(value),7) if isinstance(value,(int,float)) else str(value)
            values.append((socket.name,value))
    for field in ('use_backface_culling','surface_render_method','blend_method','use_screen_refraction','use_transparent_shadow','displacement_method'):
        if hasattr(material,field):values.append((field,getattr(material,field)))
    return tuple(values)


def compact_slots(obj,replacements=None):
    """Decouple material slots from persistent face-region IDs."""
    replacements=replacements or {};old=[slot.material for slot in obj.material_slots]
    indices=[p.material_index for p in obj.data.polygons];used=set(indices)
    kept=[];remap={}
    for i,mat in enumerate(old):
        if i not in used:continue
        mat=replacements.get(mat,mat)
        if mat not in kept:kept.append(mat)
        remap[i]=kept.index(mat)
    if not kept:return
    obj.data.materials.clear()
    for mat in kept:obj.data.materials.append(mat)
    for slot,mat in zip(obj.material_slots,kept):slot.link='DATA';slot.material=mat
    for face,index in zip(obj.data.polygons,indices):face.material_index=remap.get(index,0)
    obj.active_material_index=min(obj.active_material_index,len(kept)-1);obj.data.update()


def upgrade(scene,settings,objects=None,compact=True):
    from .runtime import restore_preview
    from .utils import all_icon_objects
    from .discovery import scan_material_bindings
    restore_preview(scene)
    if settings.preview_active:raise ValueError('Restore the previous preview before linking families.')
    if settings.studio.stage_status!='STAGED':raise ValueError('Prepare the model before linking its materials.')
    objects=[o for o in (objects if objects is not None else all_icon_objects(settings,scene,True))
             if o.type=='MESH' and o.data.get(OWNER_KEY,'')==settings.studio.stage_id]
    for family in ('MAIN','ACCENT'):ensure_parent(settings,family)
    roles={b.material:b.family for b in settings.bindings if b.material and b.enabled}
    disabled={b.material for b in settings.bindings if b.material and not b.enabled}
    materials={slot.material for obj in objects for slot in obj.material_slots if slot.material}
    materials.update(c.material for c in settings.children if c.material)
    replacements={};linked=0;skipped=[]
    for mat in materials:
        if mat in disabled:continue
        family=mat.get('color_prime_family',roles.get(mat,''))
        if family not in {'MAIN','ACCENT'} or protected(mat,settings):continue
        if mat.get(OWNER_KEY,'')!=settings.studio.stage_id:continue
        if color_input(mat) is None:skipped.append(mat.name);continue
        if not shared_socket(mat):
            from .targets import find_material_target
            if not find_material_target(mat,False).writable:skipped.append(mat.name);continue
        connect(mat,settings,family);linked+=1
        master=parent(settings,family)
        # Only collapse generated, identical base surfaces. Custom shaders and
        # child materials retain their identity and surface overrides.
        if compact and not mat.get('color_prime_child',False) and ('cp_material_region' in mat or mat.get('color_prime_family_source')=='AUTO_GEOMETRY'):
            key=_surface_key(mat)
            if key is not None and key==_surface_key(master):replacements[mat]=master
    if compact:
        for obj in objects:
            if not obj.get('color_prime_authored_slots',False) or settings.studio.zone_policy=='REPLACE':compact_slots(obj,replacements)
    scan_material_bindings(scene,settings,False)
    # These are replaceable, generated working duplicates, never original IDs.
    for rec in settings.studio.backup_materials:
        if rec.staged in replacements:rec.staged=None
    for mat in replacements:
        if mat.users==0 and mat.get(OWNER_KEY,'')==settings.studio.stage_id:
            bpy.data.materials.remove(mat)
    return {'linked':linked,'collapsed':len(replacements),'skipped':skipped}


def set_colors(scene,settings,main,accent):
    """Persistent palette application. Ordinary .blend saving retains this edit."""
    from .runtime import restore_preview
    restore_preview(scene)
    if settings.preview_active:raise ValueError('Could not restore the previous preview.')
    if not ready(settings):upgrade(scene,settings)
    for family,color in (('MAIN',main),('ACCENT',accent)):
        shared_socket(parent(settings,family)).default_value=tuple(color)
    settings.main_reference_color=main;settings.accent_reference_color=accent
    bpy.context.view_layer.update()
