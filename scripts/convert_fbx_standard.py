"""Convert FBX to use standard Lambert/Phong materials for wider compatibility."""

import argparse
from pathlib import Path
import subprocess
import sys


BLENDER_SCRIPT = """
import bpy
import sys
import os
from pathlib import Path

# Get arguments after "--"
argv = sys.argv
argv = argv[argv.index("--") + 1:]
input_file = Path(argv[0])
output_file = Path(argv[1])

# Clear default scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Import FBX
print(f"Importing {input_file}...")
bpy.ops.import_scene.fbx(filepath=str(input_file))

# Get all mesh objects
mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
print(f"Found {len(mesh_objects)} mesh objects")

# Convert materials to standard diffuse materials
print("Converting materials to standard format...")
for mat in bpy.data.materials:
    if not mat.use_nodes:
        continue
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    # Find texture image and output nodes
    tex_image = None
    output_node = None
    
    for node in nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            tex_image = node
        elif node.type == 'OUTPUT_MATERIAL':
            output_node = node
    
    if not output_node:
        continue
    
    # Remove all nodes except texture and output
    nodes_to_remove = [n for n in nodes if n not in [tex_image, output_node]]
    for node in nodes_to_remove:
        nodes.remove(node)
    
    # Create a simple Diffuse BSDF shader (more compatible than Principled)
    diffuse = nodes.new(type='ShaderNodeBsdfDiffuse')
    diffuse.location = (output_node.location.x - 200, output_node.location.y)
    
    # Connect diffuse to output
    links.new(diffuse.outputs['BSDF'], output_node.inputs['Surface'])
    
    # Connect texture to diffuse color if we have one
    if tex_image:
        tex_image.location = (diffuse.location.x - 300, diffuse.location.y)
        links.new(tex_image.outputs['Color'], diffuse.inputs['Color'])
        print(f"  {mat.name}: Converted to Diffuse with texture")
    else:
        # Set a default gray color
        diffuse.inputs['Color'].default_value = (0.8, 0.8, 0.8, 1.0)
        print(f"  {mat.name}: Converted to Diffuse (no texture)")

# Prepare output directory and textures
output_dir = output_file.parent
textures_dir = output_dir / "textures"
textures_dir.mkdir(exist_ok=True)
os.chdir(str(output_dir))

# Unpack textures to files
print("Preparing textures...")
try:
    bpy.ops.file.unpack_all(method='WRITE_LOCAL')
except:
    pass

# Update image paths to absolute
for img in bpy.data.images:
    if img.filepath:
        abs_path = bpy.path.abspath(img.filepath)
        if Path(abs_path).exists():
            img.filepath = abs_path
            if img.packed_file:
                img.unpack(method='USE_LOCAL')
            img.reload()

# Select all for export
bpy.ops.object.select_all(action='SELECT')

# Export with standard settings
print(f"Exporting to {output_file}...")
bpy.ops.export_scene.fbx(
    filepath=str(output_file),
    use_selection=True,
    global_scale=1.0,
    apply_unit_scale=True,
    apply_scale_options='FBX_SCALE_ALL',
    axis_forward='-Z',
    axis_up='Y',
    object_types={'MESH', 'ARMATURE'},
    use_mesh_modifiers=True,
    mesh_smooth_type='OFF',
    path_mode='COPY',
    embed_textures=True,
    bake_space_transform=True,
)

print("Done!")
print(f"Output: {output_file}")
"""


def find_blender():
    """Find Blender executable."""
    import shutil

    # Common Blender paths
    paths = [
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
    ]

    for path in paths:
        if Path(path).exists():
            return path

    # Try PATH
    blender = shutil.which("blender")
    if blender:
        return blender

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Convert FBX to use standard materials"
    )
    parser.add_argument("-i", "--input", required=True, help="Input FBX file")
    parser.add_argument("-o", "--output", required=True, help="Output FBX file")
    args = parser.parse_args()

    input_file = Path(args.input).resolve()
    output_file = Path(args.output).resolve()

    if not input_file.exists():
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)

    blender = find_blender()
    if not blender:
        print("Error: Blender not found")
        sys.exit(1)

    print(f"Input: {input_file}")
    print(f"Output: {output_file}")
    print(f"Using Blender: {blender}")

    # Write script to temp file
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(BLENDER_SCRIPT)
        script_path = f.name

    try:
        # Run Blender
        cmd = [
            blender,
            "--background",
            "--python",
            script_path,
            "--",
            str(input_file),
            str(output_file),
        ]

        print("Running Blender...")
        result = subprocess.run(cmd, capture_output=False)

        if result.returncode != 0:
            print(f"Error: Blender exited with code {result.returncode}")
            sys.exit(1)

    finally:
        Path(script_path).unlink()


if __name__ == "__main__":
    main()
