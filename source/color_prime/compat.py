"""Small compatibility layer for Blender 3.x, 4.x and 5.x.

Keep all version/lifecycle quirks here so the feature code does not need to
special-case Blender releases.  Most importantly, bpy.data may be a
_RestrictData proxy while add-ons register.
"""
import bpy

BLENDER_VERSION = tuple(getattr(bpy.app, "version", (0, 0, 0))[:3])


def data_collection(name):
    """Return a bpy.data collection or an empty tuple in restricted state."""
    try:
        data = getattr(bpy, "data", None)
        if data is None:
            return ()
        value = getattr(data, name)
        return value if value is not None else ()
    except (AttributeError, ReferenceError, RuntimeError):
        return ()


def iter_scenes():
    try:
        return tuple(data_collection("scenes"))
    except Exception:
        return ()


def data_get(collection_name, key):
    if not key:
        return None
    collection = data_collection(collection_name)
    try:
        getter = getattr(collection, "get", None)
        return getter(key) if getter else None
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def update_view_layer(context=None):
    try:
        context = context or bpy.context
        view_layer = getattr(context, "view_layer", None)
        if view_layer is not None:
            view_layer.update()
            return True
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    return False


def blender_at_least(major, minor=0, patch=0):
    return BLENDER_VERSION >= (major, minor, patch)


def restricted_data_active():
    try:
        getattr(bpy.data, "scenes")
        return False
    except (AttributeError, ReferenceError, RuntimeError):
        return True


def compositor_tree(scene):
    """Resolve compositor data by capability, including external node-group APIs.

    Older Blender: Scene.use_nodes + Scene.node_tree.
    Newer Blender: Scene.compositing_node_group. Never create a graph merely to
    inspect it, and never assume the old scene.node_tree attribute exists.
    """
    if hasattr(scene, 'compositing_node_group'):
        return getattr(scene, 'compositing_node_group', None)
    if not getattr(scene, 'use_nodes', False):
        return None
    return getattr(scene, 'node_tree', None)


def compositor_file_outputs(tree, path=(), visited=None):
    """Yield unmuted File Output nodes, including nested shader-like node groups."""
    if tree is None:
        return
    visited=set() if visited is None else visited
    key=tree.as_pointer() if hasattr(tree, 'as_pointer') else id(tree)
    if key in visited:
        return
    visited.add(key)
    for node in tree.nodes:
        if getattr(node, 'mute', False):
            continue
        if node.bl_idname=='CompositorNodeOutputFile':
            yield path, node
        elif getattr(node, 'node_tree', None) is not None:
            yield from compositor_file_outputs(node.node_tree, path+(node.name,), visited)


def rna_is_array(prop):
    """Array metadata exists only on numeric RNA descriptors, not on strings.

    Capability-check instead of assuming that every Property subtype implements
    FloatProperty.is_array. Boolean/int/float scalars must remain scalars.
    """
    if getattr(prop, 'type', None) not in {'BOOLEAN', 'INT', 'FLOAT'}:
        return False
    return bool(getattr(prop, 'is_array', False) or getattr(prop, 'array_length', 0))
