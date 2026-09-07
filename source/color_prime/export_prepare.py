"""Resolve current model colors for export without rebuilding zones or surfaces."""
def bindings(scene,s):
    from .appearance_workspace import meshes,model_colors
    from .targets import find_material_target,apply_spec_to_binding
    from .utils import data_block_identity
    old={b.material:(b.family,b.enabled,b.manual_family,b.locked,b.inheritance_mode) for b in s.bindings if b.material}
    plans={}
    for item in s.icon_sets:
        if not item.enabled or (s.appearance_mode=='MODEL' and item.name==s.appearance_source):continue
        colors,roles=model_colors(meshes(item,scene),s)
        for mat,role in roles.items():
            previous=old.get(mat)
            if previous and (previous[2] or previous[3]):role=previous[0]
            spec=find_material_target(mat,localize_legacy=False)
            plans.setdefault(mat,(role,spec,colors))
    prior=s.suppress_callbacks;s.suppress_callbacks=True
    try:
        s.bindings.clear()
        for mat,(role,spec,colors) in plans.items():
            b=s.bindings.add();b.material=mat;b.material_identity=data_block_identity(mat)
            apply_spec_to_binding(b,spec);b.family=role
            previous=old.get(mat)
            b.enabled=previous[1] if previous else True
            b.manual_family=previous[2] if previous else False;b.locked=previous[3] if previous else False
            b.inheritance_mode=previous[4] if previous else 'OKLCH'
            b.captured_material_color=spec.color;b.captured_family_color=colors.get(role,spec.color)
            b.confidence=1.;b.auto_reason='Current model material family'
    finally:s.suppress_callbacks=prior
