"""`um rig` end to end on a synthetic humanoid (primitives built in Blender). Skipped without Blender.

    uv run --with pytest pytest -q tests/test_rig.py
"""
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from um import rig  # noqa: E402
from um.render3d import blender_bin  # noqa: E402

try:
    BLENDER = blender_bin()
except SystemExit:
    BLENDER = None

BUILD = """
import bpy, sys
bpy.ops.wm.read_factory_settings(use_empty=True)
def box(x, y, z, sx, sy, sz):
    bpy.ops.mesh.primitive_cube_add(location=(x, y, z)); o = bpy.context.active_object; o.scale = (sx, sy, sz)
box(0, 0, 1.0, .18, .10, .45)                                   # torso
box(0, 0, 1.78, .11, .11, .12)                                  # head
for s in (1, -1):
    box(s * .75, 0, 1.52, .55, .06, .06)                        # arms out (T-pose)
    box(s * .12, 0, .42, .07, .08, .42)                         # legs
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=sys.argv[sys.argv.index("--") + 1], export_format="GLB")
"""


@pytest.mark.skipif(not BLENDER, reason="needs Blender")
def test_rig_t_pose_humanoid(tmp_path):
    script = tmp_path / "build.py"
    script.write_text(BUILD)
    src = tmp_path / "man.glb"
    subprocess.run([BLENDER, "-b", "--python", str(script), "--", str(src)], check=True, capture_output=True)
    out = tmp_path / "man_rigged.glb"
    info = rig.rig(str(src), str(out), preview=str(tmp_path / "p.png"), faces=0, anims=["idle", "walk"])
    assert info["arm_angle"] == 0 and info["unweighted_fraction"] < 0.05 and info["anims"] == ["idle", "walk"]
    assert (tmp_path / "p.png").stat().st_size > 1000
    b = out.read_bytes()
    j = json.loads(b[20:20 + struct.unpack("<I", b[12:16])[0]])
    assert len(j["skins"][0]["joints"]) > 100
    assert {a["name"] for a in j["animations"]} == {"idle", "walk"}
    assert {"JOINTS_0", "WEIGHTS_0"} <= set(j["meshes"][0]["primitives"][0]["attributes"])


@pytest.mark.skipif(not BLENDER, reason="needs Blender")
def test_rigging_a_rigged_glb_again(tmp_path):
    """The importer adds bone-display helpers (an 'Icosphere') and the old armature is called 'rig' too; neither may
    leak into the second rig."""
    script = tmp_path / "build.py"
    script.write_text(BUILD)
    src = tmp_path / "man.glb"
    subprocess.run([BLENDER, "-b", "--python", str(script), "--", str(src)], check=True, capture_output=True)
    first, second = tmp_path / "a.glb", tmp_path / "b.glb"
    rig.rig(str(src), str(first), faces=0)
    info = rig.rig(str(first), str(second), faces=0, anims=["idle"])
    assert info["tris_before"] == info["tris_after"] == 72      # six cubes: not the 80-triangle helper, nor 72 + 80
    assert info["unweighted_fraction"] < 0.05
    b = second.read_bytes()
    j = json.loads(b[20:20 + struct.unpack("<I", b[12:16])[0]])
    assert len(j["meshes"]) == 1 and len(j["skins"]) == 1 and len(j["skins"][0]["joints"]) > 100


def test_rig_cli_help(capsys):
    from um import cli
    with pytest.raises(SystemExit):
        cli.main(["rig", "--help"])
    assert "Rigify" in capsys.readouterr().out
