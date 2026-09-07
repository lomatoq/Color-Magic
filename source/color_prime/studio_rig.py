"""Opt-in, reversible camera + three softboxes. Never modifies model transforms.

All objects created here are owned by a persistent unique rig ID. Original
camera/world and render settings are retained in Scene RNA across save/reopen.
"""
import json
import uuid
import bpy
from .studio_math import fit_frame, softboxes

OWNER='cp_studio_rig_owner'
ROLE='cp_studio_rig_role'
_RENDER=('engine','resolution_x','resolution_y','resolution_percentage','film_transparent',
         'pixel_aspect_x','pixel_aspect_y','use_border','use_crop_to_border','use_sequencer')


def is_active(settings):
    return not missing_parts(settings)


def missing_parts(settings):
    """Read live scene membership, not a journal left behind by deleted objects."""
    st=settings.studio;scene=settings.id_data
    if not st.rig_id:return ['Studio']
    coll=st.rig_collection
    def contains(root):
        return root==coll or any(contains(child) for child in root.children)
    if coll is None or not contains(scene.collection):return ['Collection']
    lookup={o.get(ROLE,''):o for o in _owned_objects(st)
            if scene.objects.get(o.name)==o}
    missing=[]
    for role,kind in (('Camera','CAMERA'),('Key','LIGHT'),('Fill','LIGHT'),('Rim','LIGHT'),('Backdrop','MESH')):
        obj=lookup.get(role)
        if obj is None or obj.type!=kind or obj.data is None:missing.append(role)
    if st.rig_camera is None or lookup.get('Camera')!=st.rig_camera:
        if 'Camera' not in missing:missing.append('Camera')
    if st.rig_world is None:missing.append('World')
    if st.rig_background_owned is None:missing.append('Background material')
    return missing


def current_model(settings):
    from .utils import active_item,objects_from_icon_item
    item=active_item(settings.icon_sets,settings.icon_set_index)
    candidates=([item] if item is not None else [])+list(settings.icon_sets)
    return next((i for i in candidates if i.enabled and any(
        o.type=='MESH' for o in objects_from_icon_item(i,settings.id_data))),None)


def framed_for_model(settings):
    item=current_model(settings);st=settings.studio
    return bool(item is not None and (
        (item.root_kind=='COLLECTION' and item.collection_root==st.rig_model_collection)
        or (item.root_kind=='OBJECT' and item.object_root==st.rig_model_root)))


def _remember_model(settings):
    item=current_model(settings);st=settings.studio
    st.rig_model_collection=item.collection_root if item and item.root_kind=='COLLECTION' else None
    st.rig_model_root=item.object_root if item and item.root_kind=='OBJECT' else None


def scope_objects(scene,settings):
    from .utils import objects_from_icon_item
    icon=current_model(settings)
    if icon is not None:
        return objects_from_icon_item(icon,scene)
    from .guided_state import selection_roots,mesh_descendants
    return [o for r in selection_roots(getattr(bpy.context,'selected_objects',())) for o in mesh_descendants(r)
            if not o.get(OWNER) and scene.objects.get(o.name)==o]


def world_bounds(scene,objects):
    from mathutils import Vector
    try:depsgraph=bpy.context.evaluated_depsgraph_get()
    except (AttributeError,RuntimeError):depsgraph=None
    points=[]
    for obj in objects:
        if obj.type not in {'MESH','CURVE','SURFACE','FONT','META'}:continue
        evaluated=obj.evaluated_get(depsgraph) if depsgraph is not None else obj
        for corner in evaluated.bound_box:
            points.append(tuple(evaluated.matrix_world @ Vector(corner)))
    if not points:raise ValueError('Select a mesh or prepare an icon before creating the studio.')
    return points


def _owned_objects(st):
    return [o for o in st.rig_collection.all_objects if o.get(OWNER,'')==st.rig_id] if st.rig_collection else []


def frame(scene,settings,objects=None):
    """Reframe only the managed rig. Authored cameras/lights are never moved."""
    if not is_active(settings):return False
    st=settings.studio
    cam=st.rig_camera
    if cam is None or cam.type!='CAMERA' or cam.get(OWNER,'')!=st.rig_id:
        raise ValueError('The studio camera was removed. Remove Studio, then create it again.')
    if scene.camera!=cam:return False  # Respect a camera chosen manually afterwards.
    lookup={o.get(ROLE,''):o for o in _owned_objects(st)}
    if any(lookup.get(role) is None or lookup[role].type!='LIGHT' for role in ('Key','Fill','Rim')):
        raise ValueError('A studio softbox was removed. Remove Studio and create it again.')
    objects=scope_objects(scene,settings) if objects is None else objects
    pts=world_bounds(scene,objects)
    from .studio_presets import VIEWS,lights
    yaw,elevation=(st.rig_yaw,st.rig_elevation) if st.rig_preset=='CUSTOM' else VIEWS[st.rig_preset][1:]
    render=scene.render
    aspect=(render.resolution_x*render.pixel_aspect_x)/max(1e-12,render.resolution_y*render.pixel_aspect_y)
    fit=fit_frame(pts,yaw,elevation,aspect,st.rig_margin)
    from mathutils import Vector,Matrix
    cam.location=fit.offset(0.,0.,fit.distance)
    cam.rotation_euler=Matrix((fit.right,fit.up,fit.back)).transposed().to_euler()
    data=cam.data;data.type='ORTHO';data.sensor_fit='VERTICAL';data.shift_x=0.;data.shift_y=0.
    data.ortho_scale=1.
    # Let Blender define the sensor convention; fit both dimensions to its frame.
    corners=data.view_frame(scene=scene)
    w=max(p.x for p in corners)-min(p.x for p in corners)
    h=max(p.y for p in corners)-min(p.y for p in corners)
    occupancy=1.-2.*st.rig_margin
    data.ortho_scale=max(fit.width/max(w,1e-9),fit.height/max(h,1e-9),fit.span*.01)/occupancy
    data.clip_start=max(.0001,fit.span*.00001)
    data.clip_end=max(data.clip_start*100.,fit.distance+fit.span*20.)
    if hasattr(data,'dof'):data.dof.use_dof=False
    for role,location,size,power,color in lights(fit,st):
        light=lookup.get(role)
        if light is None or light.type!='LIGHT':raise ValueError('A studio softbox was removed. Remove Studio and create it again.')
        light.location=location
        light.rotation_euler=(Vector(fit.target)-light.location).to_track_quat('-Z','Y').to_euler()
        light.data.energy=power;light.data.size=size;light.data.color=color;light.data.shape='DISK'
    _background(settings,lookup.get('Backdrop'),fit,cam,aspect)
    try:bpy.context.view_layer.update()
    except (AttributeError,RuntimeError):pass
    st.rig_note='Studio ready: orthographic camera, Key / Fill / Rim. Original scene setup retained.'
    return True


def _new_object(st,name,data,role):
    obj=bpy.data.objects.new(name,data)
    obj[OWNER]=st.rig_id;obj[ROLE]=role
    st.rig_collection.objects.link(obj)
    return obj


def _background(settings,obj,fit,cam,aspect):
    st=settings.studio
    if obj is None:return
    obj.hide_render=not st.rig_backdrop;obj.hide_viewport=not st.rig_backdrop
    obj.location=fit.offset(0,0,-fit.depth*.5-fit.span*.1)
    obj.rotation_euler=cam.rotation_euler
    corners=cam.data.view_frame(scene=settings.id_data)
    obj.scale=((max(p.x for p in corners)-min(p.x for p in corners))*.55,
               (max(p.y for p in corners)-min(p.y for p in corners))*.55,1.)
    material=st.rig_background_material if st.rig_background_mode=='MATERIAL' else st.rig_background_owned
    if material is None:material=st.rig_background_owned
    if not obj.data.materials:obj.data.materials.append(material)
    else:obj.data.materials[0]=material
    if material!=st.rig_background_owned:return
    tree=material.node_tree;target=tree.nodes.get('Background Color').inputs['Color']
    for link in list(target.links):tree.links.remove(link)
    if st.rig_background_mode in {'MAIN','ACCENT'}:
        from .family_links import ready,parent,family_node
        if ready(settings):
            node=tree.nodes.get('Background Family') or tree.nodes.new('ShaderNodeGroup');node.name='Background Family'
            node.node_tree=family_node(parent(settings,st.rig_background_mode)).node_tree
            tree.links.new(node.outputs['Color'],target)
        else:target.default_value=settings.main_reference_color if st.rig_background_mode=='MAIN' else settings.accent_reference_color
    else:target.default_value=st.rig_background_color


def create(scene,settings,objects=None):
    st=settings.studio
    objects=scope_objects(scene,settings) if objects is None else list(objects)
    # Bounds validation precedes all writes/allocation.
    fit_frame(world_bounds(scene,objects))
    if is_active(settings):
        if st.rig_camera is None:raise ValueError('Remove the incomplete Studio and create it again.')
        previous_camera,previous_world=scene.camera,scene.world
        previous_pose=snapshot_pose(settings)
        try:
            scene.camera=st.rig_camera
            if st.rig_world is not None:scene.world=st.rig_world
            frame(scene,settings,objects)
            _remember_model(settings)
            return False
        except Exception:
            restore_pose(settings,previous_pose)
            scene.camera=previous_camera;scene.world=previous_world
            raise
    # Deleting a collection/softbox does not clear the persistent RNA journal.
    # Restore its retained scene state before replacing only our remaining rig.
    if st.rig_id:remove(scene,settings)
    st.rig_previous_camera=scene.camera;st.rig_previous_world=scene.world
    payload={'render':{k:getattr(scene.render,k) for k in _RENDER if hasattr(scene.render,k)},'cycles':{}}
    if hasattr(scene,'cycles'):
        payload['cycles']={k:getattr(scene.cycles,k) for k in ('samples','use_denoising') if hasattr(scene.cycles,k)}
    st.rig_previous_json=json.dumps(payload)
    st.rig_id=uuid.uuid4().hex
    try:
        coll=bpy.data.collections.new('Color Prime — Studio');coll[OWNER]=st.rig_id
        st.rig_collection=coll;scene.collection.children.link(coll)
        cdata=bpy.data.cameras.new('CP Studio Camera');cdata[OWNER]=st.rig_id
        st.rig_camera=_new_object(st,'CP Studio Camera',cdata,'Camera')
        for role in ('Key','Fill','Rim'):
            data=bpy.data.lights.new('CP '+role,type='AREA');data[OWNER]=st.rig_id
            _new_object(st,'CP '+role,data,role)
        mesh=bpy.data.meshes.new('CP Backdrop');mesh[OWNER]=st.rig_id
        mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],[],[(0,1,2,3)]);mesh.update()
        _new_object(st,'CP Backdrop',mesh,'Backdrop')
        mat=bpy.data.materials.new('CP Background');mat.use_nodes=True;mat[OWNER]=st.rig_id;st.rig_background_owned=mat
        mat.node_tree.nodes.clear();shader=mat.node_tree.nodes.new('ShaderNodeEmission');shader.name='Background Color'
        output=mat.node_tree.nodes.new('ShaderNodeOutputMaterial');mat.node_tree.links.new(shader.outputs[0],output.inputs['Surface'])
        world=bpy.data.worlds.new('CP Neutral World');world[OWNER]=st.rig_id;st.rig_world=world
        world.use_nodes=True
        background=next((n for n in world.node_tree.nodes if n.type=='BACKGROUND'),None)
        if background is None:raise RuntimeError('Blender did not create a world Background node.')
        background.inputs['Color'].default_value=(.8,.8,.8,1.)
        background.inputs['Strength'].default_value=.16
        st.rig_muted_lights.clear();skipped=[]
        if st.rig_isolate_lights:
            for obj in scene.objects:
                if obj.type!='LIGHT' or obj.get(OWNER,'')==st.rig_id or obj.hide_render:continue
                if obj.library or obj.override_library or len(getattr(obj,'users_scene',()))>1:
                    skipped.append(obj.name);continue
                entry=st.rig_muted_lights.add();entry.object=obj;entry.hide_render=obj.hide_render
                obj.hide_render=True
        scene.camera=st.rig_camera;scene.world=world
        scene.render.resolution_x=1024;scene.render.resolution_y=1024;scene.render.resolution_percentage=100
        scene.render.film_transparent=True
        for key,value in (('pixel_aspect_x',1.),('pixel_aspect_y',1.),('use_border',False),('use_crop_to_border',False),('use_sequencer',False)):
            if hasattr(scene.render,key):setattr(scene.render,key,value)
        if scene.render.engine not in {'CYCLES','BLENDER_EEVEE','BLENDER_EEVEE_NEXT'}:scene.render.engine='CYCLES'
        if scene.render.engine=='CYCLES' and hasattr(scene,'cycles'):
            scene.cycles.samples=64
            if hasattr(scene.cycles,'use_denoising'):scene.cycles.use_denoising=True
        frame(scene,settings,objects)
        _remember_model(settings)
        if skipped:st.rig_note+=' Shared/linked existing lights were not muted: '+', '.join(skipped[:5])
        return True
    except Exception:
        remove(scene,settings)
        raise


def remove(scene,settings):
    """Restore retained pointers; do not delete objects linked elsewhere by the user."""
    st=settings.studio
    owner=st.rig_id
    if not owner:return False
    if scene.camera==st.rig_camera or scene.camera is None:scene.camera=st.rig_previous_camera
    if scene.world==st.rig_world or scene.world is None:scene.world=st.rig_previous_world
    for entry in st.rig_muted_lights:
        obj=entry.object
        if obj is not None and not obj.library:obj.hide_render=entry.hide_render
    try:payload=json.loads(st.rig_previous_json or '{}')
    except (ValueError,TypeError):payload={}
    if not isinstance(payload,dict):payload={}
    for key,value in payload.get('render',{}).items():
        if key=='engine':
            engines={i.identifier for i in scene.render.bl_rna.properties['engine'].enum_items}
            if value not in engines:
                equivalent={'BLENDER_EEVEE':'BLENDER_EEVEE_NEXT','BLENDER_EEVEE_NEXT':'BLENDER_EEVEE'}.get(value)
                if equivalent not in engines:continue
                value=equivalent
        if key in _RENDER and hasattr(scene.render,key):setattr(scene.render,key,value)
    for key,value in payload.get('cycles',{}).items():
        if key in {'samples','use_denoising'} and hasattr(getattr(scene,'cycles',None),key):setattr(scene.cycles,key,value)
    coll=st.rig_collection
    # Include orphan allocations from a failed collection link. Never remove
    # an owned object which the author has since used in another scene.
    shared_scene=False
    for obj in list(bpy.data.objects):
        if obj.get(OWNER,'')!=owner:continue
        if len(getattr(obj,'users_scene',()))>1:
            shared_scene=True;continue
        in_collection=coll is not None and coll.objects.get(obj.name) == obj
        if in_collection:
            if len(obj.users_collection)>1:coll.objects.unlink(obj)
            else:bpy.data.objects.remove(obj,do_unlink=True)
        elif obj.users==0:bpy.data.objects.remove(obj,do_unlink=True)
    if shared_scene and coll and scene.collection.children.get(coll.name) == coll:
        scene.collection.children.unlink(coll)
    st.rig_camera=None;st.rig_world=None
    st.rig_collection=None;st.rig_previous_camera=None;st.rig_previous_world=None
    st.rig_muted_lights.clear()
    if coll and not coll.objects and not coll.children:bpy.data.collections.remove(coll)
    st.rig_background_owned=None
    for name in ('cameras','lights','worlds','meshes','materials'):
        for block in list(getattr(bpy.data,name)):
            if block.get(OWNER,'')==owner and block.users==0:getattr(bpy.data,name).remove(block)
    st.rig_id='';st.rig_setup_owner='';st.rig_previous_json='';st.rig_stage_pose_json=''
    st.rig_model_collection=None;st.rig_model_root=None
    st.rig_note='Original camera, world, light visibility and render settings restored.'
    return True


def snapshot_pose(settings):
    if not is_active(settings):return None
    st=settings.studio;records=[]
    for obj in _owned_objects(st):
        vals={}
        if obj.type=='CAMERA':vals={k:getattr(obj.data,k) for k in ('ortho_scale','clip_start','clip_end')}
        if obj.type=='LIGHT':
            vals={k:getattr(obj.data,k) for k in ('energy','size')};vals['color']=list(obj.data.color)
        records.append({'name':obj.name,'role':obj.get(ROLE,''),'matrix':[list(row) for row in obj.matrix_world],'data':vals,'hide_render':obj.hide_render,'hide_viewport':obj.hide_viewport})
    return {'owner':st.rig_id,'objects':records}


def restore_pose(settings,payload):
    if not payload:return []
    if not is_active(settings) or settings.studio.rig_id!=payload.get('owner'):return ['Studio rig changed during render; pose restoration was not applied.']
    from mathutils import Matrix
    lookup={o.get(ROLE,''):o for o in _owned_objects(settings.studio)};errors=[]
    for row in payload.get('objects',[]):
        obj=lookup.get(row['role'])
        if obj is None:errors.append('Missing studio object: '+row['name']);continue
        try:
            obj.matrix_world=Matrix(row['matrix'])
            obj.hide_render=row.get('hide_render',False);obj.hide_viewport=row.get('hide_viewport',False)
            for key,val in row['data'].items():
                if key in {'ortho_scale','clip_start','clip_end','energy','size','color'} and hasattr(obj.data,key):setattr(obj.data,key,val)
        except (ReferenceError,TypeError,ValueError,RuntimeError) as exc:errors.append(str(exc))
    return errors


def show_view(context,settings):
    """Best-effort viewport navigation; never rolls back a successful data action."""
    for area in getattr(getattr(context,'screen',None),'areas',()):
        if area.type!='VIEW_3D':continue
        try:
            space=area.spaces.active
            space.shading.type='MATERIAL'
            if is_active(settings):
                if space.region_3d is not None:space.region_3d.view_perspective='CAMERA'
                for attr in ('use_scene_lights','use_scene_world'):
                    if hasattr(space.shading,attr):setattr(space.shading,attr,True)
        except (AttributeError,ReferenceError,RuntimeError,TypeError):
            pass
