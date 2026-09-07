"""Real bpy self-test. No mocks, no network, no add-on installation side effects.

The UI offers data-only or scratch-render tests. The CLI additionally
tests save/reload and subsequent rollback. All test IDs
are explicitly tracked and cleaned; a user's scene is never used as test input.
"""
import json
import tempfile
import time
import traceback
import uuid
from pathlib import Path
import bpy


def _cube_mesh(name, size=1.):
    vertices = [tuple(size * v for v in xyz) for xyz in
        ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
    faces = ((0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    return mesh


def _material(name, color):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs['Base Color'].default_value = color
    node.inputs['Roughness'].default_value = .4
    return material


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def run(render=False, save_reload=False, output_folder=None):
    from . import transaction
    from .discovery import add_icon_root, scan_material_bindings
    from .scene_intelligence import auto_prepare_materials
    from .operators import seed_palettes_from_detected_scene, ensure_resolution_default
    from .runtime import apply_preview, restore_preview, clear_runtime_state
    from .targets import read_binding_color
    from .scene_guard import SceneGuard, recover, JOURNAL
    from . import lookbook
    prefix = 'CP_TEST_' + uuid.uuid4().hex[:10] + '_'
    window = getattr(bpy.context, 'window', None)
    original_scene = getattr(window, 'scene', None) if window else None
    original_filepath = bpy.data.filepath
    scene = None; stage_owner = ''; checks = []; errors = []; cleanup_errors = []
    owned = {name: [] for name in ('objects','meshes','materials','node_groups','cameras','lights','worlds','images')}
    folder = Path(output_folder or tempfile.mkdtemp(prefix='cp_real_bpy_test_'))
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    def check(name, predicate):
        _assert(predicate, name); checks.append(name)
    try:
        if getattr(bpy.context, 'mode', 'OBJECT') != 'OBJECT':
            raise RuntimeError('Switch to Object Mode before running the scratch-scene self-test')
        from .model_smoke import run as run_model_smoke
        checks.extend(run_model_smoke(render=render,output_folder=str(folder/'studio_features')))
        scene = bpy.data.scenes.new(prefix + 'Scene')
        if window: window.scene = scene
        settings = scene.color_prime
        root = bpy.data.objects.new(prefix+'Icon', None); scene.collection.objects.link(root); owned['objects'].append(root)
        mesh = _cube_mesh(prefix+'SharedMesh'); owned['meshes'].append(mesh)
        material = _material(prefix+'Paint', (.08,.4,.22,1)); owned['materials'].append(material)
        mesh.materials.append(material)
        a = bpy.data.objects.new(prefix+'Body', mesh); b = bpy.data.objects.new(prefix+'Detail', mesh)
        for obj in (a,b):
            scene.collection.objects.link(obj); obj.parent=root; owned['objects'].append(obj)
        a.location=(-.6,0,0); b.location=(1.1,-.15,.4); b.scale=(.38,.38,.38)
        fixed = _material(prefix+'Gold!', (.75,.43,.09,1)); owned['materials'].append(fixed)
        fixed_mesh = _cube_mesh(prefix+'FixedMesh',.2); fixed_mesh.materials.append(fixed); owned['meshes'].append(fixed_mesh)
        g = bpy.data.objects.new(prefix+'Fixed',fixed_mesh); scene.collection.objects.link(g); g.parent=root
        g.location=(.4,-1,1); owned['objects'].append(g)
        original_color=tuple(material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)
        fixed_color=tuple(fixed.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)
        add_icon_root(settings,obj=root); ensure_resolution_default(settings)
        settings.geometry_analysis_mode='WORLD'
        scene.view_layers[0].update()
        transaction.start(scene,settings); stage_owner=settings.studio.stage_id
        check('shared mesh isolated before setup',a.data!=mesh and b.data!=mesh and a.data!=b.data)
        check('protected material kept by identity',g.material_slots[0].material==fixed)
        auto_prepare_materials(scene,settings)
        scan_material_bindings(scene,settings,True)
        seed_palettes_from_detected_scene(settings)
        transaction.refresh_staged_pointers(settings)
        check('two cubes obtain Main and Accent',{'MAIN','ACCENT'}.issubset({x.family for x in settings.bindings}))
        check('bindings use original editable material IDs',all(not x.material.is_evaluated for x in settings.bindings if x.material))
        values={x.material.as_pointer():tuple(read_binding_color(x)) for x in settings.bindings if x.family in {'MAIN','ACCENT'}}
        changed,failed=apply_preview(scene,settings,(.04,.18,.85,1),(.85,.06,.24,1))
        check('preview writes both families',changed>=2 and not failed)
        check('preview changes actual material slots',all(tuple(read_binding_color(x)) != values[x.material.as_pointer()] for x in settings.bindings if x.family in {'MAIN','ACCENT'}))
        check('source material is unchanged',tuple(material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)==original_color)
        check('! material is unchanged',tuple(fixed.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value)==fixed_color)
        clear_runtime_state(); restore_preview(scene)
        check('preview survives loss of Python cache',all(tuple(read_binding_color(x))==values[x.material.as_pointer()] for x in settings.bindings if x.family in {'MAIN','ACCENT'}))
        guard=SceneGuard(scene,settings); oldsize=scene.render.resolution_x
        scene.render.resolution_x=72; a.hide_render=True
        check('guard restores dimensions and visibility',not guard.close() and scene.render.resolution_x==oldsize and not a.hide_render)
        guard=SceneGuard(scene,settings); scene.render.resolution_x=96
        guard.persist()
        check('serialized crash journal restores scene',not recover(scene) and scene.render.resolution_x==oldsize and JOURNAL not in scene)
        guard.closed=True
        if render:
            from mathutils import Vector
            from .render_session import run_sync
            from .batch_export import ExportJob
            camera_data=bpy.data.cameras.new(prefix+'Camera'); owned['cameras'].append(camera_data)
            camera=bpy.data.objects.new(prefix+'Camera',camera_data); owned['objects'].append(camera); scene.collection.objects.link(camera)
            camera.location=(5,-8,5); camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler()
            camera_data.type='ORTHO';camera_data.ortho_scale=5;scene.camera=camera
            light_data=bpy.data.lights.new(prefix+'Key','AREA');owned['lights'].append(light_data)
            light=bpy.data.objects.new(prefix+'Key',light_data);owned['objects'].append(light);scene.collection.objects.link(light)
            light.location=(1,-4,6);light.rotation_euler=(Vector((0,0,0))-light.location).to_track_quat('-Z','Y').to_euler()
            light_data.energy=650;light_data.size=4
            world=bpy.data.worlds.new(prefix+'World');owned['worlds'].append(world);scene.world=world
            world.use_nodes=True;world.node_tree.nodes.get('Background').inputs['Color'].default_value=(.12,.12,.12,1)
            scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=4
            scene.render.resolution_x=96;scene.render.resolution_y=96;scene.render.resolution_percentage=100
            scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
            settings.studio.preview_size=96;settings.studio.preview_samples=4;settings.studio.refine_best=False
            settings.studio.ray_grid=16;settings.studio.generate_inverted=False
            lookbook.generate(settings)
            while len(settings.studio.looks)>2:settings.studio.looks.remove(len(settings.studio.looks)-1)
            settings.studio.looks[0].main_color=(.04,.18,.85,1);settings.studio.looks[0].accent_color=(.85,.06,.24,1)
            lookjob=lookbook.LookJob(scene,settings);run_sync(lookjob)
            owned['images'].extend([x.image for x in settings.studio.looks if x.image]+([settings.studio.sheet] if settings.studio.sheet else []))
            check('real comparison renders exist',all(x.image and x.image.size[0]==96 for x in settings.studio.looks))
            check('real contact sheet generated',settings.studio.sheet is not None)
            restore_preview(scene)
            # Re-running validation in the same evidence directory must not
            # count PNGs from earlier successful runs as new export failures.
            test_output=folder/'outputs'/prefix
            settings.output_folder=str(test_output);settings.studio.batch_from_looks=True
            settings.studio.overwrite_outputs=False;settings.skip_existing=False
            export=ExportJob(scene,settings,False);run_sync(export)
            check('atomic outputs are actual PNGs',export.completed_count==2 and len(list(test_output.rglob('*.png')))==2)
            check('output lock released',not (test_output/'.color_prime.lock').exists())
            try:ExportJob(scene,settings,False)
            except ValueError:checks.append('unverified overwrite is refused')
            else:raise AssertionError('Existing output was accepted without verification/overwrite consent')
            check('render scene restored',scene.render.resolution_x==96 and not settings.render_running and JOURNAL not in scene)
        if save_reload:
            # This is CLI-only: opening a file intentionally replaces all data.
            # The UI never calls this mode.
            name=scene.name;mesh_name=mesh.name;object_name=a.name
            scratch=folder/'stage_roundtrip.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(scratch),check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(scratch))
            scene=bpy.data.scenes[name];settings=scene.color_prime
            check('stage pointers survive save/reload',settings.studio.stage_status=='STAGED' and len(settings.studio.backup_objects)==3)
            transaction.rollback(scene,settings)
            check('structural revert survives save/reload',bpy.data.objects[object_name].data==bpy.data.meshes[mesh_name])
            # IDs held before load are invalid. CLI process exits after report.
            owned={name:[] for name in owned};scene=None
        else:
            count=transaction.rollback(scene,settings)
            check('structural rollback restores shared original mesh',count==3 and a.data==mesh and b.data==mesh)
            check('structural rollback restores original material',a.material_slots[0].material==material and b.material_slots[0].material==material)
            check('rollback clears durable stage',settings.studio.stage_status=='NONE' and not settings.studio.backup_objects)
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        if scene:
            try:
                if scene.color_prime.studio.stage_status!='NONE':transaction.rollback(scene,scene.color_prime)
            except Exception:cleanup_errors.append(traceback.format_exc())
            try:
                if window and original_scene:window.scene=original_scene
                bpy.data.scenes.remove(scene)
            except Exception:cleanup_errors.append(traceback.format_exc())
        # Only specifically retained test IDs (plus owned stage orphans) are removed.
        for collection in ('objects','meshes','materials','node_groups','cameras','lights','worlds','images'):
            for block in owned[collection]:
                try:
                    if block and (collection=='objects' or block.users==0):
                        getattr(bpy.data,collection).remove(block,do_unlink=True)
                except (ReferenceError,RuntimeError):pass
        if stage_owner:
            try:transaction._cleanup_owned(stage_owner)
            except Exception:cleanup_errors.append(traceback.format_exc())
    result={'ok':not(errors or cleanup_errors),'blender':bpy.app.version_string,
            'render_requested':bool(render),'render_tested':'real comparison renders exist' in checks,
            'save_reload_requested':bool(save_reload),'save_reload_tested':'stage pointers survive save/reload' in checks,
            'checks':checks,'errors':errors,'cleanup_errors':cleanup_errors,
            'elapsed_seconds':round(time.monotonic()-started,3),'output':str(folder)}
    (folder/'selftest_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result
