"""Convert downloaded GLB tiles to a single DAE (COLLADA) file with a separate JPEG texture."""

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
jpeg_quality = int(argv[3]) if len(argv) > 3 else 90
atlas_size = int(argv[4]) if len(argv) > 4 else 4096

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
            if slot.material.use_nodes:
                for node in slot.material.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        all_images.add(node.image.name)

print(f"Found {len(all_materials)} materials and {len(all_images)} textures")

# Fix material textures - convert to standard Principled BSDF
# Google 3D Tiles use Emission-based materials
print("Converting materials to Principled BSDF...")
for mat in bpy.data.materials:
    if mat.use_nodes:
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        tex_images = []
        output_node = None

        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                tex_images.append(node)
            elif node.type == 'OUTPUT_MATERIAL':
                output_node = node

        if tex_images and output_node:
            principled = None
            for node in nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    principled = node
                    break

            if not principled:
                principled = nodes.new(type='ShaderNodeBsdfPrincipled')
                principled.location = (output_node.location.x - 300, output_node.location.y)
                print(f"  Created Principled BSDF for: {mat.name}")

            base_color_connected = False
            for link in links:
                if link.to_node == principled and link.to_socket.name == 'Base Color':
                    base_color_connected = True
                    break

            if not base_color_connected and tex_images:
                links.new(tex_images[0].outputs['Color'], principled.inputs['Base Color'])
                print(f"  Connected texture to Base Color in: {mat.name}")

            surface_connected = False
            for link in links:
                if link.to_node == output_node and link.to_socket.name == 'Surface' and link.from_node == principled:
                    surface_connected = True
                    break

            if not surface_connected:
                for link in list(links):
                    if link.to_node == output_node and link.to_socket.name == 'Surface':
                        links.remove(link)
                links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])
                print(f"  Connected Principled BSDF to output in: {mat.name}")

if merge and len(mesh_objects) > 1:
    print("Merging meshes (preserving materials)...")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    bpy.ops.object.join()
    print("Meshes merged")

# Refresh mesh objects list after merge
mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']

# Calculate bounding box center of all meshes combined
print("Calculating mesh center...")
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

# Calculate center (ECEF center point)
center = (min_coord + max_coord) / 2
print(f"Mesh center (ECEF): {center}")

# Align ground plane - rotate so local "up" aligns with Z axis
print("Aligning ground plane to coordinate system...")
local_up = center.normalized()
print(f"Local up vector: {local_up}")

target_up = Vector((0, 0, 1))
rotation = local_up.rotation_difference(target_up)
rotation_matrix = rotation.to_matrix().to_4x4()
print(f"Rotation quaternion: {rotation}")

# Apply rotation to all mesh objects
for obj in mesh_objects:
    original_matrix = obj.matrix_world.copy()
    obj.matrix_world = rotation_matrix @ original_matrix

# Recalculate bounds and move to origin
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

new_center = (min_coord + max_coord) / 2
print(f"New center after rotation: {new_center}")

# Move all mesh objects so the center is at world origin
print("Moving mesh to world origin...")
for obj in mesh_objects:
    obj.location -= new_center

# Apply transformations permanently
bpy.ops.object.select_all(action='DESELECT')
for obj in mesh_objects:
    obj.select_set(True)
bpy.context.view_layer.objects.active = mesh_objects[0]

bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
print("Origin set to geometry center")
print("Ground plane aligned with XY plane")

# Bake all textures into a single atlas
print(f"Baking texture atlas ({atlas_size}x{atlas_size})...")

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

output_dir = output_file.parent
textures_dir = output_dir / "textures"
textures_dir.mkdir(exist_ok=True)
atlas_path = textures_dir / "atlas_texture.jpg"

# For each material, add an Image Texture node pointing to the atlas
atlas_nodes = []
for mat_slot in obj.material_slots:
    mat = mat_slot.material
    if mat and mat.use_nodes:
        nodes = mat.node_tree.nodes
        atlas_node = nodes.new(type='ShaderNodeTexImage')
        atlas_node.image = atlas_img
        atlas_node.name = 'atlas_bake_target'
        atlas_node.label = 'Atlas Bake Target'
        nodes.active = atlas_node
        atlas_nodes.append((mat, atlas_node))

# Create a new UV map for the atlas
bpy.ops.object.mode_set(mode='EDIT')

atlas_uv = obj.data.uv_layers.new(name='atlas_uv')
obj.data.uv_layers.active = atlas_uv

bpy.ops.mesh.select_all(action='SELECT')

print("Repacking UVs with Smart UV Project...")
bpy.ops.uv.smart_project(
    angle_limit=1.15192,  # ~66 degrees
    island_margin=0.001,
    area_weight=0.0,
    correct_aspect=True,
    scale_to_bounds=True
)

bpy.ops.uv.pack_islands(margin=0.002)
bpy.ops.object.mode_set(mode='OBJECT')

# Set up bake settings
scene = bpy.context.scene
original_engine = scene.render.engine
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 1
scene.cycles.bake_type = 'DIFFUSE'
scene.render.bake.use_pass_direct = False
scene.render.bake.use_pass_indirect = False
scene.render.bake.use_pass_color = True
scene.render.bake.margin = 4
scene.render.bake.target = 'IMAGE_TEXTURES'

print("Baking textures to atlas...")
bpy.ops.object.bake(type='DIFFUSE')

# Save the atlas as JPEG
atlas_img.filepath_raw = str(atlas_path)
atlas_img.file_format = 'JPEG'
scene.render.image_settings.quality = jpeg_quality
atlas_img.save()
print(f"Saved atlas to: {atlas_path}")

# Clean up materials - replace all with single atlas material
for mat_slot in obj.material_slots:
    mat = mat_slot.material
    if mat and mat.use_nodes:
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

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

        for old_node in old_tex_nodes:
            nodes.remove(old_node)

        if atlas_node and principled:
            for link in list(links):
                if link.to_node == principled and link.to_socket.name == 'Base Color':
                    links.remove(link)

            uv_node = nodes.new(type='ShaderNodeUVMap')
            uv_node.uv_map = 'atlas_uv'
            uv_node.location = (atlas_node.location.x - 300, atlas_node.location.y)

            links.new(uv_node.outputs['UV'], atlas_node.inputs['Vector'])
            links.new(atlas_node.outputs['Color'], principled.inputs['Base Color'])

# Remove old UV maps, keep only atlas_uv
old_uv_layers = [uv for uv in obj.data.uv_layers if uv.name != 'atlas_uv']
for uv_layer in old_uv_layers:
    obj.data.uv_layers.remove(uv_layer)

# Remove old images
for img in list(bpy.data.images):
    if img != atlas_img and img.type not in ('RENDER_RESULT', 'COMPOSITING'):
        bpy.data.images.remove(img)

# Create a single atlas material
atlas_mat = bpy.data.materials.new(name='atlas_material')
atlas_mat.use_nodes = True
mat_nodes = atlas_mat.node_tree.nodes
mat_links = atlas_mat.node_tree.links

mat_nodes.clear()

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

for poly in obj.data.polygons:
    poly.material_index = 0

print(f"Atlas texture baked: {atlas_size}x{atlas_size} JPEG")
print(f"All materials replaced with single atlas material")

scene.render.engine = original_engine

# Update atlas image filepath and ensure it's not packed
atlas_img.filepath = str(atlas_path.resolve())
if atlas_img.packed_file:
    atlas_img.unpack(method='USE_LOCAL')
atlas_img.reload()

# Select all objects for export
bpy.ops.object.select_all(action='SELECT')

# Export to COLLADA (.dae)
print(f"Exporting to {output_file}...")

mesh_objects_final = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
print(f"Objects to export: {len(mesh_objects_final)}")
for obj in mesh_objects_final:
    print(f"  {obj.name}: {len(obj.data.vertices)} verts, {len(obj.data.polygons)} faces, {len(obj.data.materials)} materials")
    for uv_layer in obj.data.uv_layers:
        print(f"    UV: {uv_layer.name} (active={uv_layer.active})")

bpy.ops.wm.collada_export(
    filepath=str(output_file),
    apply_modifiers=True,
    selected=True,
    use_texture_copies=True,
)

print("Done!")
print(f"Output: {output_file}")
print(f"Texture: {atlas_path}")
"""


def find_blender():
    """Try to find Blender executable."""
    import shutil

    blender_path = shutil.which("blender")
    if blender_path:
        return blender_path

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


def convert_tiles_to_dae(
    input_dir: Path,
    output_file: Path,
    merge: bool = True,
    jpeg_quality: int = 90,
    atlas_size: int = 4096,
    blender_path: str = None,
):
    """
    Convert all GLB tiles in a directory to a single DAE file with a separate JPEG texture.

    Args:
        input_dir: Directory containing .glb files
        output_file: Output .dae file path
        merge: If True, merge all meshes into one.
        jpeg_quality: JPEG quality (1-100) for the atlas texture.
        atlas_size: Atlas texture resolution (default 4096).
        blender_path: Path to Blender executable (optional)
    """
    glb_files = list(input_dir.glob("*.glb"))

    if not glb_files:
        print(f"No .glb files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(glb_files)} GLB files")

    if blender_path is None:
        blender_path = find_blender()

    if blender_path is None:
        print("Error: Could not find Blender installation.")
        print("Please install Blender or specify the path with --blender")
        sys.exit(1)

    print(f"Using Blender: {blender_path}")

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(BLENDER_SCRIPT)
        script_path = f.name

    try:
        cmd = [
            blender_path,
            "--background",
            "--python",
            script_path,
            "--",
            str(input_dir.absolute()),
            str(output_file.absolute()),
            str(merge),
            str(jpeg_quality),
            str(atlas_size),
        ]

        print("Running Blender...")
        result = subprocess.run(cmd, capture_output=True, text=True)

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
                        "Baking",
                        "atlas",
                        "Atlas",
                        "Repacking",
                        "UV",
                        "material",
                        "Objects",
                    ]
                ):
                    print(line)

        if result.returncode != 0:
            print(f"Blender error output:\n{result.stderr}")
            sys.exit(1)

    finally:
        Path(script_path).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert GLB tiles to a single DAE file with a separate JPEG texture"
    )
    parser.add_argument(
        "-i", "--input", help="Input directory containing .glb files", required=True
    )
    parser.add_argument("-o", "--output", help="Output .dae file path", required=True)
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Don't merge meshes, keep as separate objects",
    )
    parser.add_argument("--blender", help="Path to Blender executable", default=None)
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=90,
        help="JPEG quality (1-100), default: 90",
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

    if not output_file.suffix.lower() == ".dae":
        output_file = output_file.with_suffix(".dae")

    convert_tiles_to_dae(
        input_dir,
        output_file,
        merge=not args.no_merge,
        jpeg_quality=args.jpeg_quality,
        atlas_size=args.atlas_size,
        blender_path=args.blender,
    )
