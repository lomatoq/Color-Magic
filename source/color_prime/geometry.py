"""Geometry evidence for automatic material-role inference.

Camera coverage is an inexpensive, non-rendering proxy: evaluated triangles are
projected through the active camera and accumulated per material.  It ignores
occlusion, but for icon scenes it is much closer to perceived importance than
raw local polygon area.  World-space triangle area is the universal fallback.
"""
import math
from typing import Dict, Iterable, Optional


def _triangle_area_3d(a, b, c) -> float:
    try:
        return float((b - a).cross(c - a).length) * 0.5
    except Exception:
        return 0.0


def _triangle_area_2d(a, b, c) -> float:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) * 0.5


def _clip_bbox_factor(points) -> float:
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    if max_x <= 0.0 or min_x >= 1.0 or max_y <= 0.0 or min_y >= 1.0:
        return 0.0
    width = max(max_x - min_x, 1e-9)
    height = max(max_y - min_y, 1e-9)
    overlap_x = max(0.0, min(max_x, 1.0) - max(min_x, 0.0)) / width
    overlap_y = max(0.0, min(max_y, 1.0) - max(min_y, 0.0)) / height
    return min(1.0, overlap_x) * min(1.0, overlap_y)


def _evaluated_mesh(obj, depsgraph):
    eval_obj = obj
    mesh = None
    owns_mesh = False
    try:
        if depsgraph is not None and hasattr(obj, 'evaluated_get'):
            eval_obj = obj.evaluated_get(depsgraph)
        if hasattr(eval_obj, 'to_mesh'):
            mesh = eval_obj.to_mesh()
            owns_mesh = mesh is not None
        if mesh is None:
            mesh = getattr(eval_obj, 'data', None)
        return eval_obj, mesh, owns_mesh
    except Exception:
        return obj, getattr(obj, 'data', None), False


def _clear_evaluated_mesh(eval_obj, owns_mesh):
    if owns_mesh:
        try:
            eval_obj.to_mesh_clear()
        except Exception:
            pass


def _loop_triangles(mesh):
    try:
        mesh.calc_loop_triangles()
        return list(mesh.loop_triangles)
    except Exception:
        return []


def _slot_material(obj, index):
    try:
        slots = obj.material_slots
        material = slots[index].material if 0 <= index < len(slots) else None
        # Geometry may be evaluated, but bindings must refer to editable Main
        # database IDs. Evaluated Material pointers are transient and differ
        # from the original slots used by preview/export scope checks.
        return getattr(material, 'original', material) if material is not None else None
    except Exception:
        return None


def world_material_usage(obj, depsgraph=None, max_triangles=30000) -> Dict[object, float]:
    out = {}
    eval_obj, mesh, owns_mesh = _evaluated_mesh(obj, depsgraph)
    try:
        if mesh is None:
            return out
        triangles = _loop_triangles(mesh)
        if triangles:
            stride = max(1, int(math.ceil(len(triangles) / float(max(1, max_triangles)))))
            multiplier = float(stride)
            matrix = eval_obj.matrix_world
            for tri in triangles[::stride]:
                try:
                    a, b, c = (matrix @ mesh.vertices[i].co for i in tri.vertices)
                    area = _triangle_area_3d(a, b, c) * multiplier
                    mat = _slot_material(eval_obj, int(getattr(tri, 'material_index', 0)))
                    if mat is not None and area > 0.0:
                        out[mat] = out.get(mat, 0.0) + area
                except Exception:
                    continue
        else:
            matrix = eval_obj.matrix_world
            for poly in getattr(mesh, 'polygons', ()):
                mat = _slot_material(eval_obj, int(getattr(poly, 'material_index', 0)))
                if mat is None:
                    continue
                try:
                    # Local polygon area with a scale correction is less exact
                    # than triangle transformation but remains a useful fallback.
                    scale = matrix.to_scale()
                    scale_factor = max(1e-9, (abs(scale.x * scale.y) + abs(scale.y * scale.z) + abs(scale.z * scale.x)) / 3.0)
                except Exception:
                    scale_factor = 1.0
                out[mat] = out.get(mat, 0.0) + max(float(getattr(poly, 'area', 0.0)), 1e-8) * scale_factor
    finally:
        _clear_evaluated_mesh(eval_obj, owns_mesh)
    return out


def camera_material_usage(obj, scene, depsgraph=None, max_triangles=30000) -> Dict[object, float]:
    camera = getattr(scene, 'camera', None)
    if camera is None:
        return {}
    out = {}
    eval_obj, mesh, owns_mesh = _evaluated_mesh(obj, depsgraph)
    try:
        if mesh is None:
            return out
        triangles = _loop_triangles(mesh)
        if not triangles:
            return out
        render = scene.render
        x = max(1, int(render.resolution_x * max(render.resolution_percentage, 1) / 100.0))
        y = max(1, int(render.resolution_y * max(render.resolution_percentage, 1) / 100.0))
        scale_x = float(getattr(render, 'pixel_aspect_x', 1.0) or 1.0)
        scale_y = float(getattr(render, 'pixel_aspect_y', 1.0) or 1.0)
        projection = camera.calc_matrix_camera(depsgraph, x=x, y=y, scale_x=scale_x, scale_y=scale_y)
        mvp = projection @ camera.matrix_world.inverted()
        matrix = eval_obj.matrix_world
        stride = max(1, int(math.ceil(len(triangles) / float(max(1, max_triangles)))))
        multiplier = float(stride)
        for tri in triangles[::stride]:
            ndc = []
            valid = True
            for vertex_index in tri.vertices:
                try:
                    world = matrix @ mesh.vertices[vertex_index].co
                    clip = mvp @ world.to_4d()
                    w = float(clip.w)
                    if w <= 1e-8:
                        valid = False
                        break
                    ndc.append((0.5 + 0.5 * float(clip.x) / w, 0.5 + 0.5 * float(clip.y) / w))
                except Exception:
                    valid = False
                    break
            if not valid or len(ndc) != 3:
                continue
            clip_factor = _clip_bbox_factor(ndc)
            if clip_factor <= 0.0:
                continue
            area = min(2.0, _triangle_area_2d(ndc[0], ndc[1], ndc[2])) * clip_factor * multiplier
            mat = _slot_material(eval_obj, int(getattr(tri, 'material_index', 0)))
            if mat is not None and area > 0.0:
                out[mat] = out.get(mat, 0.0) + area
    except Exception:
        return {}
    finally:
        _clear_evaluated_mesh(eval_obj, owns_mesh)
    return out



def camera_object_weight(obj, scene, depsgraph=None, max_triangles=30000) -> float:
    """Projected triangle coverage independent of material assignment."""
    camera = getattr(scene, 'camera', None)
    if camera is None:
        return 0.0
    eval_obj, mesh, owns_mesh = _evaluated_mesh(obj, depsgraph)
    try:
        if mesh is None:
            return 0.0
        triangles = _loop_triangles(mesh)
        if not triangles:
            return 0.0
        render = scene.render
        x = max(1, int(render.resolution_x * max(render.resolution_percentage, 1) / 100.0))
        y = max(1, int(render.resolution_y * max(render.resolution_percentage, 1) / 100.0))
        scale_x = float(getattr(render, 'pixel_aspect_x', 1.0) or 1.0)
        scale_y = float(getattr(render, 'pixel_aspect_y', 1.0) or 1.0)
        projection = camera.calc_matrix_camera(depsgraph, x=x, y=y, scale_x=scale_x, scale_y=scale_y)
        mvp = projection @ camera.matrix_world.inverted()
        matrix = eval_obj.matrix_world
        stride = max(1, int(math.ceil(len(triangles) / float(max(1, max_triangles)))))
        multiplier = float(stride)
        total = 0.0
        for tri in triangles[::stride]:
            ndc = []
            valid = True
            for vertex_index in tri.vertices:
                try:
                    world = matrix @ mesh.vertices[vertex_index].co
                    clip = mvp @ world.to_4d()
                    w = float(clip.w)
                    if w <= 1e-8:
                        valid = False
                        break
                    ndc.append((0.5 + 0.5 * float(clip.x) / w, 0.5 + 0.5 * float(clip.y) / w))
                except Exception:
                    valid = False
                    break
            if not valid or len(ndc) != 3:
                continue
            clip_factor = _clip_bbox_factor(ndc)
            if clip_factor <= 0.0:
                continue
            total += min(2.0, _triangle_area_2d(ndc[0], ndc[1], ndc[2])) * clip_factor * multiplier
        return max(0.0, total)
    except Exception:
        return 0.0
    finally:
        _clear_evaluated_mesh(eval_obj, owns_mesh)


def world_object_weight(obj, depsgraph=None, max_triangles=30000) -> float:
    eval_obj, mesh, owns_mesh = _evaluated_mesh(obj, depsgraph)
    try:
        if mesh is None:
            return 0.0
        triangles = _loop_triangles(mesh)
        if triangles:
            matrix = eval_obj.matrix_world
            stride = max(1, int(math.ceil(len(triangles) / float(max(1, max_triangles)))))
            multiplier = float(stride)
            total = 0.0
            for tri in triangles[::stride]:
                try:
                    a, b, c = (matrix @ mesh.vertices[i].co for i in tri.vertices)
                    total += _triangle_area_3d(a, b, c) * multiplier
                except Exception:
                    continue
            return max(0.0, total)
        return max(0.0, float(sum(max(float(getattr(poly, 'area', 0.0)), 0.0)
                                  for poly in getattr(mesh, 'polygons', ()))))
    except Exception:
        return 0.0
    finally:
        _clear_evaluated_mesh(eval_obj, owns_mesh)


def material_usage_for_objects(objects: Iterable, scene=None, mode='AUTO') -> Dict[object, float]:
    total = {}
    depsgraph = None
    if scene is not None:
        try:
            depsgraph = scene.evaluated_depsgraph_get()
        except Exception:
            try:
                import bpy
                depsgraph = bpy.context.evaluated_depsgraph_get()
            except Exception:
                depsgraph = None
    mode = str(mode or 'AUTO').upper()
    for obj in objects:
        if getattr(obj, 'type', None) != 'MESH':
            continue
        usage = {}
        if scene is not None and mode in {'AUTO', 'CAMERA'}:
            usage = camera_material_usage(obj, scene, depsgraph)
        if not usage:
            usage = world_material_usage(obj, depsgraph)
        if not usage:
            # Last-resort slot weight preserves discoverability for empty meshes
            # and procedural objects whose evaluated geometry is unavailable.
            for slot in getattr(obj, 'material_slots', ()):
                mat = getattr(slot, 'material', None)
                if mat is not None:
                    usage[mat] = usage.get(mat, 0.0) + 1.0
        for mat, weight in usage.items():
            total[mat] = total.get(mat, 0.0) + max(float(weight), 1e-8)
    return total


def object_visual_weight(obj, scene=None, mode='AUTO') -> float:
    # Measure geometry directly so blank objects with no material can still be
    # ranked through the camera during first-run bootstrap.
    depsgraph = None
    if scene is not None:
        try:
            depsgraph = scene.evaluated_depsgraph_get()
        except Exception:
            try:
                import bpy
                depsgraph = bpy.context.evaluated_depsgraph_get()
            except Exception:
                depsgraph = None
    mode = str(mode or 'AUTO').upper()
    if scene is not None and mode in {'AUTO', 'CAMERA'}:
        camera_weight = camera_object_weight(obj, scene, depsgraph)
        if camera_weight > 1e-10:
            return camera_weight
    world_weight = world_object_weight(obj, depsgraph)
    if world_weight > 1e-10:
        return world_weight
    usage = material_usage_for_objects([obj], scene, mode)
    return max(1e-8, sum(usage.values())) if usage else 1.0
