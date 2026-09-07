"""Exception-safe scene state and on-save recovery journal for render jobs."""
import json
from dataclasses import asdict
import bpy
from .runtime import snapshot_bindings, restore_snapshots
from .targets import TargetSnapshot, restore_snapshot
from .utils import iter_collection_tree
from .compat import compositor_tree

JOURNAL='cp_studio_render_recovery_v1'
_RENDER=('filepath','resolution_x','resolution_y','resolution_percentage','film_transparent',
         'use_lock_interface','use_file_extension','use_border','use_crop_to_border','use_compositing','use_sequencer','use_multiview')
_IMAGE=('file_format','color_mode','color_depth','compression')
_CYCLES=('samples','use_denoising')
_EEVEE=('taa_render_samples',)
_GEOMETRY={'MESH','CURVE','SURFACE','META','FONT','VOLUME','GPENCIL','GREASEPENCIL','CURVES','POINTCLOUD'}


def _capture(block,names):
    return {key:getattr(block,key) for key in names if block is not None and hasattr(block,key)}


def _restore(block,values,allowed,errors):
    if block is None:
        return
    for key,value in values.items():
        if key not in allowed:
            continue
        try:
            setattr(block,key,value)
        except (AttributeError,ReferenceError,TypeError,ValueError,RuntimeError) as exc:
            errors.append('{}: {}'.format(key,exc))


def _layers(root,path=()):
    yield root,path
    for i,child in enumerate(root.children):
        yield from _layers(child,path+(i,))


class SceneGuard:
    def __init__(self,scene,settings):
        if JOURNAL in scene:
            errors=recover(scene)
            if errors:
                raise RuntimeError('A previous render recovery could not complete: '+'; '.join(errors[:4]))
        self.scene=scene
        self.render=_capture(scene.render,_RENDER)
        self.image=_capture(scene.render.image_settings,_IMAGE)
        self.cycles=_capture(getattr(scene,'cycles',None),_CYCLES)
        self.eevee=_capture(getattr(scene,'eevee',None),_EEVEE)
        self.objects=[(obj,bool(obj.hide_render)) for obj in scene.objects]
        self.collections=[(coll,bool(coll.hide_render)) for coll in iter_collection_tree(scene.collection)]
        self.layers=[(vl,lc,path,bool(lc.exclude)) for vl in scene.view_layers for lc,path in _layers(vl.layer_collection)]
        self.muted=[]
        tree=compositor_tree(scene)
        if tree:
            self.muted=[(n,bool(n.mute)) for n in tree.nodes if n.bl_idname=='CompositorNodeOutputFile']
        self.materials=snapshot_bindings(settings)
        from .studio_rig import snapshot_pose
        self.rig_pose=snapshot_pose(settings)
        self.closed=False
        self.persist()

    def payload(self):
        snapshots=[]
        for snap in self.materials:
            row={k:getattr(snap,k) for k in ('material_name','material_library','kind','node_name',
                 'socket_name','socket_index','group_node_name','inner_node_name','color','path_json')}
            try:
                if snap.material_ref:
                    row['material_name']=snap.material_ref.name
            except ReferenceError:
                pass
            row['color']=list(row['color']);snapshots.append(row)
        def names(rows):
            out=[]
            for block,value in rows:
                try: out.append([block.name,value])
                except ReferenceError: pass
            return out
        return {'schema':1,'rig_pose':self.rig_pose,'render':self.render,'image':self.image,'cycles':self.cycles,'eevee':self.eevee,
                'objects':names(self.objects),'collections':names(self.collections),'muted':names(self.muted),
                'layers':[[vl.name,list(path),value] for vl,lc,path,value in self.layers],
                'materials':snapshots}

    def persist(self):
        self.scene[JOURNAL]=json.dumps(self.payload(),ensure_ascii=False,allow_nan=False)

    def reset_materials(self):
        restored=restore_snapshots(self.materials)
        if restored!=len(self.materials):
            raise RuntimeError('A color target was removed or changed during render. Job stopped.')

    def visibility(self,icon_objects,all_icon_objects,force_show=True):
        current={o.as_pointer() for o in icon_objects}
        managed={o.as_pointer() for o in all_icon_objects}
        for obj,original in self.objects:
            if obj.type in _GEOMETRY and obj.as_pointer() in managed:
                obj.hide_render=(False if force_show else original) if obj.as_pointer() in current else True
        # Never hide a whole icon collection: it may also contain a camera/light.
        if force_show:
            needed=set()
            for obj in icon_objects:
                for coll in obj.users_collection:
                    needed.add(coll.as_pointer())
            def visit(coll):
                active=coll.as_pointer() in needed
                for child in coll.children:
                    active=visit(child) or active
                if active: coll.hide_render=False
                return active
            visit(self.scene.collection)
            for vl in self.scene.view_layers:
                def expose(lc):
                    active=lc.collection.as_pointer() in needed
                    for child in lc.children:
                        active=expose(child) or active
                    if active: lc.exclude=False
                    return active
                expose(vl.layer_collection)

    def configure_preview(self,size,samples):
        render=self.scene.render
        w,h=self.render['resolution_x'],self.render['resolution_y']
        factor=float(size)/max(w,h)
        render.resolution_x=max(4,round(w*factor));render.resolution_y=max(4,round(h*factor))
        render.resolution_percentage=100;render.use_file_extension=True
        render.film_transparent=True;render.use_border=False;render.use_crop_to_border=False
        render.use_sequencer=False
        if hasattr(render,'use_multiview'): render.use_multiview=False
        # Keep authored compositing and color management, but mute file outputs.
        for node,_ in self.muted: node.mute=True
        image=render.image_settings
        image.file_format='PNG';image.color_mode='RGBA';image.color_depth='8';image.compression=20
        cycles=getattr(self.scene,'cycles',None)
        if cycles is not None: cycles.samples=int(samples)
        eevee=getattr(self.scene,'eevee',None)
        if eevee is not None and hasattr(eevee,'taa_render_samples'): eevee.taa_render_samples=int(samples)

    def close(self):
        if self.closed:
            return []
        self.closed=True;errors=[]
        try:
            count=restore_snapshots(self.materials)
            if count!=len(self.materials):errors.append('Some material targets could not be restored')
            for block,value in self.objects+self.collections:
                try:block.hide_render=value
                except (ReferenceError,RuntimeError) as exc:errors.append(str(exc))
            for vl,lc,path,value in self.layers:
                try:lc.exclude=value
                except (ReferenceError,RuntimeError) as exc:errors.append(str(exc))
            for node,value in self.muted:
                try:node.mute=value
                except (ReferenceError,RuntimeError) as exc:errors.append(str(exc))
            from .studio_rig import restore_pose
            errors.extend(restore_pose(self.scene.color_prime,self.rig_pose))
            _restore(self.scene.render,self.render,_RENDER,errors)
            _restore(self.scene.render.image_settings,self.image,_IMAGE,errors)
            _restore(getattr(self.scene,'cycles',None),self.cycles,_CYCLES,errors)
            _restore(getattr(self.scene,'eevee',None),self.eevee,_EEVEE,errors)
            if not errors and JOURNAL in self.scene:del self.scene[JOURNAL]
        except (ReferenceError,RuntimeError) as exc:
            errors.append(str(exc))
        return errors


def recover(scene):
    """Called after file load or before a new job; accepts only a fixed schema."""
    raw=scene.get(JOURNAL,'')
    if not raw:return []
    errors=[]
    try:
        data=json.loads(raw)
        if data.get('schema')!=1:raise ValueError('Unknown recovery schema')
        _restore(scene.render,data.get('render',{}),_RENDER,errors)
        _restore(scene.render.image_settings,data.get('image',{}),_IMAGE,errors)
        _restore(getattr(scene,'cycles',None),data.get('cycles',{}),_CYCLES,errors)
        _restore(getattr(scene,'eevee',None),data.get('eevee',{}),_EEVEE,errors)
        from .studio_rig import restore_pose
        errors.extend(restore_pose(scene.color_prime,data.get('rig_pose')))
        collections={c.name:c for c in iter_collection_tree(scene.collection)}
        for kind in ('objects','collections'):
            available=scene.objects if kind=='objects' else collections
            for name,value in data.get(kind,[]):
                block=available.get(name)
                if block is not None:
                    try:block.hide_render=bool(value)
                    except (RuntimeError,ReferenceError) as exc:errors.append(str(exc))
        for name,path,value in data.get('layers',[]):
            vl=scene.view_layers.get(name)
            if vl:
                try:
                    lc=vl.layer_collection
                    for index in path:lc=lc.children[index]
                    lc.exclude=bool(value)
                except (IndexError,ReferenceError,RuntimeError) as exc:errors.append(str(exc))
        tree=compositor_tree(scene)
        if tree:
            for name,value in data.get('muted',[]):
                node=tree.nodes.get(name)
                if node:node.mute=bool(value)
        for row in data.get('materials',[]):
            snap=TargetSnapshot(**row)
            if not restore_snapshot(snap):errors.append('Missing recovery target: '+row['material_name'])
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        errors.append(str(exc))
    if not errors:del scene[JOURNAL]
    return errors
