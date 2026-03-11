"""Validate mesh output files produced by the 3D tiles converter.

Tests cover:
- File can be loaded without errors
- Mesh has vertices and faces (not empty)
- All texture files referenced in materials exist on disk
"""

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import trimesh


# ── Helpers ──────────────────────────────────────────────────────────────────


def _geometry_counts(mesh) -> tuple[int, int]:
    """Return (total_vertices, total_faces) from a Trimesh or Scene."""
    if isinstance(mesh, trimesh.Scene):
        verts = sum(
            len(g.vertices)
            for g in mesh.geometry.values()
            if hasattr(g, "vertices")
        )
        faces = sum(
            len(g.faces)
            for g in mesh.geometry.values()
            if hasattr(g, "faces")
        )
    else:
        verts = len(mesh.vertices)
        faces = len(mesh.faces)
    return verts, faces


# ── OBJ ──────────────────────────────────────────────────────────────────────


def test_obj_loads(obj_path):
    """OBJ file must load without exceptions and contain geometry."""
    mesh = trimesh.load(str(obj_path), process=False)
    verts, faces = _geometry_counts(mesh)
    assert verts > 0, "OBJ loaded but has no vertices"
    assert faces > 0, "OBJ loaded but has no faces"
    print(f"\nOBJ: {verts} vertices, {faces} faces")


def test_obj_mtl_exists(obj_path):
    """A .mtl file must exist alongside the .obj."""
    mtl = obj_path.with_suffix(".mtl")
    assert mtl.exists(), f"MTL file missing: {mtl}"


def test_obj_has_texture_references(obj_path):
    """The .mtl must contain at least one map_Kd texture reference."""
    mtl = obj_path.with_suffix(".mtl")
    if not mtl.exists():
        pytest.skip("MTL not found")
    refs = [
        line.strip()
        for line in mtl.read_text().splitlines()
        if line.strip().lower().startswith("map_kd")
    ]
    assert refs, (
        "MTL has no map_Kd entries — textures were not exported. "
        "Check that materials use Principled BSDF with a texture connected to Base Color."
    )


def test_obj_textures_exist_on_disk(obj_path):
    """Every map_Kd texture referenced in the .mtl must exist on disk."""
    mtl = obj_path.with_suffix(".mtl")
    if not mtl.exists():
        pytest.skip("MTL not found")

    missing = []
    for line in mtl.read_text().splitlines():
        if line.strip().lower().startswith("map_kd"):
            rel = line.strip().split(None, 1)[1].strip()
            tex = (mtl.parent / rel).resolve()
            if not tex.exists():
                missing.append(rel)

    assert not missing, f"MTL references {len(missing)} missing texture(s):\n" + "\n".join(
        f"  {r}" for r in missing
    )


# ── DAE ──────────────────────────────────────────────────────────────────────


def test_dae_loads(dae_path):
    """DAE file must load without exceptions and contain geometry."""
    mesh = trimesh.load(str(dae_path), process=False)
    verts, faces = _geometry_counts(mesh)
    assert verts > 0, "DAE loaded but has no vertices"
    assert faces > 0, "DAE loaded but has no faces"
    print(f"\nDAE: {verts} vertices, {faces} faces")


def test_dae_textures_exist_on_disk(dae_path):
    """Every image file path declared in the DAE must exist on disk.

    COLLADA has two kinds of <init_from> elements:
      - Inside <library_images><image>  -> actual file path (checked here)
      - Inside <newparam><surface>      -> internal image ID reference (skipped)
    """
    tree = ET.parse(str(dae_path))
    missing = []
    for node in tree.getroot().iter():
        tag = node.tag.split("}")[-1]  # strip XML namespace
        if tag == "image":
            for child in node:
                ctag = child.tag.split("}")[-1]
                if ctag == "init_from" and child.text:
                    ref = child.text.strip()
                    if ref and not ref.startswith("#") and not ref.startswith("data:"):
                        tex = (dae_path.parent / ref).resolve()
                        if not tex.exists():
                            missing.append(ref)

    assert not missing, f"DAE references {len(missing)} missing texture(s):\n" + "\n".join(
        f"  {r}" for r in missing
    )


# ── FBX ──────────────────────────────────────────────────────────────────────

_FBX_BLENDER_SCRIPT = """\
import bpy, sys

fbx_path = sys.argv[sys.argv.index("--") + 1]

# Clear default scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Import FBX
try:
    bpy.ops.import_scene.fbx(filepath=fbx_path)
except Exception as e:
    print(f"IMPORT_ERROR: {e}")
    sys.exit(1)

meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
if not meshes:
    print("MESH_ERROR: no mesh objects found after FBX import")
    sys.exit(1)

total_verts = sum(len(m.data.vertices) for m in meshes)
total_faces = sum(len(m.data.polygons) for m in meshes)

if total_verts == 0:
    print("MESH_ERROR: mesh has no vertices")
    sys.exit(1)
if total_faces == 0:
    print("MESH_ERROR: mesh has no faces")
    sys.exit(1)

# Check materials have textures
textured = 0
for obj in meshes:
    for slot in obj.material_slots:
        mat = slot.material
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    textured += 1

print(f"FBX_OK objects={len(meshes)} verts={total_verts} faces={total_faces} textured_mats={textured}")
"""


def test_fbx_loads(fbx_path, blender_exe):
    """FBX must import cleanly in Blender with geometry and materials."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_FBX_BLENDER_SCRIPT)
        script = Path(f.name)

    try:
        result = subprocess.run(
            [
                blender_exe,
                "--background",
                "--python",
                str(script),
                "--",
                str(fbx_path.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        stdout = result.stdout
        stderr = result.stderr

        # Surface any import or mesh errors from the script
        error_lines = [l for l in stdout.splitlines() if "ERROR" in l]
        assert result.returncode == 0, (
            f"Blender exited with code {result.returncode}\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
        assert not error_lines, "FBX validation errors:\n" + "\n".join(error_lines)

        ok_line = next((l for l in stdout.splitlines() if l.startswith("FBX_OK")), None)
        assert ok_line, f"FBX_OK status line not found in output:\n{stdout}"
        print(f"\n{ok_line}")

    finally:
        script.unlink(missing_ok=True)
