"""Trace an authored COLOR dependency, not a common shader's name.

Supported: RGB -> reroute / hue-saturation / gamma / curves / one-source MixRGB
-> (nested) color groups -> Principled Base Color. Ambiguous multi-source graphs
abstain. Exposed group defaults are instance-local, never shared-family evidence.
No writes occur during trace; group paths are localized only after a unique source
has been established. This is deliberately not a general shader interpreter.
"""
from dataclasses import dataclass
from .utils import data_block_identity


@dataclass
class ColorSource:
    node: object
    socket: object
    group_path: tuple
    tree: object
    is_output: bool
    lineage: str = ''
    reason: str = ''


_TRANSFORM_INPUT = {'HUE_SAT':'Color','GAMMA':'Color','BRIGHTCONTRAST':'Color','CURVE_RGB':'Color'}


def _match(sockets, target, fallback):
    ident = getattr(target,'identifier','')
    if ident:
        matches = [s for s in sockets if getattr(s,'identifier','') == ident]
        if len(matches) == 1:
            return matches[0]
    matches = [s for s in sockets if s.name == target.name]
    if len(matches) == 1:
        return matches[0]
    return sockets[fallback] if 0 <= fallback < len(sockets) else None


def _source_key(source):
    return (source.group_path, source.node.name, source.socket.name,
            getattr(source.socket,'identifier',''), source.is_output)


def trace_color_socket(socket, tree, path=(), callers=(), visited=None, budget=80):
    if socket is None or getattr(socket,'node',None) is None or budget <= 0:
        return []
    visited = set() if visited is None else visited
    key = (id(tree), path, id(socket))
    if key in visited:
        return []
    visited = visited | {key}
    if not getattr(socket,'is_output',False):
        if not socket.is_linked:
            if getattr(socket,'type','') != 'RGBA':
                return []
            return [ColorSource(socket.node,socket,path,tree,False,
                    reason='instance-local authored color input')]
        links = list(socket.links)
        if len(links) != 1:
            return []
        return trace_color_socket(links[0].from_socket,tree,path,callers,visited,budget-1)
    node = socket.node
    typ = node.type
    if typ == 'RGB':
        original = tree.get('color_prime_source_identity','') or data_block_identity(tree)
        lineage = '{}::RGB::{}'.format(original,node.name) if path else ''
        return [ColorSource(node,socket,path,tree,True,lineage,
                'authored RGB color dependency; transforms retained')]
    if typ == 'REROUTE':
        return trace_color_socket(node.inputs[0],tree,path,callers,visited,budget-1)
    if typ in _TRANSFORM_INPUT:
        return trace_color_socket(node.inputs.get(_TRANSFORM_INPUT[typ]),tree,path,callers,visited,budget-1)
    if typ == 'GROUP' and node.node_tree:
        outputs = [n for n in node.node_tree.nodes if n.type == 'GROUP_OUTPUT']
        active = [n for n in outputs if getattr(n,'is_active_output',False)]
        outputs = active or outputs
        if len(outputs) != 1:
            return []
        idx = list(node.outputs).index(socket)
        inner = _match(outputs[0].inputs,socket,idx)
        return trace_color_socket(inner,node.node_tree,path+(node.name,),callers+((tree,path,node),),visited,budget-1)
    if typ == 'GROUP_INPUT' and callers:
        parent_tree,parent_path,group = callers[-1]
        idx = list(node.outputs).index(socket)
        outer = _match(group.inputs,socket,idx)
        return trace_color_socket(outer,parent_tree,parent_path,callers[:-1],visited,budget-1)
    if typ == 'MIX_RGB':
        fac,a,b = node.inputs[:3]
        if node.get('cp_selection_texture_tint',False):
            # Our explicit texture multiplier has exactly one managed color;
            # its other input remains the untouched authored texture graph.
            return trace_color_socket(b,tree,path,callers,visited,budget-1)
        if not fac.is_linked and node.blend_type == 'MIX' and not node.get('cp_family_shade',False):
            t = float(fac.default_value)
            if t <= 0:
                return trace_color_socket(a,tree,path,callers,visited,budget-1)
            if t >= 1:
                return trace_color_socket(b,tree,path,callers,visited,budget-1)
        linked = [s for s in (a,b) if s.is_linked]
        # The other constant is an authored tint/shade, NOT another family.
        if not linked:
            return []
        result = []
        for inp in linked:
            found = trace_color_socket(inp,tree,path,callers,visited,budget-1)
            if not found:
                return []
            result.extend(found)
        unique = {_source_key(s):s for s in result}
        return list(unique.values()) if len(unique) == 1 else []
    return []


def principled_color_source(material, principled_nodes):
    if not material or not material.node_tree or not principled_nodes:
        return None
    sources = []
    for node in principled_nodes:
        found = trace_color_socket(node.inputs.get('Base Color'),material.node_tree)
        if len(found) != 1:
            return None
        sources.extend(found)
    unique = {_source_key(s):s for s in sources}
    return next(iter(unique.values())) if len(unique) == 1 else None


def localize_source(material, source):
    """Copy nested groups from outside in; preserve the true source identity."""
    tree = material.node_tree
    owner = str(material.get('cp_studio_owner',''))
    for name in source.group_path:
        node = tree.nodes.get(name)
        if node is None or node.node_tree is None:
            raise RuntimeError('Color dependency changed while localizing: ' + name)
        original = node.node_tree
        # Repeated scans reuse our material-local copy, never copy repeatedly.
        local_for = str(original.get('color_prime_local_material',''))
        if local_for != data_block_identity(material):
            copy = original.copy()
            copy['color_prime_source_identity'] = original.get('color_prime_source_identity','') or data_block_identity(original)
            copy['color_prime_source_group'] = original.get('color_prime_source_group','') or original.name
            copy['color_prime_local_material'] = data_block_identity(material)
            if owner:
                copy['cp_studio_owner'] = owner
            node.node_tree = copy
        tree = node.node_tree
    return tree
