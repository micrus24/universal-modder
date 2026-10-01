"""Auto-rig a humanoid model with Blender's Rigify: GLB in, rigged GLB out (deform bones, skin weights, optional loops).

    um rig knight.glb knight_rigged.glb                          # fit, generate, bind, export
    um rig knight.glb out.glb --preview check.png                # + a front render with the bones drawn over the mesh
    um rig knight.glb out.glb --anims idle,walk --faces 12000    # + simple procedural loops, game-sized mesh
    um rig knight.glb out.glb --yaw 180                          # the model faces +Y instead of -Y

Works on a standing humanoid, ideally in a T-pose (arms out), facing the camera (-Y in Blender; use --yaw if the
preview shows it turned). Rigify's human metarig is scaled to the mesh's height, and to its arm span when the
figure looks like a T-pose; the control rig is generated, and the mesh is bound with automatic weights (envelope
weights if heat weighting fails on a dirty mesh). The export keeps the deform bones only, ready for an engine.

Always look at the preview. The fit is automatic, not magic: arms that hang down, a cape, or a creature with extra
limbs need a manual pass in Blender (the metarig is just bones you can move in Edit Mode). The loops are plain
sine-wave idle / walk cycles on the control bones, meant as a starting point or a placeholder.
Needs Blender (PATH, BLENDER=, or the default install folder).
"""
from __future__ import annotations

import json
from pathlib import Path

from um.common import die, run_blender


def rig(glb: str, out: str, preview: str | None = None, faces: int = 20000, yaw: float = 0, fit_width: bool = True,
        arm_angle: float | None = None, anims: list[str] | None = None, frames: int = 24, fps: int = 24) -> dict:
    src = Path(glb)
    if not src.is_file():
        die(f"no such file: {src}")
    dst = Path(out).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(glb=str(src.resolve()), out=str(dst), preview=str(Path(preview).resolve()) if preview else None, faces=faces,
               yaw_deg=yaw, fit_width=fit_width, arm_angle=arm_angle, anims=anims or [], frames=frames, fps=fps)
    if cfg["preview"]:
        Path(cfg["preview"]).parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).parent / "blender" / "rig_humanoid.py"
    r = run_blender(script, cfg)
    lines = r.stdout.splitlines()
    res = [l for l in lines if l.startswith("RIG_RESULT ")]
    if r.returncode or not res:
        err = [l for l in lines if l.startswith("RIG_ERROR")]
        die("Blender rigging failed: " + (err[-1][10:] if err else (r.stderr or r.stdout)[-2500:]))
    return json.loads(res[-1][len("RIG_RESULT "):])


def main(a):
    info = rig(a.glb, a.out, a.preview, a.faces, a.yaw, not a.no_fit_width, a.arm_angle, [x for x in (a.anims or "").split(",") if x], a.frames)
    print(a.out)
    print(f"  mesh {info['tris_before']} -> {info['tris_after']} triangles, height {info['height']}, width {info['width']}")
    print(f"  arms swung down {info['arm_angle']:g} deg" if info["arm_angle"] else "  arms left in a T-pose")
    print(f"  metarig scaled x{info['arm_scale']} wide, x{info['height_scale']} tall; {info['deform_bones']} deform bones")
    print(f"  weights: {info['weights']}, {info['unweighted_fraction'] * 100:.1f}% of vertices without a bone")
    if info["anims"]:
        print(f"  animations: {', '.join(info['anims'])}")
    if a.preview:
        print(f"  preview: {a.preview}")


def register(sub):
    import argparse
    p = sub.add_parser("rig", help="auto-rig a humanoid GLB with Blender's Rigify (rigged GLB out)",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("glb")
    p.add_argument("out")
    p.add_argument("--preview", help="write a front render with the bones drawn over the mesh")
    p.add_argument("--faces", type=int, default=20000, help="decimate to about this many triangles first (0 = keep)")
    p.add_argument("--yaw", type=float, default=0, help="turn the model about Z (degrees) so it faces -Y")
    p.add_argument("--no-fit-width", action="store_true", help="scale the metarig by height only (arms hang down)")
    p.add_argument("--arm-angle", type=float, help="swing the metarig's arms down by this many degrees (default: 50 when the "
                   "mesh's arms hang, 0 for a T-pose)")
    p.add_argument("--anims", help="comma list of loops to key: idle, walk")
    p.add_argument("--frames", type=int, default=24, help="frames per loop")
    p.set_defaults(func=main)
