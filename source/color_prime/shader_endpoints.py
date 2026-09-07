"""Resolve active surface color inputs across nested shader groups, read-only."""
import json
from .color_graph import _match


def endpoints(material):
    if material is None or not material.use_nodes or material.node_tree is None:return []
    tree=material.node_tree
    if material.get('cp_color_node',''):
        path=();callers=()
        for name in json.loads(material.get('cp_color_path','[]')):
            group=tree.nodes.get(name)
            if group is None or group.type!='GROUP' or not group.node_tree:return []
            callers+=((tree,path,group),);path+=(name,);tree=group.node_tree
        node=tree.nodes.get(material['cp_color_node'])
        socket=node.inputs.get(material.get('cp_color_socket','Color')) if node else None
        return [(socket,tree,path,callers)] if socket is not None else []
    def visit(socket,tree,path=(),callers=(),budget=80):
        if socket is None or budget<=0:return []
        if not socket.is_output:
            return [end for link in socket.links for end in visit(link.from_socket,tree,path,callers,budget-1)]
        node=socket.node
        if node.type in {'BSDF_PRINCIPLED','BSDF_DIFFUSE','EMISSION'}:
            color=node.inputs.get('Base Color' if node.type=='BSDF_PRINCIPLED' else 'Color')
            return [(color,tree,path,callers)] if color else []
        if node.type=='GROUP' and node.node_tree:
            outputs=[n for n in node.node_tree.nodes if n.type=='GROUP_OUTPUT']
            active=[n for n in outputs if n.is_active_output];outputs=active or outputs
            if len(outputs)!=1:return []
            inner=_match(outputs[0].inputs,socket,list(node.outputs).index(socket))
            return visit(inner,node.node_tree,path+(node.name,),callers+((tree,path,node),),budget-1)
        if node.type=='GROUP_INPUT' and callers:
            parent,parent_path,group=callers[-1]
            return visit(_match(group.inputs,socket,list(node.outputs).index(socket)),parent,parent_path,callers[:-1],budget-1)
        if node.type=='REROUTE':return visit(node.inputs[0],tree,path,callers,budget-1)
        if node.type in {'MIX_SHADER','ADD_SHADER'}:
            return [end for inp in node.inputs if inp.type=='SHADER' for end in visit(inp,tree,path,callers,budget-1)]
        return []
    outputs=[n for n in tree.nodes if n.type=='OUTPUT_MATERIAL']
    active=[n for n in outputs if n.is_active_output];outputs=active or outputs
    return visit(outputs[0].inputs.get('Surface'),tree) if len(outputs)==1 else []
