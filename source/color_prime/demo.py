"""Small self-contained example. Adds a scene; never clears an existing scene."""
from array import array
import bpy
from .selftest import _cube_mesh, _material


def create(context, operator):
    if context.window is None:
        raise RuntimeError('Create Demo needs an interactive Blender window')
    if context.mode != 'OBJECT':
        raise RuntimeError('Switch to Object Mode first')
    from mathutils import Vector
    scene=bpy.data.scenes.new('Color Prime — Demo')
    context.window.scene=scene
    root=bpy.data.objects.new('CP Demo Icon',None);scene.collection.objects.link(root)
    paint=_material('Demo Paint',(.07,.35,.22,1))
    gold=_material('Latch!',(.7,.38,.055,1))
    shader=gold.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Metallic'].default_value=.7;shader.inputs['Roughness'].default_value=.28
    for name,scale,z,material in (
        ('Body',(1.15,.72,.64),0.,paint),('Lid',(1.20,.76,.23),.82,paint),
        ('Protected latch',(.16,.10,.27),.38,gold)):
        mesh=_cube_mesh('Demo '+name);mesh.materials.append(material)
        # Bake local dimensions for uniform bevel width (no operator/context dependency).
        for vertex in mesh.vertices:
            for axis in range(3):vertex.co[axis]*=scale[axis]
        mesh.update()
        obj=bpy.data.objects.new(name,mesh);scene.collection.objects.link(obj);obj.parent=root
        obj.location.z=z
        if name=='Protected latch':obj.location.y=-.81
        bevel=obj.modifiers.new('Soft crafted edges','BEVEL');bevel.width=.09 if name!='Protected latch' else .04
        bevel.segments=3
    camera_data=bpy.data.cameras.new('Demo Camera');camera=bpy.data.objects.new('Demo Camera',camera_data)
    scene.collection.objects.link(camera);camera.location=(4,-6,3.5)
    camera.rotation_euler=(Vector((0,0,.35))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera_data.type='ORTHO';camera_data.ortho_scale=4.1;scene.camera=camera
    for name,position,energy,size in (('Key',(-3,-4,6),700,4),('Fill',(4,-1,3),260,3),('Rim',(0,4,5),550,3)):
        light_data=bpy.data.lights.new('Demo '+name,'AREA');light_data.energy=energy;light_data.size=size
        light=bpy.data.objects.new('Demo '+name,light_data);scene.collection.objects.link(light)
        light.location=position;light.rotation_euler=(Vector((0,0,.3))-light.location).to_track_quat('-Z','Y').to_euler()
    world=bpy.data.worlds.new('Demo World');world.use_nodes=True
    world.node_tree.nodes.get('Background').inputs['Color'].default_value=(.10,.10,.10,1);scene.world=world
    scene.render.engine='CYCLES';scene.cycles.samples=24
    scene.render.resolution_x=512;scene.render.resolution_y=512;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
    scene.render.film_transparent=True
    image=bpy.data.images.new('Demo Reference — synthetic test palette',128,128,alpha=True)
    image.colorspace_settings.name='sRGB'
    pixels=array('f')
    for y in range(128):
        for x in range(128):
            # Deliberately includes background, a shaded main region and a compact accent.
            rgba=(.95,.95,.95,1)
            if 20<x<106 and 18<y<106:rgba=(.16,.68,.42,1)
            if 20<x<106 and 18<y<36:rgba=(.08,.37,.23,1)
            if 77<x<112 and 71<y<110:rgba=(.96,.44,.13,1)
            pixels.extend(rgba)
    image.pixels.foreach_set(pixels);image.update();image.pack()
    scene.color_prime.reference_image=image
    scene.color_prime.studio.preview_size=256
    scene.color_prime.studio.preview_samples=12
    for obj in scene.objects:obj.select_set(False)
    root.select_set(True);scene.view_layers[0].objects.active=root
    scene.view_layers[0].update()
    from .studio_ops import run_auto
    result=run_auto(operator,context)
    if result != {'FINISHED'}:
        raise RuntimeError('Demo geometry was added, but automatic setup failed; see last error')
    return scene
