"""Bounded, deterministic surface matching, invariant to uniform scale/reflection.

Descriptors only reject candidates; aligned bidirectional distances confirm a
match. No geometry is changed. numpy ships with the supported Blender releases.
"""
from dataclasses import dataclass
from itertools import product, permutations
import numpy as np


@dataclass
class Shape:
    points: object
    spectrum: object
    radial: object
    compactness: float
    area: float
    center: object
    radius: float
    exact_key: object=None


def _radical(index,base):
    value=0.; factor=1./base
    while index:
        value+=(index%base)*factor;index//=base;factor/=base
    return value


def describe(vertices,faces,samples=512):
    vertices=np.asarray(vertices,dtype=np.float64)
    triangles=[(face[0],face[k],face[k+1]) for face in faces for k in range(1,len(face)-1)]
    if not triangles or len(triangles)>240000:return None
    tri=vertices[np.asarray(triangles,dtype=np.int64)]
    if not np.isfinite(tri).all():return None
    # Subtract an anchor before covariance for large translated scenes.
    anchor=tri[0,0].copy();tri=tri-anchor
    area=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)*.5
    valid=area>max(float(area.max())*1e-14,1e-30)
    tri,area=tri[valid],area[valid]
    total=float(area.sum())
    if total<=0.:return None
    means=tri.mean(axis=1);center=np.sum(means*area[:,None],axis=0)/total
    centered=tri-center
    summed=centered.sum(axis=1)
    second=(np.einsum('tvi,tvj->tij',centered,centered)+np.einsum('ti,tj->tij',summed,summed))/12.
    covariance=np.sum(second*area[:,None,None],axis=0)/total
    values,axes=np.linalg.eigh(covariance)
    order=np.argsort(values)[::-1];values=np.maximum(values[order],0.);axes=axes[:,order]
    radius=float(np.sqrt(values.sum()))
    if radius<1e-15:return None
    # Deterministic low-discrepancy sampling of area, then triangle barycentrics.
    count=max(64,min(512,int(samples)))
    indices=np.searchsorted(np.cumsum(area),(np.arange(count)+.5)*total/count)
    u=np.sqrt([_radical(i+1,2) for i in range(count)])
    v=np.array([_radical(i+1,3) for i in range(count)])
    picked=tri[indices]
    points=(1-u[:,None])*picked[:,0]+(u*(1-v))[:,None]*picked[:,1]+(u*v)[:,None]*picked[:,2]
    points=(points-center)@axes/radius
    radial=np.quantile(np.linalg.norm(points,axis=1),np.linspace(.1,.9,9))
    shape=Shape(points,np.sqrt(values)/radius,radial,total/(radius*radius),total,center+anchor,radius)
    # Exact small-part copies have identical normalized surface vertices. This
    # shortcut avoids repeatedly fitting the same bolts/grips against each other.
    unique=np.unique(tri.reshape(-1,3),axis=0)
    if len(unique)<=512:
        local=(unique-center)@axes/radius
        keys=[]
        for perm in permutations(range(3)):
            if np.max(np.abs(shape.spectrum-shape.spectrum[list(perm)]))>1e-5:continue
            for signs in product((-1.,1.),repeat=3):
                rounded=np.round(local[:,perm]*np.asarray(signs),5)
                rounded[rounded==0]=0
                keys.append(rounded[np.lexsort(rounded.T[::-1])].tobytes())
        shape.exact_key=min(keys) if keys else None
    return shape


def compare(a,b):
    """Return (matched, normalized mean residual); no absolute size gate."""
    if a is None or b is None:return False,float('inf')
    if np.max(np.abs(a.spectrum-b.spectrum))>.085:return False,float('inf')
    if np.mean(np.abs(a.radial-b.radial))>.08:return False,float('inf')
    if abs(np.log(max(a.compactness,1e-12)/max(b.compactness,1e-12)))>.24:return False,float('inf')
    if a.exact_key is not None and a.exact_key==b.exact_key:return True,0.
    try:from mathutils.kdtree import KDTree
    except ImportError:KDTree=None
    def tree(points):
        if KDTree is None:return points
        result=KDTree(len(points))
        for i,p in enumerate(points):result.insert(p,i)
        result.balance();return result
    if not hasattr(a,'_tree'):a._tree=tree(a.points)
    if not hasattr(b,'_tree'):b._tree=tree(b.points)
    a_tree=a._tree
    def distances(q,inverse=None):
        if KDTree is None:
            delta=a.points[:,None,:]-q[None,:,:]
            d2=np.einsum('ijk,ijk->ij',delta,delta)
            return np.sqrt(np.concatenate((d2.min(axis=0),d2.min(axis=1))))
        q_tree=b._tree if inverse is not None else tree(q)
        return np.asarray([a_tree.find(p)[2] for p in q]+[q_tree.find(p)[2] for p in (inverse if inverse is not None else a.points)])
    best=float('inf');tail=float('inf');best_points=None
    # Nearly equal eigenvalues make axes interchangeable. Test only compatible
    # permutations; sign alternatives also cover mirror copies.
    for perm in permutations(range(3)):
        if np.max(np.abs(a.spectrum-b.spectrum[list(perm)]))>.055:continue
        target=b.points[:,perm]
        for signs in product((-1.,1.),repeat=3):
            q=target*np.asarray(signs)
            d=distances(q,(a.points*np.asarray(signs))[:,np.argsort(perm)])
            score=float(d.mean())
            if score<best:best=score;tail=float(np.quantile(d,.9));best_points=q
            if best<=.075 and tail<=.15:return True,best
            if best<1e-7:return True,best
    # PCA is unstable around repeated eigenvalues (round grips / wheels).
    # Bounded rigid ICP refines the best candidate; no scale or nonrigid fit.
    if best_points is not None and best<.18:
        q=best_points.copy()
        for _ in range(12):
            if KDTree is None:
                delta=q[:,None,:]-a.points[None,:,:]
                target=a.points[np.argmin(np.einsum('ijk,ijk->ij',delta,delta),axis=1)]
            else:target=a.points[[a_tree.find(p)[1] for p in q]]
            source_center=q.mean(axis=0);target_center=target.mean(axis=0)
            u,_,vt=np.linalg.svd((q-source_center).T@(target-target_center))
            rotation=u@vt
            q=(q-source_center)@rotation+target_center
            d=distances(q)
            score=float(d.mean())
            if score<best:best=score;tail=float(np.quantile(d,.9))
            if best<=.075 and tail<=.15:return True,best
    return best<=.075 and tail<=.15,best


def group_shapes(shapes,max_comparisons=4096):
    """Complete-link groups avoid A~B~C chains joining incompatible A and C."""
    groups=[]; cache={}; comparisons=0
    for index,shape in enumerate(shapes):
        placed=False
        if shape is not None:
            # Try the closest descriptors first. Area-order scanning exhausts
            # the budget on unrelated parts before reaching small mirror pairs.
            candidates=sorted(groups,key=lambda g:float(np.sum(np.abs(shapes[g[0]].spectrum-shape.spectrum))+np.mean(np.abs(shapes[g[0]].radial-shape.radial))) if shapes[g[0]] is not None else float('inf'))
            for group in candidates:
                fits=True
                for other in group:
                    candidate=shapes[other]
                    if candidate is None or np.max(np.abs(candidate.spectrum-shape.spectrum))>.085 or np.mean(np.abs(candidate.radial-shape.radial))>.08 or abs(np.log(max(candidate.compactness,1e-12)/max(shape.compactness,1e-12)))>.24:
                        fits=False;break
                    key=(other,index)
                    if key not in cache:
                        if comparisons>=max_comparisons:
                            fits=False;break
                        cache[key]=compare(shapes[other],shape);comparisons+=1
                    if not cache[key][0]:fits=False;break
                if fits:
                    group.append(index);placed=True;break
        if not placed:groups.append([index])
    return groups,{'comparisons':comparisons,'limit_reached':comparisons>=max_comparisons,
                   'matches':[{'a':a,'b':b,'residual':round(value[1],6)} for (a,b),value in cache.items() if value[0]]}
