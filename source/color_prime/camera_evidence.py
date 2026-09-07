"""Camera-grid nearest-surface evidence, with actual occlusion, for static mesh icons.

Not a semantic segmenter or exact render pass. Transparent volumes, motion blur,
compositing warps and render-only modifiers can disagree with this viewport grid.
"""
import bpy
from .compat import compositor_tree


def role_mask(scene,settings,width,height):
    from mathutils import Vector
    camera=scene.camera
    if camera is None:raise ValueError('An active camera is required')
    if camera.data.type not in {'PERSP','ORTHO'}:
        raise ValueError('Readability measurement supports perspective/orthographic cameras, not panoramic cameras')
    layer=next((vl for vl in scene.view_layers if vl.use), scene.view_layers[0])
    layer.update()
    depsgraph=layer.depsgraph
    projection=camera.calc_matrix_camera(depsgraph,x=width,y=height,
        scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)
    inverse=(projection @ camera.matrix_world.inverted()).inverted()
    family={b.material.as_pointer(): (1 if b.family=='MAIN' else 2 if b.family=='ACCENT' else 3)
            for b in settings.bindings if b.material and b.enabled}
    result=[0]*(width*height);warnings=[]
    evaluated={}
    for y in range(height):
        for x in range(width):
            nx,ny=2*(x+.5)/width-1,2*(y+.5)/height-1
            a=inverse @ Vector((nx,ny,-1,1));b=inverse @ Vector((nx,ny,1,1))
            if abs(a.w)<1e-12 or abs(b.w)<1e-12:continue
            start=Vector(a[:3])/a.w;end=Vector(b[:3])/b.w
            direction=end-start;distance=direction.length
            if distance<=0:continue
            direction.normalize()
            for _ in range(24):
                hit,location,normal,face,obj,matrix=scene.ray_cast(depsgraph,start,direction,distance=distance)
                if not hit:break
                original=getattr(obj,'original',obj)
                if original.hide_render:
                    traveled=(location-start).length+1e-4
                    distance-=traveled
                    if distance<=0:break
                    start=location+direction*1e-4
                    continue
                if original.type!='MESH':break
                ptr=original.as_pointer()
                if ptr not in evaluated:evaluated[ptr]=original.evaluated_get(depsgraph)
                mesh=evaluated[ptr].data
                material=None
                if 0<=face<len(mesh.polygons):
                    slot=mesh.polygons[face].material_index
                    slots=evaluated[ptr].material_slots
                    if 0<=slot<len(slots):material=slots[slot].material
                material=getattr(material,'original',material) if material else None
                result[y*width+x]=family.get(material.as_pointer(),3) if material else 3
                break
    if getattr(scene.render,'use_motion_blur',False):warnings.append('Motion blur is not modeled by the camera grid')
    if compositor_tree(scene) is not None and scene.render.use_compositing:
        warnings.append('Compositor may change pixel positions; numeric correction is disabled')
    if len([vl for vl in scene.view_layers if vl.use]) != 1:
        warnings.append('Multiple render view layers: numeric correction disabled')
    if any(m.show_render != m.show_viewport for obj in scene.objects for m in obj.modifiers):
        warnings.append('Render/viewport modifier settings differ: numeric correction disabled')
    return result,warnings
