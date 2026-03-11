"""Convert downloaded GLB tiles to a single FBX file using Blender."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


BLENDER_SCRIPT = """
import bpy
import sys
import os
import math
from pathlib import Path
from mathutils import Vector, Matrix

# Get arguments after "--"
argv = sys.argv
argv = argv[argv.index("--") + 1:]
input_dir = Path(argv[0])
output_file = Path(argv[1])
merge = argv[2] == "True"
embed_textures = argv[3] == "True"
jpeg_textures = argv[4] == "True" if len(argv) > 4 else False
jpeg_quality = int(argv[5]) if len(argv) > 5 else 90
atlas_texture = argv[6] == "True" if len(argv) > 6 else False
atlas_size = int(argv[7]) if len(argv) > 7 else 4096

# Clear default scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Also remove any orphaned data
for block in bpy.data.meshes:
    if block.users == 0:
        bpy.data.meshes.remove(block)
for block in bpy.data.materials:
    if block.users == 0:
        bpy.data.materials.remove(block)
for block in bpy.data.images:
    if block.users == 0:
        bpy.data.images.remove(block)

# Import all GLB files
glb_files = sorted(input_dir.glob("*.glb"))
print(f"Found {len(glb_files)} GLB files")

for glb_path in glb_files:
    try:
        bpy.ops.import_scene.gltf(filepath=str(glb_path))
        print(f"Imported: {glb_path.name}")
    except Exception as e:
        print(f"Warning: Failed to import {glb_path.name}: {e}")

# Select all mesh objects
bpy.ops.object.select_all(action='DESELECT')
mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']

if not mesh_objects:
    print("No mesh objects found!")
    sys.exit(1)

print(f"Loaded {len(mesh_objects)} mesh objects")

# Count materials and textures
all_materials = set()
all_images = set()
for obj in mesh_objects:
    for slot in obj.material_slots:
        if slot.material:
            all_materials.add(slot.material.name)
            # Check for textures in the material
            if slot.material.use_nodes:
                for node in slot.material.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        all_images.add(node.image.name)

print(f"Found {len(all_materials)} materials and {len(all_images)} textures")

# Fix material textures - convert to standard Principled BSDF for FBX export
# Google 3D Tiles use Emission-based materials which FBX doesn't handle well
print("Converting materials to Principled BSDF for FBX compatibility...")
for mat in bpy.data.materials:
    if mat.use_nodes:
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        
        # Find texture image nodes and output node
        tex_images = []
        output_node = None
        
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                tex_images.append(node)
            elif node.type == 'OUTPUT_MATERIAL':
                output_node = node
        
        if tex_images and output_node:
            # Check if there's already a Principled BSDF connected properly
            principled = None
            for node in nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    principled = node
                    break
            
            # If no Principled BSDF, create one and rewire
            if not principled:
                # Create a new Principled BSDF
                principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                principled.location = (output_node.location.x - 300, output_node.location.y)
                print(f"  Created Principled BSDF for: {mat.name}")
            
            # Connect texture to Base Color
            base_color_connected = False
            for link in links:
                if link.to_node == principled and link.to_socket.name == 'Base Color':
                    base_color_connected = True
                    break
            
            if not base_color_connected and tex_images:
                links.new(tex_images[0].outputs['Color'], principled.inputs['Base Color'])
                print(f"  Connected texture to Base Color in: {mat.name}")
            
            # Connect Principled BSDF to output
            surface_connected = False
            for link in links:
                if link.to_node == output_node and link.to_socket.name == 'Surface' and link.from_node == principled:
                    surface_connected = True
                    break
            
            if not surface_connected:
                # Clear existing surface connection
                for link in list(links):
                    if link.to_node == output_node and link.to_socket.name == 'Surface':
                        links.remove(link)
                links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])
                print(f"  Connected Principled BSDF to output in: {mat.name}")

if merge and len(mesh_objects) > 1:
    print("Merging meshes (preserving materials)...")
    # Select all mesh objects
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    
    # Join all selected objects - this preserves materials
    bpy.ops.object.join()
    print("Meshes merged")

# Refresh mesh objects list after merge
mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']

# Calculate bounding box center of all meshes combined
print("Calculating mesh center...")
min_coord = Vector((float('inf'), float('inf'), float('inf')))
max_coord = Vector((float('-inf'), float('-inf'), float('-inf')))

for obj in mesh_objects:
    # Get world-space bounding box corners
    for corner in obj.bound_box:
        world_corner = obj.matrix_world @ Vector(corner)
        min_coord.x = min(min_coord.x, world_corner.x)
        min_coord.y = min(min_coord.y, world_corner.y)
        min_coord.z = min(min_coord.z, world_corner.z)
        max_coord.x = max(max_coord.x, world_corner.x)
        max_coord.y = max(max_coord.y, world_corner.y)
        max_coord.z = max(max_coord.z, world_corner.z)

# Calculate center (this is the ECEF center point)
center = (min_coord + max_coord) / 2
print(f"Mesh center (ECEF): {center}")

# For 3D tiles in ECEF coordinates, the "up" direction at any point
# is the normalized vector from Earth's center to that point.
# We need to rotate so this local "up" aligns with the Z axis.
print("Aligning ground plane to coordinate system...")

# The local "up" vector in ECEF is the normalized center position
# (pointing away from Earth's center)
local_up = center.normalized()
print(f"Local up vector: {local_up}")

# Target up vector (Z-up for Blender's coordinate system)
target_up = Vector((0, 0, 1))

# Calculate rotation to align local_up with target_up
# Using rotation_difference to find the quaternion that rotates local_up to target_up
rotation = local_up.rotation_difference(target_up)
rotation_matrix = rotation.to_matrix().to_4x4()

print(f"Rotation quaternion: {rotation}")

# Apply rotation to all mesh objects
for obj in mesh_objects:
    # Store original matrix
    original_matrix = obj.matrix_world.copy()
    # Apply rotation around the center point
    # First translate to origin, rotate, then translate back
    obj.matrix_world = rotation_matrix @ original_matrix

# After rotation, recalculate bounds and move to origin
print("Recalculating bounds after rotation...")
min_coord = Vector((float('inf'), float('inf'), float('inf')))
max_coord = Vector((float('-inf'), float('-inf'), float('-inf')))

for obj in mesh_objects:
    for corner in obj.bound_box:
        world_corner = obj.matrix_world @ Vector(corner)
        min_coord.x = min(min_coord.x, world_corner.x)
        min_coord.y = min(min_coord.y, world_corner.y)
        min_coord.z = min(min_coord.z, world_corner.z)
        max_coord.x = max(max_coord.x, world_corner.x)
        max_coord.y = max(max_coord.y, world_corner.y)
        max_coord.z = max(max_coord.z, world_corner.z)

# Calculate new center after rotation
new_center = (min_coord + max_coord) / 2
print(f"New center after rotation: {new_center}")

# Move all mesh objects so the center is at world origin
print("Moving mesh to world origin...")
for obj in mesh_objects:
    obj.location -= new_center

# Apply the transformation to make it permanent
bpy.ops.object.select_all(action='DESELECT')
for obj in mesh_objects:
    obj.select_set(True)
bpy.context.view_layer.objects.active = mesh_objects[0]

# Apply all transformations
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# Set origin to geometry center for each object
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
print("Origin set to geometry center")
print("Ground plane aligned with XY plane")

# Bake all textures into a single atlas if requested
if atlas_texture:
    print(f"Baking texture atlas ({atlas_size}x{atlas_size})...")
    
    # Refresh mesh objects
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    
    # Make sure we have exactly one merged object
    if len(mesh_objects) != 1:
        print("Atlas baking requires merged meshes. Merging now...")
        bpy.ops.object.select_all(action='DESELECT')
        for obj in mesh_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objects[0]
        bpy.ops.object.join()
        mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    
    obj = mesh_objects[0]
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    
    # Create the atlas image
    atlas_img = bpy.data.images.new(
        name="atlas_texture",
        width=atlas_size,
        height=atlas_size,
        alpha=False
    )
    
    # Set up the output path for the atlas
    output_dir = output_file.parent
    atlas_path = output_dir / "atlas_texture.jpg"
    
    # For each material, add an Image Texture node pointing to the atlas
    # (this tells the baker where to bake to)
    atlas_nodes = []
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if mat and mat.use_nodes:
            nodes = mat.node_tree.nodes
            # Create a new image texture node for the atlas
            atlas_node = nodes.new(type='ShaderNodeTexImage')
            atlas_node.image = atlas_img
            atlas_node.name = 'atlas_bake_target'
            atlas_node.label = 'Atlas Bake Target'
            # Select this node (bake target must be the selected/active node)
            nodes.active = atlas_node
            atlas_nodes.append((mat, atlas_node))
    
    # Switch to edit mode to create a new UV map for the atlas
    bpy.ops.object.mode_set(mode='EDIT')
    
    # Create a new UV map for the atlas
    atlas_uv = obj.data.uv_layers.new(name='atlas_uv')
    obj.data.uv_layers.active = atlas_uv
    
    # Select all geometry
    bpy.ops.mesh.select_all(action='SELECT')
    
    # Smart UV project to pack all UVs into a single [0,1] space
    print("Repacking UVs with Smart UV Project...")
    bpy.ops.uv.smart_project(
        angle_limit=1.15192,  # ~66 degrees
        island_margin=0.001,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=True
    )
    
    # Pack islands for better utilization
    bpy.ops.uv.pack_islands(margin=0.002)
    
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Set up bake settings
    scene = bpy.context.scene
    original_engine = scene.render.engine
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1  # Diffuse bake doesn't need many samples
    scene.cycles.bake_type = 'DIFFUSE'
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True
    scene.render.bake.margin = 4
    scene.render.bake.target = 'IMAGE_TEXTURES'
    
    # Bake from the original UVs to the new atlas UV
    print("Baking textures to atlas...")
    bpy.ops.object.bake(type='DIFFUSE')
    
    # Save the atlas as JPEG
    atlas_img.filepath_raw = str(atlas_path)
    atlas_img.file_format = 'JPEG'
    scene.render.image_settings.quality = jpeg_quality
    atlas_img.save()
    print(f"Saved atlas to: {atlas_path}")
    
    # Now remove the old UV map and rename atlas UV
    # Remove all materials' old texture nodes and replace with atlas
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if mat and mat.use_nodes:
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            
            # Find the principled BSDF and output
            principled = None
            output_node = None
            atlas_node = None
            old_tex_nodes = []
            
            for node in nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    principled = node
                elif node.type == 'OUTPUT_MATERIAL':
                    output_node = node
                elif node.name == 'atlas_bake_target':
                    atlas_node = node
                elif node.type == 'TEX_IMAGE' and node.name != 'atlas_bake_target':
                    old_tex_nodes.append(node)
            
            # Remove old texture nodes
            for old_node in old_tex_nodes:
                nodes.remove(old_node)
            
            # Connect atlas to principled BSDF base color
            if atlas_node and principled:
                # Clear existing base color links
                for link in list(links):
                    if link.to_node == principled and link.to_socket.name == 'Base Color':
                        links.remove(link)
                
                # Add UV Map node pointing to atlas_uv
                uv_node = nodes.new(type='ShaderNodeUVMap')
                uv_node.uv_map = 'atlas_uv'
                uv_node.location = (atlas_node.location.x - 300, atlas_node.location.y)
                
                links.new(uv_node.outputs['UV'], atlas_node.inputs['Vector'])
                links.new(atlas_node.outputs['Color'], principled.inputs['Base Color'])
    
    # Remove the old UV map(s), keep only atlas_uv
    old_uv_layers = [uv for uv in obj.data.uv_layers if uv.name != 'atlas_uv']
    for uv_layer in old_uv_layers:
        obj.data.uv_layers.remove(uv_layer)
    
    # Remove old images from blend data
    for img in list(bpy.data.images):
        if img != atlas_img and img.type not in ('RENDER_RESULT', 'COMPOSITING'):
            bpy.data.images.remove(img)
    
    # Collapse all material slots to a single material using the atlas
    # Create a single atlas material
    atlas_mat = bpy.data.materials.new(name='atlas_material')
    atlas_mat.use_nodes = True
    mat_nodes = atlas_mat.node_tree.nodes
    mat_links = atlas_mat.node_tree.links
    
    # Clear default nodes
    mat_nodes.clear()
    
    # Create nodes
    output_node = mat_nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (400, 0)
    
    principled = mat_nodes.new(type='ShaderNodeBsdfPrincipled')
    principled.location = (100, 0)
    
    tex_node = mat_nodes.new(type='ShaderNodeTexImage')
    tex_node.image = atlas_img
    tex_node.location = (-300, 0)
    
    uv_node = mat_nodes.new(type='ShaderNodeUVMap')
    uv_node.uv_map = 'atlas_uv'
    uv_node.location = (-600, 0)
    
    mat_links.new(uv_node.outputs['UV'], tex_node.inputs['Vector'])
    mat_links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
    mat_links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])
    
    # Replace all material slots with the atlas material
    obj.data.materials.clear()
    obj.data.materials.append(atlas_mat)
    
    # Reset all face material indices to 0 (the single atlas material)
    for poly in obj.data.polygons:
        poly.material_index = 0
    
    print(f"Atlas texture baked: {atlas_size}x{atlas_size} JPEG")
    print(f"All materials replaced with single atlas material")
    print(f"Reset {len(obj.data.polygons)} faces to atlas material")
    
    # Restore render engine
    scene.render.engine = original_engine
    
    # Override jpeg_textures since we already have a JPEG atlas
    jpeg_textures = False

# Select all objects for export
bpy.ops.object.select_all(action='SELECT')

# Prepare textures directory - do this FIRST before any image operations
output_dir = output_file.parent
textures_dir = output_dir / "textures"
textures_dir.mkdir(exist_ok=True)

# Change working directory to output directory FIRST
os.chdir(str(output_dir))
print(f"Working directory: {os.getcwd()}")

if atlas_texture:
    # Atlas path: texture is already saved as a JPEG file, skip the complex pipeline
    import shutil
    atlas_src = output_dir / "atlas_texture.jpg"
    atlas_dst = textures_dir / "atlas_texture.jpg"
    if atlas_src.exists() and atlas_src.resolve() != atlas_dst.resolve():
        shutil.copy2(str(atlas_src), str(atlas_dst))
    
    # Update the atlas image filepath to the textures dir copy
    for img in bpy.data.images:
        if img.name == 'atlas_texture':
            img.filepath = str(atlas_dst.resolve())
            if img.packed_file:
                img.unpack(method='USE_LOCAL')
            img.reload()
            print(f"  Atlas texture: {img.filepath}")
    
    print(f"Atlas texture ready at: {atlas_dst}")
else:
    # Non-atlas path: unpack and process individual textures
    print("Preparing textures for export...")
    print(f"Textures directory: {textures_dir}")
    print("Unpacking all images...")
    try:
        bpy.ops.file.unpack_all(method='WRITE_LOCAL')
        print("Unpacked all files")
    except Exception as e:
        print(f"Unpack all failed: {e}")

    # Now move/copy textures to our output textures directory and update paths
    saved_count = 0
    for img in bpy.data.images:
        if img.users == 0:
            continue
        if img.type in ('RENDER_RESULT', 'COMPOSITING'):
            continue
        
        # Get the current absolute filepath
        old_path = bpy.path.abspath(img.filepath) if img.filepath else ""
        
        if old_path and Path(old_path).exists():
            # Copy to our textures directory
            import shutil
            new_name = Path(old_path).name
            new_path = textures_dir / new_name
            
            if str(Path(old_path).parent.resolve()) != str(textures_dir.resolve()):
                try:
                    shutil.copy2(old_path, new_path)
                    print(f"  Copied: {new_name}")
                except Exception as e:
                    print(f"  Copy failed for {new_name}: {e}")
            
            # Update filepath to use absolute path to our textures dir
            img.filepath = str(new_path.resolve())
            saved_count += 1
        else:
            # Image wasn't unpacked - save directly from memory/packed data
            if img.packed_file or img.has_data:
                img_name = img.name
                for char in ['<', '>', ':', '"', '/', '\\\\', '|', '?', '*']:
                    img_name = img_name.replace(char, '_')
                if not img_name.endswith(('.png', '.jpg', '.jpeg')):
                    img_name += '.png'
                img_path = textures_dir / img_name

                try:
                    if img.packed_file:
                        # Write raw packed bytes directly — works in background
                        # mode without needing the GPU pixel buffer to be loaded.
                        img_path.write_bytes(img.packed_file.data)
                    else:
                        img.filepath_raw = str(img_path)
                        img.file_format = 'PNG'
                        img.save()
                    img.filepath = str(img_path.resolve())
                    saved_count += 1
                    print(f"  Saved from packed: {img_name}")
                except Exception as e:
                    print(f"  Failed to save {img_name}: {e}")

    print(f"Processed {saved_count} textures")

    # Convert textures to JPEG if requested
    if jpeg_textures:
        print(f"Converting textures to JPEG (quality={jpeg_quality})...")
        converted_count = 0
        for img in bpy.data.images:
            if img.users == 0:
                continue
            if img.type in ('RENDER_RESULT', 'COMPOSITING'):
                continue
            
            abs_path = bpy.path.abspath(img.filepath) if img.filepath else ""
            if abs_path and Path(abs_path).exists():
                # Get the new JPEG path
                old_path = Path(abs_path)
                new_path = old_path.with_suffix('.jpg')
                
                # Skip if already a JPEG
                if old_path.suffix.lower() in ['.jpg', '.jpeg']:
                    print(f"  Already JPEG: {old_path.name}")
                    continue
                
                try:
                    # Load image data
                    img.reload()
                    
                    # Set up render settings for JPEG
                    scene = bpy.context.scene
                    old_format = scene.render.image_settings.file_format
                    old_quality = scene.render.image_settings.quality
                    old_color_mode = scene.render.image_settings.color_mode
                    
                    scene.render.image_settings.file_format = 'JPEG'
                    scene.render.image_settings.quality = jpeg_quality
                    scene.render.image_settings.color_mode = 'RGB'
                    
                    # Save as JPEG
                    img.save_render(str(new_path))
                    
                    # Restore settings
                    scene.render.image_settings.file_format = old_format
                    scene.render.image_settings.quality = old_quality
                    scene.render.image_settings.color_mode = old_color_mode
                    
                    # Update image to use the new JPEG file
                    img.filepath = str(new_path)
                    img.reload()
                    
                    # Optionally remove the old PNG file
                    if old_path.suffix.lower() == '.png' and old_path.exists() and old_path != new_path:
                        old_path.unlink()
                        print(f"  Converted and removed: {old_path.name} -> {new_path.name}")
                    else:
                        print(f"  Converted: {old_path.name} -> {new_path.name}")
                    
                    converted_count += 1
                except Exception as e:
                    print(f"  Failed to convert {old_path.name}: {e}")
        
        print(f"Converted {converted_count} textures to JPEG")

# Force reload all images to ensure they have pixel data
print("Reloading images from disk...")
for img in bpy.data.images:
    if img.users > 0 and img.filepath and img.type not in ('RENDER_RESULT', 'COMPOSITING'):
        try:
            # Make sure path is absolute
            abs_path = bpy.path.abspath(img.filepath)
            if Path(abs_path).exists():
                img.filepath = abs_path
                # Unpack if packed - FBX exporter needs to read from disk files
                if img.packed_file:
                    img.unpack(method='USE_LOCAL')
                img.reload()
        except Exception as e:
            print(f"  Failed to reload {img.name}: {e}")

# Verify all images have valid file paths
print("Verifying texture files...")
all_valid = True
for img in bpy.data.images:
    if img.users > 0 and img.type not in ('RENDER_RESULT', 'COMPOSITING'):
        abs_path = bpy.path.abspath(img.filepath) if img.filepath else ""
        exists = Path(abs_path).exists() if abs_path else False
        packed = img.packed_file is not None
        print(f"  {img.name}: packed={packed}, exists={exists}, path={abs_path}")
        if not exists and not packed:
            all_valid = False

if not all_valid:
    print("Warning: Some textures may not export correctly!")

# Export to FBX
print(f"Exporting to {output_file}...")
print(f"Embed textures: {embed_textures}")

# Debug: print mesh info before export
mesh_objects_final = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
print(f"Objects to export: {len(mesh_objects_final)}")
for obj in mesh_objects_final:
    print(f"  {obj.name}: {len(obj.data.vertices)} verts, {len(obj.data.polygons)} faces, {len(obj.data.materials)} materials")
    for i, mat_slot in enumerate(obj.material_slots):
        print(f"    Material {i}: {mat_slot.material.name if mat_slot.material else 'None'}")
    for uv_layer in obj.data.uv_layers:
        print(f"    UV: {uv_layer.name} (active={uv_layer.active})")

# Always use COPY mode and embed textures for reliable texture transfer
export_kwargs = {
    'filepath': str(output_file),
    'use_selection': True,
    'global_scale': 1.0,
    'apply_unit_scale': True,
    'apply_scale_options': 'FBX_SCALE_ALL',
    'axis_forward': '-Z',
    'axis_up': 'Y',
    'object_types': {'MESH'},
    'use_mesh_modifiers': True,
    'mesh_smooth_type': 'OFF',
    'use_mesh_edges': False,
    'path_mode': 'COPY',
    'bake_space_transform': False,
    'use_custom_props': False,
    'embed_textures': embed_textures,
}

bpy.ops.export_scene.fbx(**export_kwargs)

print("Done!")
print(f"Output: {output_file}")
print(f"Model origin centered at geometry bounds")
print(f"Textures saved to: {textures_dir}")
if embed_textures:
    print("Textures also embedded in FBX file")
else:
    print("Textures referenced from textures folder")
if jpeg_textures:
    print(f"Textures converted to JPEG (quality={jpeg_quality})")
if atlas_texture:
    print(f"Single atlas texture: {atlas_size}x{atlas_size} JPEG")
"""


def find_blender():
    """Try to find Blender executable."""
    import shutil

    # Check if blender is in PATH
    blender_path = shutil.which("blender")
    if blender_path:
        return blender_path

    # Common installation paths on Windows
    common_paths = [
        Path("C:/Program Files/Blender Foundation/Blender 4.3/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.2/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.1/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 4.0/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 3.6/blender.exe"),
        Path("C:/Program Files/Blender Foundation/Blender 3.5/blender.exe"),
    ]

    for path in common_paths:
        if path.exists():
            return str(path)

    return None


def convert_tiles_to_fbx(
    input_dir: Path,
    output_file: Path,
    merge: bool = True,
    embed_textures: bool = True,
    jpeg_textures: bool = False,
    jpeg_quality: int = 90,
    atlas_texture: bool = False,
    atlas_size: int = 4096,
    blender_path: str = None,
):
    """
    Convert all GLB tiles in a directory to a single FBX file using Blender.

    Args:
        input_dir: Directory containing .glb files
        output_file: Output .fbx file path
        merge: If True, merge all meshes into one.
        embed_textures: If True, embed textures in the FBX file.
        jpeg_textures: If True, convert textures to JPEG format.
        jpeg_quality: JPEG quality (1-100) when jpeg_textures is True.
        atlas_texture: If True, bake all textures into a single atlas.
        atlas_size: Atlas texture resolution (default 4096).
        blender_path: Path to Blender executable (optional)
    """
    glb_files = list(input_dir.glob("*.glb"))

    if not glb_files:
        print(f"No .glb files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(glb_files)} GLB files")

    # Find Blender
    if blender_path is None:
        blender_path = find_blender()

    if blender_path is None:
        print("Error: Could not find Blender installation.")
        print("Please install Blender or specify the path with --blender")
        sys.exit(1)

    print(f"Using Blender: {blender_path}")

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Write the Blender script to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(BLENDER_SCRIPT)
        script_path = f.name

    try:
        # Run Blender in background mode
        cmd = [
            blender_path,
            "--background",
            "--python",
            script_path,
            "--",
            str(input_dir.absolute()),
            str(output_file.absolute()),
            str(merge),
            str(embed_textures),
            str(jpeg_textures),
            str(jpeg_quality),
            str(atlas_texture),
            str(atlas_size),
        ]

        print("Running Blender...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Print Blender output
        if result.stdout:
            for line in result.stdout.split("\n"):
                if any(
                    x in line
                    for x in [
                        "Found",
                        "Imported",
                        "Loaded",
                        "Merging",
                        "Exporting",
                        "Done",
                        "Warning",
                        "Error",
                        "Output",
                        "Texture",
                        "textures",
                        "materials",
                        "Saved",
                        "Reloading",
                        "Packing",
                        "Packed",
                        "packed",
                        "Failed",
                        "Baking",
                        "atlas",
                        "Atlas",
                        "Repacking",
                        "UV",
                        "material",
                    ]
                ):
                    print(line)

        if result.returncode != 0:
            print(f"Blender error output:\n{result.stderr}")
            sys.exit(1)

    finally:
        # Clean up temp script
        Path(script_path).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert GLB tiles to a single FBX file using Blender"
    )
    parser.add_argument(
        "-i", "--input", help="Input directory containing .glb files", required=True
    )
    parser.add_argument("-o", "--output", help="Output .fbx file path", required=True)
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Don't merge meshes, keep as separate objects",
    )
    parser.add_argument("--blender", help="Path to Blender executable", default=None)
    parser.add_argument(
        "--no-embed-textures",
        action="store_true",
        help="Don't embed textures in FBX, reference them externally",
    )
    parser.add_argument(
        "--jpeg",
        action="store_true",
        help="Convert textures to JPEG format for better compatibility",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=90,
        help="JPEG quality (1-100), default: 90",
    )
    parser.add_argument(
        "--atlas",
        action="store_true",
        help="Bake all textures into a single JPEG atlas with repacked UVs",
    )
    parser.add_argument(
        "--atlas-size",
        type=int,
        default=4096,
        help="Atlas texture resolution (default: 4096)",
    )

    args = parser.parse_args()

    input_dir = Path(args.input)
    output_file = Path(args.output)

    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    if not output_file.suffix.lower() == ".fbx":
        output_file = output_file.with_suffix(".fbx")

    convert_tiles_to_fbx(
        input_dir,
        output_file,
        merge=not args.no_merge,
        embed_textures=not args.no_embed_textures,
        jpeg_textures=args.jpeg,
        jpeg_quality=args.jpeg_quality,
        atlas_texture=args.atlas,
        atlas_size=args.atlas_size,
        blender_path=args.blender,
    )
