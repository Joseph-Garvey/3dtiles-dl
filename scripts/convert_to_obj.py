"""Convert downloaded GLB tiles to a single OBJ file using Blender."""

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

# Fix material textures - convert to standard Principled BSDF for OBJ export
# Google 3D Tiles use Emission-based materials which OBJ doesn't handle well
print("Converting materials to Principled BSDF for OBJ compatibility...")
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

# Select all objects for export
bpy.ops.object.select_all(action='SELECT')

# Create textures directory next to output file
output_dir = output_file.parent
textures_dir = output_dir / "textures"
textures_dir.mkdir(exist_ok=True)

# Change working directory so // paths resolve correctly
os.chdir(str(output_dir))

# Save all images to the textures directory and update their paths
print("Saving textures...")
saved_count = 0
for img in bpy.data.images:
    if img.users == 0:
        continue
    if img.type in ('RENDER_RESULT', 'COMPOSITING'):
        continue
    if not img.packed_file and not img.has_data:
        continue

    img_name = img.name
    if not img_name.endswith(('.png', '.jpg', '.jpeg')):
        img_name = f"{img_name}.png"
    img_path = textures_dir / img_name

    try:
        if img.packed_file:
            # Write raw packed bytes directly — works in background mode
            # without needing the GPU pixel buffer (has_data) to be loaded.
            img_path.write_bytes(img.packed_file.data)
        else:
            img.filepath_raw = str(img_path)
            img.file_format = 'PNG'
            img.save()
        # Use Blender's // convention (relative to blend-file dir = CWD =
        # output_dir) so the OBJ exporter writes "textures/<name>" in the
        # .mtl rather than an absolute Windows path.
        img.filepath = f"//textures/{img_name}"
        saved_count += 1
        print(f"  Saved texture: {img_name}")
    except Exception as e:
        print(f"  Warning: Could not save texture {img_name}: {e}")
print(f"Saved {saved_count} textures")

# Export to OBJ with textures
print(f"Exporting to {output_file}...")

bpy.ops.wm.obj_export(
    filepath=str(output_file),
    export_selected_objects=True,
    export_uv=True,
    export_normals=True,
    export_colors=True,
    export_materials=True,
    export_pbr_extensions=True,
    path_mode='RELATIVE',
    forward_axis='NEGATIVE_Z',
    up_axis='Y',
)

print("Done!")
print(f"Output: {output_file}")
print(f"Model origin centered at geometry bounds")
print(f"Textures saved to: {textures_dir}")
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


def convert_tiles_to_obj(
    input_dir: Path,
    output_file: Path,
    merge: bool = True,
    blender_path: str = None,
):
    """
    Convert all GLB tiles in a directory to a single OBJ file using Blender.

    Args:
        input_dir: Directory containing .glb files
        output_file: Output .obj file path
        merge: If True, merge all meshes into one.
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
                        "materials",
                        "textures",
                        "Saved",
                        "Origin",
                        "Ground",
                    ]
                ):
                    print(line)

        if result.returncode != 0:
            print(f"Blender error output:\n{result.stderr}")
            sys.exit(1)

        # Post-process the .mtl to fix texture paths.
        # Blender's path_mode='RELATIVE' in background mode (no blend file)
        # produces incorrect relative paths on Windows.  We know every texture
        # was saved to textures/<name>, so rewrite map_Kd entries directly.
        mtl_file = output_file.with_suffix(".mtl")
        if mtl_file.exists():
            lines = mtl_file.read_text(encoding="utf-8").splitlines()
            fixed = []
            for line in lines:
                stripped = line.strip()
                if stripped.lower().startswith("map_kd") and " " in stripped:
                    img_name = Path(stripped.split(None, 1)[1].strip()).name
                    fixed.append(f"map_Kd textures/{img_name}")
                else:
                    fixed.append(line)
            mtl_file.write_text("\n".join(fixed) + "\n", encoding="utf-8")
            print("Fixed texture paths in MTL")

    finally:
        # Clean up temp script
        Path(script_path).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert GLB tiles to a single OBJ file using Blender"
    )
    parser.add_argument(
        "-i", "--input", help="Input directory containing .glb files", required=True
    )
    parser.add_argument("-o", "--output", help="Output .obj file path", required=True)
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Don't merge meshes, keep as separate objects",
    )
    parser.add_argument("--blender", help="Path to Blender executable", default=None)

    args = parser.parse_args()

    input_dir = Path(args.input)
    output_file = Path(args.output)

    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    if not output_file.suffix.lower() == ".obj":
        output_file = output_file.with_suffix(".obj")

    convert_tiles_to_obj(
        input_dir,
        output_file,
        merge=not args.no_merge,
        blender_path=args.blender,
    )
