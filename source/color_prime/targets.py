from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Set, Tuple
import re
import json
import math
import bpy
from .compat import data_get
from .color_math import color4
from .constants import ACCENT_NAME_TOKENS, COLOR_SOCKET_ALIASES, EXPLICIT_TARGET_TOKENS, LEGACY_ACCENT_PREFIXES, LEGACY_MAIN_PREFIXES, MAIN_NAME_TOKENS
from .utils import data_block_identity, log, sanitize_filename

@dataclass
class TargetSpec:
    kind: str = 'NONE'
    node_name: str = ''
    socket_name: str = ''
    socket_index: int = -1
    group_node_name: str = ''
    inner_node_name: str = ''
    legacy_source_group_name: str = ''
    source_group_name: str = ''
    source_group_identity: str = ''
    source_group_stem: str = ''
    color: Tuple[float, float, float, float] = (0, 0, 0, 1)
    family_hint: str = ''
    confidence: float = 0.0
    reason: str = ''
    texture_driven: bool = False
    protected_shader: bool = False
    metallic: float = 0.0
    roughness: float = 0.5
    writable: bool = False
    path_json: str = '[]'

@dataclass
class TargetSnapshot:
    material_name: str
    material_library: str
    kind: str
    node_name: str
    socket_name: str
    socket_index: int
    group_node_name: str
    inner_node_name: str
    color: Tuple[float, float, float, float]
    material_ref: object = field(default=None, repr=False, compare=False)
    path_json: str = '[]'

def _has_token(value: str, tokens: Iterable[str]):
    value = (value or '').casefold()
    return any((t.casefold() in value for t in tokens))

def _node_text(node):
    values = [getattr(node, 'name', ''), getattr(node, 'label', '')]
    if getattr(node, 'node_tree', None):
        values.append(node.node_tree.name)
    return ' '.join(values).casefold()

def _explicit(node):
    try:
        if bool(node.get('color_prime_target', False)):
            return True
    except Exception:
        pass
    return _has_token(_node_text(node), EXPLICIT_TARGET_TOKENS)

def _tree_hint(name):
    for p in LEGACY_MAIN_PREFIXES:
        if name == p or name.startswith(p + '.'):
            return 'MAIN'
    for p in LEGACY_ACCENT_PREFIXES:
        if name == p or name.startswith(p + '.'):
            return 'ACCENT'
    return ''

def _group_stem(name):
    """Normalize Blender duplicate suffixes so Foo/Foo.001 share a lineage."""
    name = str(name or '')
    return re.sub(r'\.\d{3,}$', '', name)


def _group_source_info(node):
    tree = getattr(node, 'node_tree', None)
    if tree is None:
        return ('', '', '')
    try:
        source_name = str(tree.get('color_prime_source_group', '') or tree.name)
        source_identity = str(tree.get('color_prime_source_identity', '') or data_block_identity(tree))
    except Exception:
        source_name = getattr(tree, 'name', '')
        source_identity = data_block_identity(tree)
    return (source_name, source_identity, _group_stem(source_name))


def _attach_group_source(spec, node):
    source_name, source_identity, stem = _group_source_info(node)
    spec.source_group_name = source_name
    spec.source_group_identity = source_identity if spec.kind != 'NODE_INPUT' else ''
    spec.source_group_stem = source_identity if spec.kind != 'NODE_INPUT' else ''
    if not spec.legacy_source_group_name:
        spec.legacy_source_group_name = source_name
    return spec

def _socket_is_color(socket):
    return getattr(socket, 'type', '') == 'RGBA'

def _find_color_input(node):
    inputs = getattr(node, 'inputs', ())
    for alias in COLOR_SOCKET_ALIASES:
        try:
            sock = inputs.get(alias)
        except Exception:
            sock = None
        if sock is not None and _socket_is_color(sock):
            return (sock, list(inputs).index(sock))
    for i, sock in enumerate(inputs):
        if _socket_is_color(sock) and _has_token(getattr(sock, 'name', ''), ('color', 'tint', 'albedo')):
            return (sock, i)
    return (None, -1)

def _get_input(node, name, index):
    if node is None:
        return None
    inputs = node.inputs
    if name:
        try:
            sock = inputs.get(name)
        except Exception:
            sock = None
        if sock is not None:
            return sock
    return inputs[index] if 0 <= index < len(inputs) else None

def _get_output(node, name, index):
    if node is None:
        return None
    outputs = node.outputs
    if name:
        try:
            sock = outputs.get(name)
        except Exception:
            sock = None
        if sock is not None:
            return sock
    if 0 <= index < len(outputs):
        return outputs[index]
    return outputs[0] if outputs else None

def _reachable_nodes(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    outputs = [n for n in material.node_tree.nodes if n.type == 'OUTPUT_MATERIAL']
    active = [n for n in outputs if bool(getattr(n, 'is_active_output', False))]
    if active:
        outputs = active
    stack = []
    for out in outputs:
        sock = _get_input(out, 'Surface', -1)
        if sock and sock.is_linked:
            stack.extend((link.from_node for link in sock.links))
    seen, result = (set(), [])
    while stack:
        node = stack.pop()
        ptr = node.as_pointer()
        if ptr in seen:
            continue
        seen.add(ptr)
        result.append(node)
        for sock in node.inputs:
            if sock.is_linked:
                stack.extend((link.from_node for link in sock.links))
    return result

def _principled_nodes(material):
    reachable = [n for n in _reachable_nodes(material) if n.bl_idname == 'ShaderNodeBsdfPrincipled']
    if reachable:
        return reachable
    if not material or not material.node_tree:
        return []
    return [n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled']

def _float_input(node, name, default):
    try:
        sock = node.inputs.get(name)
        return float(sock.default_value) if sock and (not sock.is_linked) else default
    except Exception:
        return default

def _metadata(material):
    principled = _principled_nodes(material)
    metallic = _float_input(principled[0], 'Metallic', 0.0) if principled else 0.0
    roughness = _float_input(principled[0], 'Roughness', 0.5) if principled else 0.5
    protected = False
    if material and material.node_tree:
        protected_ids = {'ShaderNodeBsdfGlass', 'ShaderNodeVolumePrincipled', 'ShaderNodeHoldout'}
        protected = any((n.bl_idname in protected_ids for n in material.node_tree.nodes))
    return (metallic, roughness, protected)

def _rgb_spec(node, hint='', confidence=0.9, reason='RGB node'):
    sock = _get_output(node, 'Color', 0)
    if sock is None:
        return TargetSpec(reason='RGB node has no output')
    return TargetSpec(kind='RGB_OUTPUT', node_name=node.name, socket_name=sock.name, socket_index=list(node.outputs).index(sock), color=color4(sock.default_value), family_hint=hint, confidence=confidence, reason=reason, writable=True)

def _input_spec(node, sock, index, hint='', confidence=0.8, reason='Node input'):
    return TargetSpec(kind='NODE_INPUT', node_name=node.name, socket_name=sock.name, socket_index=index, color=color4(sock.default_value), family_hint=hint, confidence=confidence, reason=reason, writable=not sock.is_linked)

def _pick_inner_rgb(tree, hint):
    nodes = [n for n in tree.nodes if n.type == 'RGB']
    explicit = [n for n in nodes if _explicit(n)]
    if len(explicit) == 1:
        return explicit[0]
    tokens = MAIN_NAME_TOKENS if hint == 'MAIN' else ACCENT_NAME_TOKENS
    named = [n for n in nodes if _has_token(_node_text(n), tokens)]
    if len(named) == 1:
        return named[0]
    return nodes[0] if len(nodes) == 1 else None

def _localize_group(material, node):
    original = node.node_tree
    owner = data_block_identity(material)
    try:
        if original.get('color_prime_owner_material', '') == owner:
            return (original, 'Already localized')
    except Exception:
        pass
    try:
        source_name, source_identity, _stem = _group_source_info(node)
        copied = original.copy()
        copied.name = 'CP__{}__{}'.format(sanitize_filename(source_name or original.name), sanitize_filename(material.name))
        copied['color_prime_owner_material'] = owner
        copied['color_prime_source_group'] = source_name or original.name
        copied['color_prime_source_identity'] = source_identity or data_block_identity(original)
        node.node_tree = copied
        log("Localized color group '{}' for '{}'".format(source_name or original.name, material.name))
        return (copied, 'Localized from {}'.format(source_name or original.name))
    except Exception as exc:
        log("Could not localize '{}' for '{}'".format(original.name, material.name), 'ERROR', exc)
        return (None, str(exc))

def _group_spec(material, node, localize, hint='', confidence=0.95, reason='Inherited color group'):
    tree = node.node_tree
    if tree is None:
        return None
    source_name, source_identity, source_stem = _group_source_info(node)
    sock, idx = _find_color_input(node)
    if sock and (not sock.is_linked):
        spec = _input_spec(node, sock, idx, hint, confidence, reason + ' with local color input')
        return _attach_group_source(spec, node)
    inner = _pick_inner_rgb(tree, hint)
    if inner is None:
        return None
    if not localize:
        spec = TargetSpec(kind='NONE', group_node_name=node.name, inner_node_name=inner.name,
                          legacy_source_group_name=source_name, source_group_name=source_name,
                          source_group_identity=source_identity, source_group_stem=source_identity,
                          color=color4(inner.outputs[0].default_value), family_hint=hint,
                          confidence=confidence, reason=reason + ' needs material-local copy', writable=False)
        return spec
    tree, why = _localize_group(material, node)
    if tree is None:
        return TargetSpec(family_hint=hint, legacy_source_group_name=source_name,
                          source_group_name=source_name, source_group_identity=source_identity,
                          source_group_stem=source_identity, reason='Localization failed: ' + why)
    inner = _pick_inner_rgb(tree, hint)
    if inner is None:
        return TargetSpec(family_hint=hint, legacy_source_group_name=source_name,
                          source_group_name=source_name, source_group_identity=source_identity,
                          source_group_stem=source_identity, reason=reason + ' has ambiguous internal RGB nodes')
    out = _get_output(inner, 'Color', 0)
    spec = TargetSpec(kind='LEGACY_GROUP_RGB', group_node_name=node.name, inner_node_name=inner.name,
                      legacy_source_group_name=source_name, source_group_name=source_name,
                      source_group_identity=source_identity, source_group_stem=source_identity,
                      socket_name=out.name, socket_index=list(inner.outputs).index(out),
                      color=color4(out.default_value), family_hint=hint, confidence=confidence,
                      reason=why + '; target is material-local', writable=True)
    return spec


def _legacy_spec(material, node, localize):
    tree = node.node_tree
    hint = _tree_hint(tree.name) if tree else ''
    if not hint:
        return None
    return _group_spec(material, node, localize, hint, 0.99, 'Legacy {} family group'.format(hint.title()))


def _generic_group_candidates(material):
    """Conservative generic family-group discovery.

    A candidate must be part of the active shader graph when possible and have
    either an exposed unlinked color socket or one unambiguous internal RGB.
    """
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    reachable = [n for n in _reachable_nodes(material) if n.type == 'GROUP' and n.node_tree]
    groups = reachable or [n for n in material.node_tree.nodes if n.type == 'GROUP' and n.node_tree]
    out = []
    for node in groups:
        if _tree_hint(node.node_tree.name):
            continue
        sock, _idx = _find_color_input(node)
        if sock is not None and not sock.is_linked:
            out.append(node)
            continue
        if _pick_inner_rgb(node.node_tree, '') is not None:
            out.append(node)
    # Exactly one generic color-bearing group is unambiguous. Multiple groups
    # are deliberately left alone instead of guessing.
    return out

def find_material_target(material, localize_legacy=False):
    metallic, roughness, protected = _metadata(material)
    base = TargetSpec(metallic=metallic, roughness=roughness, protected_shader=protected)
    if material is None:
        base.reason = 'Material is missing'
        return base
    if not material.use_nodes or material.node_tree is None:
        base.reason = 'Material does not use nodes'
        return base
    from .family_links import family_node,shared_socket,color_source
    shared=family_node(material);socket=shared_socket(material)
    if shared and socket is not None:
        return TargetSpec(kind='PATH_RGB',node_name=socket.node.name,socket_name=socket.name,socket_index=0,
            path_json=json.dumps(list(color_source(material).group_path)),color=color4(socket.default_value),
            family_hint=shared.node_tree.get('cp_family_role',''),confidence=1.,writable=True,
            reason='Shared Prime family source',source_group_name=shared.node_tree.name,
            source_group_identity=data_block_identity(shared.node_tree),source_group_stem=data_block_identity(shared.node_tree),
            metallic=metallic,roughness=roughness,protected_shader=protected)
    nodes = list(material.node_tree.nodes)
    if sum(bool(_explicit(n)) for n in nodes) > 1:
        base.reason = 'Multiple explicit color targets; choose exactly one'
        return base
    for node in nodes:
        if not _explicit(node):
            continue
        txt = _node_text(node)
        hint = 'ACCENT' if _has_token(txt, ACCENT_NAME_TOKENS) else 'MAIN' if _has_token(txt, MAIN_NAME_TOKENS) else ''
        spec = _rgb_spec(node, hint, 1.0, 'Explicit Color Prime RGB target') if node.type == 'RGB' else None
        if spec is None:
            sock, idx = _find_color_input(node)
            if sock:
                spec = _input_spec(node, sock, idx, hint, 1.0, 'Explicit Color Prime node input')
        if spec:
            spec.metallic, spec.roughness, spec.protected_shader = (metallic, roughness, protected)
            return spec
    # Trace the dependency that actually feeds Base Color. Generic shader
    # reuse is not evidence that colors inherit from the same source.
    from .color_graph import principled_color_source, localize_source
    source = color_source(material) or principled_color_source(material, _principled_nodes(material))
    if source is not None:
        if source.group_path:
            if localize_legacy and not source.tree.get('cp_studio_owner',''):
                localize_source(material, source)
            kind = 'PATH_RGB' if source.is_output else 'PATH_INPUT'
        else:
            kind = 'RGB_OUTPUT' if source.is_output else 'NODE_INPUT'
        family_hint = ''
        for group_name in source.group_path:
            group = material.node_tree.nodes.get(group_name)
            tree_name = group.node_tree.name if group and getattr(group,'node_tree',None) else group_name
            family_hint = _tree_hint(tree_name) or family_hint
        return TargetSpec(kind=kind, node_name=source.node.name,
            socket_name=source.socket.name,
            socket_index=list(source.node.outputs if source.is_output else source.node.inputs).index(source.socket),
            color=color4(source.socket.default_value), family_hint=family_hint,
            source_group_name=source.tree.name if source.lineage else '',
            source_group_identity=source.lineage, source_group_stem=source.lineage,
            path_json=json.dumps(list(source.group_path)), reason=source.reason,
            confidence=.96, writable=(not source.group_path or localize_legacy or bool(source.tree.get('cp_studio_owner',''))), metallic=metallic, roughness=roughness, protected_shader=protected)
    linked_principled = [n for n in _principled_nodes(material)
                          if _get_input(n, 'Base Color', -1) is not None
                          and _get_input(n, 'Base Color', -1).is_linked]
    if linked_principled and any(getattr(link, 'from_socket', None) is not None
        for n in linked_principled for link in _get_input(n, 'Base Color', -1).links):
        base.texture_driven = True
        base.reason = 'Ambiguous or unsupported active color graph; select an explicit target'
        return base
    for node in nodes:
        if node.type == 'GROUP' and node.node_tree:
            spec = _legacy_spec(material, node, localize_legacy)
            if spec:
                spec.metallic, spec.roughness, spec.protected_shader = (metallic, roughness, protected)
                return spec
    generic_groups = _generic_group_candidates(material)
    if len(generic_groups) == 1:
        spec = _group_spec(material, generic_groups[0], localize_legacy, '', 0.96, 'Auto-detected inherited color group')
        if spec:
            spec.metallic, spec.roughness, spec.protected_shader = (metallic, roughness, protected)
            return spec
    named_rgb = [n for n in nodes if n.type == 'RGB' and _has_token(_node_text(n), MAIN_NAME_TOKENS + ACCENT_NAME_TOKENS)]
    if len(named_rgb) == 1:
        txt = _node_text(named_rgb[0])
        hint = 'ACCENT' if _has_token(txt, ACCENT_NAME_TOKENS) else 'MAIN'
        spec = _rgb_spec(named_rgb[0], hint, 0.96, 'Named family RGB node')
        spec.metallic, spec.roughness, spec.protected_shader = (metallic, roughness, protected)
        return spec
    for princ in _principled_nodes(material):
        sock = _get_input(princ, 'Base Color', -1)
        if sock is None:
            continue
        if sock.is_linked:
            links = list(sock.links)
            if len(links) == 1 and links[0].from_node.type == 'RGB':
                spec = _rgb_spec(links[0].from_node, '', 0.9, 'RGB directly feeds active Principled Base Color')
                spec.metallic, spec.roughness, spec.protected_shader = (metallic, roughness, protected)
                return spec
            base.texture_driven = True
            continue
        spec = _input_spec(princ, sock, list(princ.inputs).index(sock), '', 0.82, 'Unlinked active Principled Base Color')
        spec.metallic, spec.roughness, spec.protected_shader = (metallic, roughness, protected)
        return spec
    base.reason = 'Active Base Color is linked/texture-driven' if base.texture_driven else 'No unambiguous writable color target'
    return base

def apply_spec_to_binding(binding, spec):
    binding.target_kind = spec.kind
    if hasattr(binding, 'target_path_json'):
        binding.target_path_json = spec.path_json
    binding.target_node_name = spec.node_name
    binding.target_socket_name = spec.socket_name
    binding.target_socket_index = spec.socket_index
    binding.target_group_node_name = spec.group_node_name
    binding.target_inner_node_name = spec.inner_node_name
    binding.legacy_source_group_name = spec.legacy_source_group_name
    if hasattr(binding, 'source_group_name'):
        binding.source_group_name = spec.source_group_name
        binding.source_group_identity = spec.source_group_identity
        binding.source_group_stem = spec.source_group_stem
    binding.status = spec.reason

def _resolve(binding):
    mat = binding.material
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    if binding.target_kind in {'PATH_RGB','PATH_INPUT'}:
        tree = mat.node_tree
        try:
            for name in json.loads(getattr(binding,'target_path_json','[]')):
                node = tree.nodes.get(name)
                if node is None or node.node_tree is None:
                    return None
                tree = node.node_tree
        except (ValueError, AttributeError, TypeError):
            return None
        node = tree.nodes.get(binding.target_node_name)
        getter = _get_output if binding.target_kind == 'PATH_RGB' else _get_input
        return getter(node, binding.target_socket_name, binding.target_socket_index)
    if binding.target_kind == 'NODE_INPUT':
        return _get_input(mat.node_tree.nodes.get(binding.target_node_name), binding.target_socket_name, binding.target_socket_index)
    if binding.target_kind == 'RGB_OUTPUT':
        return _get_output(mat.node_tree.nodes.get(binding.target_node_name), binding.target_socket_name, binding.target_socket_index)
    if binding.target_kind == 'LEGACY_GROUP_RGB':
        group = mat.node_tree.nodes.get(binding.target_group_node_name)
        tree = group.node_tree if group else None
        inner = tree.nodes.get(binding.target_inner_node_name) if tree else None
        return _get_output(inner, binding.target_socket_name, binding.target_socket_index)
    return None

def read_binding_color(binding):
    sock = _resolve(binding)
    try:
        value=color4(sock.default_value) if sock else None
        from .family_links import shared_socket
        if value is not None and shared_socket(binding.material) and binding.material.get('color_prime_child',False):
            from .child_materials import child_color
            value=child_color(binding.material,value)
        return value
    except Exception:
        return None

def write_binding_color(binding, value: Sequence[float]):
    try:
        sock = _resolve(binding)
        if sock is None:
            return False
        # Connections drive INPUT values. A connected RGB OUTPUT remains the
        # writable source of those connections; is_linked is true for both.
        is_output = bool(getattr(sock, 'is_output', False)) or binding.target_kind in {
            'RGB_OUTPUT', 'LEGACY_GROUP_RGB', 'PATH_RGB'}
        if not is_output and bool(getattr(sock, 'is_linked', False)):
            return False
        sock.default_value = color4(value)
        return True
    except Exception as exc:
        try:
            name = binding.material.name if binding.material else 'missing'
        except (AttributeError, ReferenceError, RuntimeError):
            name = 'removed material'
        log("Failed to set target on '{}'".format(name), 'ERROR', exc)
        return False

def binding_target_is_valid(binding):
    return _resolve(binding) is not None

def make_snapshot(binding):
    mat, socket = binding.material, _resolve(binding)
    if mat is None or socket is None:
        return None
    color = tuple(float(v) for v in socket.default_value)
    if len(color) != 4 or not all(math.isfinite(v) for v in color):
        raise ValueError('Cannot snapshot a non-finite or non-RGBA color target')
    lib = getattr(getattr(mat, 'library', None), 'filepath', '') or ''
    return TargetSnapshot(mat.name, lib, binding.target_kind, binding.target_node_name, binding.target_socket_name, binding.target_socket_index, binding.target_group_node_name, binding.target_inner_node_name, color, mat, getattr(binding, 'target_path_json', '[]'))

def restore_snapshot(snapshot):
    mat = getattr(snapshot, 'material_ref', None)
    if mat is not None:
        try:
            # Runtime snapshots follow a renamed datablock. Never restore into
            # a different material that has acquired its old name.
            if not mat.as_pointer():
                return False
        except (ReferenceError, AttributeError, RuntimeError):
            return False
    else:
        mat = data_get('materials', snapshot.material_name)
        if mat is None:
            return False
        library = getattr(getattr(mat, 'library', None), 'filepath', '') or ''
        if library != snapshot.material_library:
            return False

    class B:
        pass
    b = B()
    b.material = mat
    b.target_kind = snapshot.kind
    b.target_node_name = snapshot.node_name
    b.target_socket_name = snapshot.socket_name
    b.target_socket_index = snapshot.socket_index
    b.target_group_node_name = snapshot.group_node_name
    b.target_inner_node_name = snapshot.inner_node_name
    b.target_path_json = getattr(snapshot, 'path_json', '[]')
    socket = _resolve(b)
    if socket is None:
        return False
    output = bool(getattr(socket, 'is_output', False)) or b.target_kind in {'RGB_OUTPUT','PATH_RGB','LEGACY_GROUP_RGB'}
    if not output and bool(getattr(socket, 'is_linked', False)):
        return False
    try:
        # Restore exact authored floats, including HDR/negative components.
        # Palette clamping is intentionally NOT used for recovery.
        values = tuple(float(v) for v in snapshot.color)
        if len(values) != 4 or not all(math.isfinite(v) for v in values):
            return False
        socket.default_value = values
        return True
    except (ValueError, TypeError, RuntimeError, AttributeError, ReferenceError):
        return False
