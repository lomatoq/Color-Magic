"""Material regions on safe working meshes. Geometry and topology are untouched."""
import json
import bpy
from .mesh_regions import segment

REGION_ATTRIBUTE='cp_material_region'


def sharp_edges(mesh):
    result={tuple(e.vertices) for e in mesh.edges if getattr(e,'use_edge_sharp',False)}
    attr=mesh.attributes.get('sharp_edge')
    if attr and attr.domain=='EDGE':
        result.update(tuple(e.vertices) for e,d in zip(mesh.edges,attr.data) if d.value)
    return result


def existing_regions(obj):
    """Adopt authored material boundaries as face labels without repainting."""
    mesh=obj.data
    used=sorted({p.material_index for p in mesh.polygons})
    if not used:return 0
    attr=mesh.attributes.get(REGION_ATTRIBUTE)
    if attr is not None:return region_count(obj)
    # Sort by area so region 1 is the dominant surface, retaining every slot.
    areas={index:0. for index in used}
    for face in mesh.polygons:areas[face.material_index]+=face.area
    order=sorted(used,key=lambda index:(-areas[index],index))
    remap={slot:index for index,slot in enumerate(order)}
    attr=mesh.attributes.new(name=REGION_ATTRIBUTE,type='INT',domain='FACE')
    attr.data.foreach_set('value',[remap[p.material_index] for p in mesh.polygons])
    mesh.update()
    return len(used)


def _similar_regions(vertices,faces,plan,seed):
    """Repeat-part evidence: area and rotation-invariant surface covariance.

    This can keep matching wheels/handles in one family. It does not identify
    semantic part names; the largest region always remains Main.
    """
    if plan.method=='ISLANDS':
        from .shape_matching import describe,compare
        import numpy as np
        vertices=np.asarray(vertices,dtype=np.float64)
        grouped=[[] for _ in range(plan.count)]
        for face,label in zip(faces,plan.labels):grouped[label].append(face)
        reference=describe(vertices,grouped[seed])
        chosen={seed}
        for index,group in enumerate(grouped):
            if index==seed:continue
            if compare(reference,describe(vertices,group))[0]:chosen.add(index)
        # Matching the main body means the entire family should stay Main.
        return set() if 0 in chosen else chosen
    from .mesh_regions import _face_stats
    stats=[_face_stats(vertices,face) for face in faces]
    signatures=[]
    grouped=[[] for _ in range(plan.count)]
    for (_,area,center),label in zip(stats,plan.labels):grouped[label].append((area,center))
    for selected in grouped:
        total=sum(a for a,c in selected)
        center=[sum(a*c[k] for a,c in selected)/max(total,1e-16) for k in range(3)]
        cov=[[sum(a*(c[i]-center[i])*(c[j]-center[j]) for a,c in selected)/max(total,1e-16)
              for j in range(3)] for i in range(3)]
        # Jacobi eigenvalues for a symmetric 3x3 covariance matrix.
        import math
        for _ in range(16):
            i,j=max(((0,1),(0,2),(1,2)),key=lambda ij:abs(cov[ij[0]][ij[1]]))
            if abs(cov[i][j])<1e-16:break
            angle=.5*math.atan2(2*cov[i][j],cov[j][j]-cov[i][i])
            co,si=math.cos(angle),math.sin(angle)
            a,b,d=cov[i][i],cov[j][j],cov[i][j]
            cov[i][i]=co*co*a-2*co*si*d+si*si*b
            cov[j][j]=si*si*a+2*co*si*d+co*co*b
            cov[i][j]=cov[j][i]=0.
            for k in range(3):
                if k in (i,j):continue
                x,y=cov[k][i],cov[k][j]
                cov[k][i]=cov[i][k]=co*x-si*y
                cov[k][j]=cov[j][k]=si*x+co*y
        values=sorted(max(0.,cov[k][k]) for k in range(3))
        norm=max(sum(values),1e-16)
        signatures.append([x/norm for x in values])
    chosen={seed}
    if plan.method not in {'ISLANDS','ISLAND_GROUPS'}:return chosen
    for index in range(1,plan.count):
        if index in plan.aggregate_regions:continue
        ratio=plan.areas[index]/max(plan.areas[seed],1e-16)
        difference=sum(abs(a-b) for a,b in zip(signatures[index],signatures[seed]))
        if .72<=ratio<=1.38 and difference<.16:
            chosen.add(index)
    # Keep the secondary family secondary even when many repeated pieces exist.
    # Never break a repeated/symmetric group merely to hit an area quota.
    return chosen


def _notes(settings,obj,message):
    if getattr(settings.studio,'guide_language','EN')=='BE':
        message={
            'Existing material zones preserved.':'Існыя зоны матэрыялаў захаваныя.',
            'Protected or explicit family assignment preserved.':'Абарона і яўнае прызначэнне сямейства захаваныя.',
            'Texture-driven / ambiguous color graph: no automatic split.':'Тэкстурны або неадназначны шэйдар: аўтападзел прапушчаны.',
            'Over 120,000 faces: automatic analysis skipped, manual face assignment remains available.':'Больш за 120 000 граняў: аўтападзел прапушчаны; можна прызначыць грані ўручную.',
            'Disconnected face islands; no geometry was separated.':'Знойдзеныя асобныя астравы граняў; геаметрыя не разразалася.',
            'Seams/sharp or concave surface boundaries.':'Зоны знойдзеныя па швах і рэзкіх або ўвагнутых межах.',
            'More geometric regions; review the proposed boundary.':'Прапанавана больш геаметрычных зон; правер іх межы.',
            'Strong narrow-neck cross-section; geometric suggestion, not semantic recognition.':'Зоны падзеленыя па вузкай перамычцы. Гэта геаметрычная прапанова.',
            'No reliable boundary found. Kept one region; select faces for a manual Accent.':'Надзейная мяжа не знойдзеная. Мадэль пакінутая цэлай; выберы грані для Accent.',
        }.get(message,message)
    text=obj.name+': '+message
    settings.studio.region_note=(settings.studio.region_note+'\n'+text).strip()


def auto_regions(obj,settings,explicit=False):
    from .transaction import protected, OWNER_KEY
    from .scene_intelligence import _copy_for_family,_new_family_material,_saved_role
    from .targets import find_material_target
    st=settings.studio
    if st.surface_regions=='OFF':return 0
    mesh=getattr(obj,'data',None)
    if obj.type!='MESH' or mesh is None:return 0
    if region_count(obj):return region_count(obj)
    # Standalone legacy Auto Setup can still run without a staged transaction;
    # the new region feature is deliberately limited to reversible copies.
    if st.stage_status!='STAGED' or not st.stage_id or mesh.get(OWNER_KEY,'')!=st.stage_id:return 0
    mats=[slot.material for slot in obj.material_slots if slot.material]
    if st.zone_policy=='PRESERVE' and obj.get('color_prime_authored_slots',False):
        return existing_regions(obj)
    if len({m.as_pointer() for m in mats})>1:
        count=existing_regions(obj)
        _notes(settings,obj,'Existing material zones preserved.');return count
    if any(protected(m,settings) or (not explicit and _saved_role(m)) for m in mats):
        _notes(settings,obj,'Protected or explicit family assignment preserved.');return 0
    source=mats[0] if mats else None
    if source is not None and source.use_nodes and not find_material_target(source,False).writable:
        _notes(settings,obj,'Texture-driven / ambiguous color graph: no automatic split.');return 0
    if len(mesh.polygons)>120000:
        _notes(settings,obj,'Over 120,000 faces: automatic analysis skipped, manual face assignment remains available.');return 0
    vertices=[tuple(obj.matrix_world @ v.co) for v in mesh.vertices]
    faces=[tuple(p.vertices) for p in mesh.polygons]
    seams=[tuple(e.vertices) for e in mesh.edges if getattr(e,'use_seam',False)]
    sharp=[tuple(e.vertices) for e in mesh.edges if getattr(e,'use_edge_sharp',False)]
    attr=mesh.attributes.get('sharp_edge')
    if attr and attr.domain=='EDGE':
        sharp.extend(tuple(e.vertices) for e,d in zip(mesh.edges,attr.data) if d.value)
    plan=segment(vertices,faces,seams,sharp,st.surface_regions,max_regions=st.region_max)
    _notes(settings,obj,plan.reason)
    if plan.count<2:return 0
    # Pick a secondary region, then include similarly shaped repeated parts.
    # Each zone retains its own slot for local corrections.
    total=sum(plan.areas)
    candidates=[i for i in range(1,plan.count) if i not in plan.aggregate_regions]
    accent=min(candidates,key=lambda i:(abs(plan.areas[i]/total-.22),i)) if candidates else None
    accents=_similar_regions(vertices,faces,plan,accent) if accent is not None else set()
    attribute=mesh.attributes.get(REGION_ATTRIBUTE)
    if attribute is not None and (attribute.domain!='FACE' or attribute.data_type!='INT'):
        _notes(settings,obj,'Incompatible region attribute: kept authored data unchanged.');return 0
    copies=[]
    for i in range(plan.count):
        family='ACCENT' if i in accents else 'MAIN'
        name='{} Zone {:02d}'.format(obj.name,i+1)
        material=(_copy_for_family(source,family,name,plan.evidence,plan.reason) if source else
                  _new_family_material(settings,family,name,plan.evidence,plan.reason))
        material[OWNER_KEY]=st.stage_id;material['cp_material_region']=i
        copies.append(material)
    mesh.materials.clear()
    for mat in copies:mesh.materials.append(mat)
    for slot,mat in zip(obj.material_slots,copies):slot.link='DATA';slot.material=mat
    for p,label in zip(mesh.polygons,plan.labels):p.material_index=label
    attribute=mesh.attributes.get(REGION_ATTRIBUTE)
    if attribute is None:attribute=mesh.attributes.new(name=REGION_ATTRIBUTE,type='INT',domain='FACE')
    attribute.data.foreach_set('value',plan.labels)
    mesh.update()
    obj['color_prime_region_summary']=json.dumps({'count':plan.count,'method':plan.method,'reason':plan.reason,'aggregate_regions':plan.aggregate_regions})
    obj['color_prime_geometry_split']=True
    return plan.count


def region_count(obj):
    if obj is None or getattr(obj,'type','')!='MESH':return 0
    try:
        if getattr(obj,'mode','OBJECT')=='EDIT':
            import bmesh
            bm=bmesh.from_edit_mesh(obj.data)
            layer=bm.faces.layers.int.get(REGION_ATTRIBUTE)
            return 1+max((face[layer] for face in bm.faces),default=-1) if layer is not None else 0
        attr=obj.data.attributes.get(REGION_ATTRIBUTE)
        if attr is None:return 0
        return 1+max((v.value for v in attr.data),default=-1)
    except (AttributeError,ReferenceError,ValueError):return 0


def assign_faces(scene,settings,obj,face_indices,family):
    """Called in Object Mode; caller syncs BMesh beforehand for Edit Mode UI."""
    from .transaction import OWNER_KEY,hard_protected
    from .targets import find_material_target
    from .scene_intelligence import _new_family_material
    from .runtime import restore_preview
    if settings.studio.stage_status!='STAGED':raise ValueError('Prepare the model first; originals must be retained.')
    mesh=obj.data
    if mesh.get(OWNER_KEY,'')!=settings.studio.stage_id:raise ValueError('This mesh is outside the current safe setup.')
    faces=[mesh.polygons[i] for i in sorted(set(face_indices)) if 0<=i<len(mesh.polygons)]
    if not faces:raise ValueError('Select faces, or choose an existing region.')
    restore_preview(scene)
    if settings.preview_active:raise RuntimeError('Restore the previous preview before changing face roles.')
    allocations={};old_count=len(mesh.materials);old_indices={f.index:f.material_index for f in faces};created=[]
    try:
        for face in faces:
            old=face.material_index
            if old in allocations:continue
            source=obj.material_slots[old].material if old<len(obj.material_slots) else None
            if source is not None and hard_protected(source,settings):allocations[old]=None;continue
            if source is not None and family!='FIXED' and not find_material_target(source,False).writable:
                allocations[old]=None;continue
            mat=source.copy() if source else _new_family_material(settings,'MAIN',obj.name+' Faces')
            created.append(mat);mat[OWNER_KEY]=settings.studio.stage_id
            mat['color_prime_family']=family;mat['color_prime_family_source']='MANUAL';mat['color_prime_locked']=True
            from .family_links import connect,disconnect
            if family in {'MAIN','ACCENT'}:connect(mat,settings,family)
            else:disconnect(mat)
            from .material_names import name_material
            name_material(mat,family)
            mesh.materials.append(mat);allocations[old]=len(mesh.materials)-1
        changed=0
        for face in faces:
            index=allocations.get(face.material_index)
            if index is not None:face.material_index=index;changed+=1
        if not changed:raise ValueError('All selected faces use protected or unsupported materials; nothing changed.')
        mesh.update()
        return changed
    except Exception:
        for index,value in old_indices.items():mesh.polygons[index].material_index=value
        while len(mesh.materials)>old_count:mesh.materials.pop(index=len(mesh.materials)-1)
        for mat in created:
            if mat.users==0:bpy.data.materials.remove(mat)
        raise
