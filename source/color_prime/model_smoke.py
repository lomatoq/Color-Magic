"""Native Blender feature checks, run only on an isolated scratch scene.

No stubs. Called by the built-in self-test and the CLI release gate. This file
is shipped as a test, NOT as evidence that it was run on the build host.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import uuid
import bpy


@contextmanager
def _context(scene,obj):
    layer=scene.view_layers[0]
    layer.objects.active=obj
    for item in layer.objects:item.select_set(item==obj,view_layer=layer)
    if hasattr(bpy.context,'temp_override'):
        with bpy.context.temp_override(scene=scene,view_layer=layer,active_object=obj,object=obj,
                                       selected_objects=[obj],selected_editable_objects=[obj]):yield
    else:
        # Blender 3.0 uses the window scene, set by run().
        yield


def run(render=False,output_folder=None):
    from .selftest import _cube_mesh,_material
    from . import transaction,studio_rig
    from .surface_adapter import region_count
    from .scene_guard import SceneGuard
    token=uuid.uuid4().hex
    marker='cp_native_model_test'
    checks=[];scene=None
    window=getattr(bpy.context,'window',None)
    previous=window.scene if window else None
    owner=''
    def own(block):block[marker]=token;return block
    def check(condition,name):
        if not condition:raise AssertionError(name)
        checks.append(name)
    try:
        if bpy.context.mode!='OBJECT':raise RuntimeError('Switch to Object Mode before the native feature test.')
        scene=own(bpy.data.scenes.new('CP Native Studio Test'))
        if window:window.scene=scene
        root=own(bpy.data.objects.new('CP_TEST_ICON',None));scene.collection.objects.link(root)
        original=own(_cube_mesh('CP_TEST_CONNECTED'))
        for edge in original.edges:
            if all(i>=4 for i in edge.vertices):edge.use_seam=True
        material=own(_material('CP_TEST_PAINT',(.12,.35,.65,1.)))
        original.materials.append(material)
        obj=own(bpy.data.objects.new('CP_TEST_ONE_MESH',original));scene.collection.objects.link(obj);obj.parent=root
        oldworld=own(bpy.data.worlds.new('CP_TEST_ORIGINAL_WORLD'));scene.world=oldworld
        vertices=[tuple(v.co) for v in original.vertices];faces=[tuple(f.vertices) for f in original.polygons]
        original_size=(scene.render.resolution_x,scene.render.resolution_y)
        s=scene.color_prime;s.studio.create_studio_on_prepare=True;s.studio.surface_regions='AUTO'
        scene.view_layers[0].update()
        # Exact reported path: actual StringProperty, not an invented numeric descriptor.
        string=s.bl_rna.properties['output_folder']
        check(string.type=='STRING','native RNA: output_folder is a StringProperty')
        baseline=transaction.snapshot_settings(s)
        transaction.restore_settings(s,baseline)
        check(transaction.snapshot_settings(s)==baseline,'native RNA: all declared settings round-trip')
        with _context(scene,root):
            result=bpy.ops.color_prime.guide_prepare()
        owner=s.studio.stage_id
        check(result=={'FINISHED'},'native guided Prepare My Model operator completes')
        check(s.studio.guide_step=='COLORS','native guide advances to Colors')
        check(obj.data!=original and len(obj.data.polygons)==len(faces),'native single-mesh setup retains original topology')
        # RC7 deliberately preserves a prepainted one-material object. Explicit
        # replacement is the geometry-inference acceptance path.
        s.studio.zone_policy='REPLACE';s.studio.replace_zones=True;s.studio.replace_materials=True
        with _context(scene,obj):bpy.ops.color_prime.workflow_regions()
        check(region_count(obj)==2,'native connected cube is divided into two material zones along seam')
        check({b.family for b in s.bindings if b.enabled}>={'MAIN','ACCENT'},'native region materials obtain Main and Accent')
        check(studio_rig.is_active(s) and scene.camera==s.studio.rig_camera,'native opt-in studio assigns its camera')
        check(sum(o.type=='LIGHT' for o in s.studio.rig_collection.objects)==3,'native studio contains exactly three softboxes')
        rigid=s.studio.rig_id;objects_count=len(s.studio.rig_collection.objects)
        with _context(scene,obj):studio_rig.create(scene,s,[obj])
        check(s.studio.rig_id==rigid and len(s.studio.rig_collection.objects)==objects_count,'native repeated studio update creates no duplicate rig')
        from bpy_extras.object_utils import world_to_camera_view
        from mathutils import Vector
        for w,h in ((320,640),(640,320)):
            scene.render.resolution_x=w;scene.render.resolution_y=h
            with _context(scene,obj):studio_rig.frame(scene,s,[obj])
            scene.view_layers[0].update()
            projected=[world_to_camera_view(scene,scene.camera,obj.matrix_world @ Vector(p)) for p in obj.bound_box]
            check(all(.10<=p.x<=.90 and .10<=p.y<=.90 and p.z>0 for p in projected),'native camera fits bounds at {}x{}'.format(w,h))
        before=[list(r) for r in scene.camera.matrix_world]
        guard=SceneGuard(scene,s)
        scene.camera.location.x+=50
        check(not guard.close() and all(abs(a-b)<1e-5 for ra,rb in zip(before,scene.camera.matrix_world) for a,b in zip(ra,rb)),
              'native render guard restores studio camera pose')
        with _context(scene,obj):
            s.studio.region_index=2
            result=bpy.ops.color_prime.region_role(family='ACCENT',source='REGION')
        check(result=={'FINISHED'},'native region correction is a functioning Blender operator')
        if render:
            folder=Path(output_folder or tempfile.mkdtemp(prefix='cp_native_studio_'));folder.mkdir(parents=True,exist_ok=True)
            scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=4
            scene.render.resolution_x=96;scene.render.resolution_y=96;scene.render.resolution_percentage=100
            scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
            scene.render.filepath=str(folder/'studio_rig.png')
            with _context(scene,obj):
                studio_rig.frame(scene,s,[obj]);bpy.ops.render.render(write_still=True,scene=scene.name)
            from .png_validation import validate_png
            validate_png(folder/'studio_rig.png',96,96)
            checks.append('native three-softbox studio produces a verified 96x96 PNG')
        transaction.rollback(scene,s)
        check(obj.data==original,'native Revert restores the original mesh pointer')
        check(scene.camera is None and scene.world==oldworld and not s.studio.rig_id,'native Revert also restores the pre-studio camera/world')
        check((scene.render.resolution_x,scene.render.resolution_y)==original_size,'native Revert restores pre-studio resolution')
        check([tuple(v.co) for v in original.vertices]==vertices and [tuple(f.vertices) for f in original.polygons]==faces,
              'native original vertices and face topology are unchanged')
        return checks
    finally:
        if scene is not None:
            try:
                if scene.color_prime.studio.stage_status!='NONE':transaction.rollback(scene,scene.color_prime)
                if scene.color_prime.studio.rig_id:studio_rig.remove(scene,scene.color_prime)
            finally:
                if window and previous:window.scene=previous
                bpy.data.scenes.remove(scene)
        for name in ('objects','meshes','materials','worlds','collections'):
            for block in list(getattr(bpy.data,name)):
                if block.get(marker,'')==token and (name=='objects' or block.users==0):
                    getattr(bpy.data,name).remove(block,do_unlink=True)
        if owner:transaction._cleanup_owned(owner)
