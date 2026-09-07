"""Conservative surface-region proposals, not semantic/AI recognition.

No remeshing, vertex movement, external libraries or topology edits. A region
is a face label. Explicit boundaries / concave creases are preferred; a strong
cross-section bottleneck is a fallback for smooth, connected shapes. A smooth
sphere stays one region rather than receiving an arbitrary decorative cut.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
import math


def sub(a,b): return (a[0]-b[0],a[1]-b[1],a[2]-b[2])
def dot(a,b): return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def cross(a,b): return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def length(a): return math.sqrt(max(0.,dot(a,a)))
def normalized(a):
    s=length(a)
    return tuple(v/s for v in a) if s>1e-20 else (0.,0.,0.)


@dataclass(frozen=True)
class RegionPlan:
    labels: tuple
    areas: tuple
    method: str
    reason: str
    evidence: float
    aggregate_regions: tuple=()

    @property
    def count(self): return len(self.areas)


def _face_stats(verts,face):
    points=[verts[i] for i in face]
    center=tuple(sum(p[k] for p in points)/len(points) for k in range(3))
    vector=[0.,0.,0.]
    for p,q in zip(points,points[1:]+points[:1]):
        vector[0]+=(p[1]-q[1])*(p[2]+q[2])
        vector[1]+=(p[2]-q[2])*(p[0]+q[0])
        vector[2]+=(p[0]-q[0])*(p[1]+q[1])
    return normalized(vector),max(length(vector)*.5,1e-16),center


def _components(adjacency,blocked=None,side=None):
    n=len(adjacency);labels=[-1]*n;count=0
    for start in range(n):
        if labels[start]>=0:continue
        labels[start]=count;queue=deque([start])
        while queue:
            f=queue.popleft()
            for g,edge in adjacency[f]:
                if labels[g]>=0 or (blocked and edge in blocked):continue
                if side is not None and side[f]!=side[g]:continue
                labels[g]=count;queue.append(g)
        count+=1
    return labels


def _normalize(labels,areas):
    totals=defaultdict(float)
    for lab,area in zip(labels,areas):totals[lab]+=area
    order=sorted(totals,key=lambda k:(-totals[k],k))
    lookup={old:new for new,old in enumerate(order)}
    return tuple(lookup[x] for x in labels),tuple(totals[x] for x in order)


def _merge_small(labels,areas,adjacency,limit,min_fraction):
    """Region-graph agglomeration; avoid a quadratic full-face rescan per merge."""
    import heapq
    totals=defaultdict(float)
    links=defaultdict(lambda:defaultdict(float))
    for lab,area in zip(labels,areas):totals[lab]+=area
    for f,neighbors in enumerate(adjacency):
        a=labels[f]
        for g,_ in neighbors:
            b=labels[g]
            if a!=b:links[a][b]+=math.sqrt(areas[g])
    parent={x:x for x in totals};active=set(totals);total=sum(areas)
    heap=[(area,lab) for lab,area in totals.items()];heapq.heapify(heap)
    while heap and len(active)>1:
        area,small=heapq.heappop(heap)
        if small not in active or area!=totals[small]:continue
        if len(active)<=limit and area>=total*min_fraction:break
        neighbors={n:w for n,w in links[small].items() if n in active and n!=small}
        if not neighbors:continue
        target=max(neighbors,key=lambda n:(neighbors[n],totals[n],-n))
        active.remove(small);parent[small]=target;totals[target]+=totals[small]
        for other,weight in neighbors.items():
            if other==target:continue
            links[target][other]+=weight
            links[other][target]+=links[other].pop(small,0.)
        links[target].pop(small,None);links.pop(small,None)
        heapq.heappush(heap,(totals[target],target))
    def find(x):
        root=x
        while parent[root]!=root:root=parent[root]
        while parent[x]!=x:
            nxt=parent[x];parent[x]=root;x=nxt
        return root
    return _normalize([find(x) for x in labels],areas)


def _hull_area(points):
    pts=sorted(set((round(x,10),round(y,10)) for x,y in points))
    if len(pts)<3:return 0.
    def turn(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lower=[]
    for p in pts:
        while len(lower)>1 and turn(lower[-2],lower[-1],p)<=0:lower.pop()
        lower.append(p)
    upper=[]
    for p in reversed(pts):
        while len(upper)>1 and turn(upper[-2],upper[-1],p)<=0:upper.pop()
        upper.append(p)
    hull=lower[:-1]+upper[:-1]
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(hull,hull[1:]+hull[:1])))*.5


def _principal_axis(points):
    center=tuple(sum(p[k] for p in points)/len(points) for k in range(3))
    cov=[[0.]*3 for _ in range(3)]
    for p in points:
        d=sub(p,center)
        for i in range(3):
            for j in range(3):cov[i][j]+=d[i]*d[j]
    axis=(1.,.37,.13)
    for _ in range(20):
        candidate=normalized(tuple(sum(cov[i][j]*axis[j] for j in range(3)) for i in range(3)))
        if length(candidate)<.5:break
        axis=candidate
    return normalized(axis)


def _neck(verts,edges,centers,areas,adjacency):
    """Plane cross-section valley test. Not SDF, not a semantic classifier."""
    best=None;total=sum(areas)
    axes=[_principal_axis(verts),(1.,0.,0.),(0.,1.,0.),(0.,0.,1.)]
    for axis in axes:
        if length(axis)<.5:continue
        helper=(1.,0.,0.) if abs(axis[0])<.8 else (0.,1.,0.)
        u=normalized(cross(axis,helper));v=cross(axis,u)
        ts=[dot(p,axis) for p in verts];lo,hi=min(ts),max(ts)
        if hi-lo<1e-7:continue
        sections=[];planes=[lo+(hi-lo)*(i+1)/25. for i in range(24)]
        # Pre-bucket edge intersections. Long edges cross many planes, but
        # ordinary tessellated meshes touch only a few bins per edge.
        buckets=[[] for _ in planes]
        for a,b in edges:
            ta,tb=ts[a],ts[b]
            if abs(tb-ta)<1e-12:continue
            start=max(0,math.ceil((min(ta,tb)-lo)/(hi-lo)*25-1))
            end=min(23,math.floor((max(ta,tb)-lo)/(hi-lo)*25-1))
            for i in range(start,end+1):
                t=(planes[i]-ta)/(tb-ta)
                if -1e-9<=t<=1.+1e-9:
                    p=tuple(verts[a][k]+t*(verts[b][k]-verts[a][k]) for k in range(3))
                    buckets[i].append((dot(p,u),dot(p,v)))
        sections=[_hull_area(p) for p in buckets]
        for i in range(4,20):
            left=max(sections[:i-1],default=0.);right=max(sections[i+2:],default=0.)
            shoulder=min(left,right)
            if shoulder<=1e-12:continue
            valley=sum(sections[max(0,i-1):i+2])/3.
            ratio=valley/shoulder
            if ratio>.46 or sections[i]<=shoulder*.008:continue
            side=[dot(c,axis)>planes[i] for c in centers]
            share=sum(a for a,s in zip(areas,side) if s)/total
            if not .12<=share<=.88:continue
            labels=_components(adjacency,side=side)
            if len(set(labels))!=2:continue
            quality=(1.-ratio)*(.8+.2*(1.-abs(.5-share)*2.))
            if best is None or quality>best[0]:best=(quality,labels)
    return None if best is None else best[1]


def segment(vertices,faces,seams=(),sharp=(),mode='AUTO',max_regions=6,min_fraction=.018,max_faces=120000):
    if mode=='OFF':return RegionPlan((),(),'OFF','Automatic surface regions are disabled.',0.)
    if not faces:return RegionPlan((),(),'EMPTY','The mesh has no faces.',0.)
    if len(faces)>max_faces:
        return RegionPlan((),(),'LIMIT','Mesh exceeds the safety face limit; existing assignments are preserved.',0.)
    verts=[tuple(map(float,p)) for p in vertices]
    if not verts or any(len(v)!=3 or not all(math.isfinite(x) for x in v) for v in verts):
        raise ValueError('Mesh has invalid vertex coordinates.')
    faces=[tuple(map(int,f)) for f in faces]
    if any(len(f)<3 or min(f)<0 or max(f)>=len(verts) for f in faces):
        raise ValueError('Mesh contains invalid face indices.')
    # Normalize around the bounds center: thresholds/sections are scale invariant.
    lows=[min(p[k] for p in verts) for k in range(3)];highs=[max(p[k] for p in verts) for k in range(3)]
    span=max(b-a for a,b in zip(lows,highs))
    if span<1e-12:return RegionPlan(tuple(0 for _ in faces),(0.,),'DEGENERATE','Zero-size surface.',0.)
    mid=tuple((a+b)*.5 for a,b in zip(lows,highs))
    verts=[tuple((p[k]-mid[k])/span for k in range(3)) for p in verts]
    stats=[_face_stats(verts,f) for f in faces];normals,areas,centers=zip(*stats)
    owners=defaultdict(list)
    for fi,face in enumerate(faces):
        for a,b in zip(face,face[1:]+face[:1]):
            if a!=b:owners[tuple(sorted((a,b)))].append(fi)
    adjacency=[[] for _ in faces]
    for edge,fs in owners.items():
        if len(fs)==2:
            a,b=fs;adjacency[a].append((b,edge));adjacency[b].append((a,edge))
    islands=_components(adjacency)
    if len(set(islands))>1:
        labels,totals=_normalize(islands,areas)
        # Existing disconnected parts are real boundaries, not generated cuts.
        # A soft limit on inferred cuts must never join unrelated islands or
        # split a mirrored pair at an area-ranked cutoff.
        return RegionPlan(labels,totals,'ISLANDS','Disconnected face islands; no geometry was separated.',.9)
    marked={tuple(sorted(e)) for e in seams}|{tuple(sorted(e)) for e in sharp}
    closed=all(len(x)==2 for x in owners.values())
    volume=sum(dot(n,c)*a for n,c,a in zip(normals,centers,areas))/3.
    orient=-1. if closed and volume<0 else 1.
    blocked=set(marked)
    cosine=math.cos(math.radians(27. if mode=='AUTO' else 45.))
    for edge,fs in owners.items():
        if len(fs)!=2:continue
        a,b=fs;angle_dot=max(-1.,min(1.,dot(normals[a],normals[b])))
        if angle_dot>=cosine:continue
        concave=orient*dot(sub(centers[b],centers[a]),normals[a])>1e-8
        if concave or mode=='DETAIL':blocked.add(edge)
    labels=_components(adjacency,blocked)
    if len(set(labels))>1:
        labels,totals=_merge_small(labels,areas,adjacency,max_regions,min_fraction)
        if 1<len(totals)<=max_regions:
            return RegionPlan(labels,totals,'BOUNDARIES','Seams/sharp or concave surface boundaries.' if mode=='AUTO' else 'More geometric regions; review the proposed boundary.',.82 if marked else .66)
    if closed:
        neck=_neck(verts,list(owners),centers,areas,adjacency)
        if neck is not None:
            labels,totals=_normalize(neck,areas)
            return RegionPlan(labels,totals,'NECK','Strong narrow-neck cross-section; geometric suggestion, not semantic recognition.',.70)
    return RegionPlan(tuple(0 for _ in faces),(sum(areas),),'NO_BOUNDARY',
                      'No reliable boundary found. Kept one region; select faces for a manual Accent.',.0)
