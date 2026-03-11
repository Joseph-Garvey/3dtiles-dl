"""Pytest configuration and shared fixtures for mesh output tests."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

_BLENDER_COMMON_PATHS = [
    "C:/Program Files/Blender Foundation/Blender 4.3/blender.exe",
    "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe",
    "C:/Program Files/Blender Foundation/Blender 4.1/blender.exe",
    "C:/Program Files/Blender Foundation/Blender 4.0/blender.exe",
    "C:/Program Files/Blender Foundation/Blender 3.6/blender.exe",
]


def _find_blender() -> str | None:
    if exe := shutil.which("blender"):
        return exe
    for p in _BLENDER_COMMON_PATHS:
        if Path(p).exists():
            return p
    return None


def pytest_addoption(parser):
    parser.addoption(
        "--mesh-dir",
        default=str(ROOT),
        help="Directory containing output mesh files (default: project root)",
    )
    parser.addoption(
        "--blender",
        default=None,
        help="Path to Blender executable (auto-detected if omitted)",
    )


@pytest.fixture(scope="session")
def mesh_dir(request) -> Path:
    return Path(request.config.getoption("--mesh-dir"))


@pytest.fixture(scope="session")
def blender_exe(request) -> str:
    exe = request.config.getoption("--blender") or _find_blender()
    if not exe:
        pytest.skip("Blender not found — skipping FBX tests")
    return exe


@pytest.fixture(scope="session")
def obj_path(mesh_dir) -> Path:
    p = mesh_dir / "output.obj"
    if not p.exists():
        pytest.skip(f"output.obj not found in {mesh_dir}")
    return p


@pytest.fixture(scope="session")
def dae_path(mesh_dir) -> Path:
    p = mesh_dir / "output.dae"
    if not p.exists():
        pytest.skip(f"output.dae not found in {mesh_dir}")
    return p


@pytest.fixture(scope="session")
def fbx_path(mesh_dir) -> Path:
    p = mesh_dir / "output.fbx"
    if not p.exists():
        pytest.skip(f"output.fbx not found in {mesh_dir}")
    return p
