"""Explicit shader choice and native per-child tint controls."""
import bpy
from bpy.props import EnumProperty,StringProperty
from .studio_ops import idle
from .family_links import color_input,shared_socket,parent,family_node,color_source

SHADERS=(('PRINCIPLED','Principled BSDF',''),('DIFFUSE','Diffuse BSDF',''),('EMISSION','Emission',''))


def local_endpoint(mat):
    source=color_source(mat);target=color_input(mat)
    if target is not None and target.id_data!=mat.node_tree and source and source.group_path:
        from .color_graph import localize_source
        node=family_node(mat);group=node.node_tree if node else None
        localize_source(mat,source)
        if group:family_node(mat).node_tree=group
    return source


def change_shader(mat,settings,family,kind):
    source=local_endpoint(mat)
    target=color_input(mat)
    if target is None:raise ValueError('Select a color input for this material first.')
    tree=target.id_data;old=target.node
    node_type={'PRINCIPLED':'ShaderNodeBsdfPrincipled','DIFFUSE':'ShaderNodeBsdfDiffuse','EMISSION':'ShaderNodeEmission'}[kind]
    if old.bl_idname==node_type:return
    destinations=[l.to_socket for output in old.outputs if output.type=='SHADER' for l in output.links]
    if not destinations:raise ValueError('Selected shader is not connected to a surface.')
    shader=tree.nodes.new(node_type);shader.location=old.location
    dest=shader.inputs.get('Base Color' if kind=='PRINCIPLED' else 'Color')
    dest.default_value=target.default_value
    if target.is_linked:tree.links.new(target.links[0].from_socket,dest)
    for name in ('Roughness','Normal'):
        source_input=old.inputs.get(name);value=shader.inputs.get(name)
        if source_input is not None and value is not None:
            if source_input.is_linked:tree.links.new(source_input.links[0].from_socket,value)
            elif hasattr(source_input,'default_value'):value.default_value=source_input.default_value
    for socket in destinations:tree.links.new(shader.outputs[0],socket)
    # Keep the old authored shader for later editing, but explicitly bind the new one.
    mat['cp_color_node']=shader.name;mat['cp_color_socket']=dest.name
    import json
    from .shader_endpoints import endpoints
    # The endpoint before the switch determines the shader's containing tree.
    path=[];walk=mat.node_tree
    if source:
        for name in source.group_path:
            if walk==tree:break
            path.append(name);walk=walk.nodes[name].node_tree
    mat['cp_color_path']=json.dumps(path)
    mat['cp_shader_kind']=kind


def tint_node(mat,create=False):
    if not mat or not mat.node_tree:return None
    target=color_input(mat)
    node=target.id_data.nodes.get('Family Shade') if target else None
    if node and node.get('cp_family_shade',False):return node
    if not create:return None
    local_endpoint(mat);target=color_input(mat)
    if target is None:raise ValueError('Choose the material color input first.')
    tree=target.id_data;shade=tree.nodes.new('ShaderNodeMixRGB');shade.name='Family Shade'
    shade['cp_family_shade']=True;shade.blend_type='MIX';shade.inputs[0].default_value=0.
    shade.inputs[2].default_value=(1,1,1,1)
    if target.is_linked:tree.links.new(target.links[0].from_socket,shade.inputs[1])
    else:shade.inputs[1].default_value=target.default_value
    tree.links.new(shade.outputs[0],target)
    return shade


class COLORPRIME_OT_material_controls(bpy.types.Operator):
    bl_idname='color_prime.material_controls';bl_label='Настроить материал';bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=tuple((x,x,'') for x in ('SHADER','TARGET','LIGHT','DARK','RESET','TINT')))
    shader:EnumProperty(name='Shader',items=SHADERS,default='PRINCIPLED')
    node_name:StringProperty(name='Нод шейдера / группы')
    socket_name:StringProperty(name='Цветовой вход',default='Color')
    @classmethod
    def poll(cls,context):return idle(context) and bool(context.scene.color_prime.children)
    def invoke(self,context,event):
        if self.action in {'SHADER','TARGET'}:return context.window_manager.invoke_props_dialog(self,width=400)
        return self.execute(context)
    def draw(self,context):
        if self.action=='SHADER':self.layout.prop(self,'shader')
        else:
            s=context.scene.color_prime;mat=s.children[s.child_index].material
            self.layout.prop_search(self,'node_name',mat.node_tree,'nodes')
            node=mat.node_tree.nodes.get(self.node_name)
            if node:self.layout.prop_search(self,'socket_name',node,'inputs')
            else:self.layout.prop(self,'socket_name')
    def execute(self,context):
        s=context.scene.color_prime
        try:
            child=s.children[s.child_index];mat=child.material
            if mat is None:raise ValueError('Select a material.')
            if self.action=='SHADER':change_shader(mat,s,child.family,self.shader)
            elif self.action=='TARGET':
                node=mat.node_tree.nodes.get(self.node_name);sock=node.inputs.get(self.socket_name) if node else None
                if sock is None or sock.type!='RGBA':raise ValueError('Choose an RGBA input on the shader or group.')
                mat['cp_color_node']=node.name;mat['cp_color_socket']=sock.name
                mat['cp_color_path']='[]'
                from .family_links import connect
                connect(mat,s,child.family)
            else:
                node=tint_node(mat,True)
                node.inputs[0].default_value=.18 if self.action=='LIGHT' else .22 if self.action=='DARK' else 0.
                node.inputs[2].default_value=(0,0,0,1) if self.action=='DARK' else (1,1,1,1)
                mat['color_prime_child_profile']=self.action if self.action in {'LIGHT','DARK'} else 'SAME'
            from .discovery import scan_material_bindings
            scan_material_bindings(context.scene,s,False,local_only=True)
            from .material_names import update_surface_names
            update_surface_names(s);s.last_error='';return {'FINISHED'}
        except Exception as exc:
            s.last_error=str(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}


def draw(layout,context,s,mat):
    row=layout.row(align=True)
    row.operator('color_prime.material_controls',text='Тип шейдера…').action='SHADER'
    row.operator('color_prime.material_controls',text='Свой цветовой вход…').action='TARGET'
    target=color_input(mat)
    if target:
        layout.label(text=target.node.name+' → '+target.name)
        if target.node.type=='EMISSION':layout.prop(target.node.inputs['Strength'],'default_value',text='Сила свечения')
        elif target.node.type=='BSDF_DIFFUSE':layout.prop(target.node.inputs['Roughness'],'default_value',text='Шероховатость')
    row=layout.row(align=True)
    for action,label in (('LIGHT','Светлее'),('DARK','Темнее'),('RESET','Сброс')):row.operator('color_prime.material_controls',text=label).action=action
    node=tint_node(mat)
    if node:
        layout.prop(node.inputs[2],'default_value',text='Подмешать цвет')
        layout.prop(node.inputs[0],'default_value',text='Смешивание',slider=True)
    else:layout.operator('color_prime.material_controls',text='Добавить подмешивание цвета').action='TINT'


class COLORPRIME_OT_adoption_target(bpy.types.Operator):
    bl_idname='color_prime.adoption_target';bl_label='Цветовой вход материала';bl_options={'REGISTER','UNDO'}
    material_name:StringProperty()
    node_name:StringProperty(name='Нод шейдера / группы')
    socket_name:StringProperty(name='Цветовой вход',default='Color')
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self,width=420)
    def draw(self,context):
        mat=bpy.data.materials.get(self.material_name)
        if mat and mat.node_tree:
            self.layout.prop_search(self,'node_name',mat.node_tree,'nodes')
            node=mat.node_tree.nodes.get(self.node_name)
            if node:self.layout.prop_search(self,'socket_name',node,'inputs')
    def execute(self,context):
        s=context.scene.color_prime;mat=bpy.data.materials.get(self.material_name)
        if not mat or not any(b.material==mat for b in s.bindings):return {'CANCELLED'}
        node=mat.node_tree.nodes.get(self.node_name);socket=node.inputs.get(self.socket_name) if node else None
        if socket is None or socket.type!='RGBA':self.report({'ERROR'},'Choose an RGBA input.');return {'CANCELLED'}
        from .color_graph import trace_color_socket
        if len(trace_color_socket(socket,mat.node_tree))!=1:
            self.report({'ERROR'},'Choose an unlinked color input or an unambiguous color source.');return {'CANCELLED'}
        mat['cp_color_node']=node.name;mat['cp_color_socket']=socket.name;mat['cp_color_path']='[]'
        from .discovery import scan_material_bindings
        scan_material_bindings(context.scene,s,False)
        return {'FINISHED'}


CLASSES=(COLORPRIME_OT_material_controls,COLORPRIME_OT_adoption_target)
