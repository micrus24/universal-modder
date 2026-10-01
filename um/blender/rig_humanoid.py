"""Auto-rig a humanoid GLB with Blender's Rigify. Runs inside Blender:

    blender -b --python rig_humanoid.py -- config.json

(`um rig` writes the config and calls this.) Steps: import, join and clean the meshes, decimate to a game-sized
triangle count, fit Rigify's human metarig to the mesh's bounding box (height, and arm span when the figure is in
a T-pose), generate the control rig, bind the mesh with automatic weights (falling back to envelope weights when
heat weighting fails), optionally key simple idle / walk loops on the control bones, export a GLB with the
deform bones only, and render a front preview with the bones drawn over the mesh.

Config keys: glb, out, preview (png or null), faces (target triangles, 0 = keep), yaw_deg (rotate the model about Z
so it faces -Y, the way the metarig does), fit_width (bool), anims (list of "idle" / "walk"), frames, fps.
Prints one line "RIG_RESULT {json}" with the numbers `um rig` reports.
"""
import json
import math
import sys

import bpy
from mathutils import Vector

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
result = {}

import addon_utils  # noqa: E402

try:                       # Rigify ships with Blender but is off by default. The operator creates the preferences entry
    bpy.ops.preferences.addon_enable(module="rigify")      # that Rigify's own register() needs (addon_utils.enable doesn't)
except Exception:
    try:
        addon_utils.enable("rigify", default_set=True, persistent=True)
    except Exception:
        pass
if not hasattr(bpy.ops.object, "armature_human_metarig_add"):
    print("RIG_ERROR Rigify is not available in this Blender (enable it in Preferences > Add-ons / Extensions)")
    sys.exit(1)


def select_only(*objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[-1]


def bounds(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


# ---- import + clean the mesh
bpy.ops.import_scene.gltf(filepath=cfg["glb"])
# Importing a skinned GLB makes bone-display helpers ("Icosphere", ...) in a "glTF_not_exported" collection; they are
# meshes to Blender but not part of the model.
for o in [o for o in scene.objects if any(c.name == "glTF_not_exported" for c in o.users_collection)]:
    bpy.data.objects.remove(o, do_unlink=True)
meshes = [o for o in scene.objects if o.type == "MESH"]
if not meshes:
    print("RIG_ERROR no mesh in the GLB")
    sys.exit(1)
for o in meshes:                       # keep world position, then forget the old skeleton and its weights
    world = o.matrix_world.copy()
    o.parent = None
    o.matrix_world = world
    for m in [m for m in o.modifiers if m.type == "ARMATURE"]:
        o.modifiers.remove(m)
    o.vertex_groups.clear()
for o in [o for o in scene.objects if o.type != "MESH"]:   # imported armatures / empties would shadow the new rig
    bpy.data.objects.remove(o, do_unlink=True)
select_only(*meshes)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
if len(meshes) > 1:
    bpy.ops.object.join()
mesh = bpy.context.active_object
mesh.name = "body"
if cfg.get("yaw_deg"):
    mesh.rotation_euler[2] = math.radians(cfg["yaw_deg"])
    bpy.ops.object.transform_apply(rotation=True)

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=1e-5)
bpy.ops.object.mode_set(mode="OBJECT")
tris_before = sum(len(p.vertices) - 2 for p in mesh.data.polygons)
faces = cfg.get("faces", 20000)

def tri_count():
    return sum(len(p.vertices) - 2 for p in mesh.data.polygons)


for _ in range(6):                  # one collapse pass stops at ~40% on dense scans, so repeat until the target
    now = tri_count()
    if not faces or now <= faces * 1.05:
        break
    mod = mesh.modifiers.new("decimate", "DECIMATE")
    mod.ratio = max(0.01, faces / now)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    if tri_count() >= now * 0.97:
        break
if faces and tri_count() > faces * 1.5 and not mesh.data.uv_layers:
    # Open (non-watertight) scans block edge collapse. A voxel remesh makes the surface closed and lets us pick the
    # density: quads ~ area / size^2, two triangles each. It drops UVs and vertex colours, hence the uv_layers guard.
    area = sum(p.area for p in mesh.data.polygons)
    select_only(mesh)
    mesh.data.remesh_voxel_size = (2 * area / faces) ** 0.5
    bpy.ops.object.voxel_remesh()
    tri = mesh.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=tri.name)
    result["remeshed"] = True
    for _ in range(3):
        now = tri_count()
        if now <= faces * 1.1:
            break
        mod = mesh.modifiers.new("decimate", "DECIMATE")
        mod.ratio = max(0.01, faces / now)
        bpy.ops.object.modifier_apply(modifier=mod.name)
result["tris_before"], result["tris_after"] = tris_before, tri_count()

lo, hi = bounds(mesh)
height = hi.z - lo.z
# Arm span, measured on the shoulder band only: image-to-3D meshes often carry a ground sheet or a cape that would
# inflate the full bounding box. Hanging arms give ~0.4 x height here, a T-pose ~1.0 x height.
xs = [(mesh.matrix_world @ v.co).x for v in mesh.data.vertices
      if lo.z + 0.62 * height <= (mesh.matrix_world @ v.co).z <= lo.z + 0.84 * height]
span = (max(xs) - min(xs)) if xs else hi.x - lo.x
center = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
result["height"], result["width"] = round(height, 4), round(span, 4)

# ---- fit the metarig
bpy.ops.object.armature_human_metarig_add()
meta = bpy.context.active_object
mlo, mhi = bounds(meta)
mh, mw = mhi.z - mlo.z, mhi.x - mlo.x
sz = height / mh
sx = sz
if cfg.get("fit_width", True) and span > 0.75 * height:      # looks like a T-pose: match the arm span
    sx = span / mw
meta.scale = (sx, sz, sz)          # Y (depth) follows the height so the figure keeps its proportions
bpy.context.view_layer.update()
mlo, mhi = bounds(meta)
meta.location = (center.x - (mlo.x + mhi.x) / 2 + meta.location.x, center.y - (mlo.y + mhi.y) / 2 + meta.location.y,
                 center.z - mlo.z + meta.location.z)
select_only(meta)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
result["arm_scale"], result["height_scale"] = round(sx, 3), round(sz, 3)

# Match the metarig's limbs to the mesh. Arms: the metarig's point out and ~28 deg down; if the mesh's hang (A-pose /
# relaxed), swing each chain further down about the shoulder and move the joint out to the shoulder width. Legs: spread
# the thigh chains to the mesh's stance. Otherwise the bones sit outside the limbs and heat weighting binds them to
# the torso. Both are rigid moves of a whole bone chain, so Rigify still sees a normal metarig.
from mathutils import Matrix  # noqa: E402


def move_chain(ebs, top_name, rot, shift):
    top = ebs.get(top_name)
    if top is None:
        return
    parent = top.parent
    chain = [top] + list(top.children_recursive)
    pivot = top.head.copy()
    snap = [(eb, eb.head.copy(), eb.tail.copy(), eb.z_axis.copy(), eb.use_connect) for eb in chain]
    parent_conn = parent.use_connect if parent else False
    for eb in chain + ([parent] if parent else []):
        eb.use_connect = False                  # connected bones drag their parent's tail along while we assign
    for eb, head, tail, z_axis, _ in snap:
        eb.head, eb.tail = pivot + shift + rot @ (head - pivot), pivot + shift + rot @ (tail - pivot)
        eb.align_roll(rot @ z_axis)
    if parent:
        parent.tail = pivot + shift
    for eb, _, _, _, conn in snap:
        eb.use_connect = conn
    if parent:
        parent.use_connect = parent_conn


def band_halfwidth(z0, z1):
    xs = [(mesh.matrix_world @ v.co).x for v in mesh.data.vertices if lo.z + z0 * height <= (mesh.matrix_world @ v.co).z <= lo.z + z1 * height]
    return max(abs(min(xs)), abs(max(xs))) if xs else 0.0


arm_angle = cfg.get("arm_angle")
if arm_angle is None:
    arm_angle = 0.0 if span > 0.75 * height else 50.0
result["arm_angle"] = arm_angle
leg_x = 0.65 * band_halfwidth(0.06, 0.38)
select_only(meta)
bpy.ops.object.mode_set(mode="EDIT")
ebs = meta.data.edit_bones
for side, sign in (("L", 1), ("R", -1)):
    if arm_angle:
        top = ebs.get(f"upper_arm.{side}")
        if top is not None:
            move_chain(ebs, top.name, Matrix.Rotation(sign * math.radians(arm_angle), 3, "Y"),
                       Vector((sign * max(0.0, 0.33 * span - abs(top.head.x)), 0, 0)))
    thigh = ebs.get(f"thigh.{side}")
    if thigh is not None and cfg.get("fit_legs", True):
        move_chain(ebs, thigh.name, Matrix.Identity(3), Vector((sign * max(0.0, leg_x - abs(thigh.head.x)), 0, 0)))
bpy.ops.object.mode_set(mode="OBJECT")

# ---- generate the control rig
select_only(meta)
armatures_before = {o.name for o in scene.objects if o.type == "ARMATURE"}
bpy.ops.pose.rigify_generate()
rig = next((o for o in scene.objects if o.type == "ARMATURE" and o.name not in armatures_before), None)
if rig is None:
    print("RIG_ERROR Rigify did not produce a rig")
    sys.exit(1)

# ---- bind with automatic weights; fall back to envelopes, then to nearest-bone
select_only(mesh, rig)
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
deform = {b.name for b in rig.data.bones if b.use_deform}


def unweighted() -> float:
    idx = {g.index for g in mesh.vertex_groups if g.name in deform}
    bad = sum(1 for v in mesh.data.vertices if not any(g.group in idx and g.weight > 1e-4 for g in v.groups))
    return bad / max(1, len(mesh.data.vertices))


frac = unweighted()
result["weights"] = "automatic"
if frac > 0.02:
    mesh.vertex_groups.clear()
    for m in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
        mesh.modifiers.remove(m)
    select_only(mesh, rig)
    bpy.ops.object.parent_set(type="ARMATURE_ENVELOPE")
    frac = unweighted()
    result["weights"] = "envelope (heat weighting failed)"
result["unweighted_fraction"] = round(frac, 4)
result["deform_bones"] = len(deform)

# ---- simple procedural loops on the control bones
FPS = cfg.get("fps", 24)
scene.render.fps = FPS


def key(pb, path, frame, idx=-1):
    pb.keyframe_insert(path, index=idx, frame=frame)


def make_action(name, frames, setup):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    rig.animation_data_create().action = act
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="POSE")
    for f in range(frames + 1):
        scene.frame_set(f)
        setup(f / frames)
        for pb in rig.pose.bones:
            if pb.name in touched:
                for path in ("location", "rotation_euler"):
                    key(pb, path, f)
    for pb in rig.pose.bones:       # reset to rest
        pb.location, pb.rotation_euler = (0, 0, 0), (0, 0, 0)
    bpy.ops.object.mode_set(mode="OBJECT")


touched = set()
pb = rig.pose.bones
reach = height * 0.12


def use(name):
    b = pb.get(name)
    if b is not None:
        touched.add(name)
        b.rotation_mode = "XYZ"
    return b


def idle(t):
    s = math.sin(2 * math.pi * t)
    if use("torso"):
        pb["torso"].location = (0, 0, s * height * 0.006)
    if use("chest"):
        pb["chest"].rotation_euler = (s * 0.02, 0, 0)
    for side in "LR":
        if use(f"upper_arm_fk.{side}"):
            pb[f"upper_arm_fk.{side}"].rotation_euler = (0, 0, (1 if side == "L" else -1) * s * 0.03)


def walk(t):
    a = 2 * math.pi * t
    if use("torso"):
        pb["torso"].location = (0, 0, abs(math.sin(a)) * height * 0.015)
    for side, ph in (("L", 0.0), ("R", math.pi)):
        foot = use(f"foot_ik.{side}")
        if foot:
            foot.location = (0, math.sin(a + ph) * reach, max(0.0, math.cos(a + ph)) * reach * 0.5)
        arm = use(f"upper_arm_fk.{side}")
        if arm:
            arm.rotation_euler = (math.sin(a + ph + math.pi) * 0.35, 0, 0)


anims = cfg.get("anims") or []
for name in anims:
    fn = {"idle": idle, "walk": walk}.get(name)
    if fn:
        touched.clear()
        make_action(name, cfg.get("frames", 24), fn)
result["anims"] = [a for a in anims if a in ("idle", "walk")]

# ---- preview: front view, bones drawn over the mesh
if cfg.get("preview"):
    # Workbench renders don't draw armatures, so lay a thin red curve along every deform bone, drawn in front.
    curve = bpy.data.curves.new("bones", "CURVE")
    curve.dimensions, curve.bevel_depth = "3D", height * 0.004
    for b in rig.data.bones:
        if b.use_deform:
            sp = curve.splines.new("POLY")
            sp.points.add(1)
            for pt, v in zip(sp.points, (rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local)):
                pt.co = (v.x, v.y, v.z, 1)
    bones_obj = bpy.data.objects.new("bones_preview", curve)
    scene.collection.objects.link(bones_obj)
    bones_obj.color, bones_obj.show_in_front = (0.9, 0.1, 0.1, 1), True
    mesh.color = (0.62, 0.64, 0.7, 1)
    mesh.display_type = "SOLID"
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.render.resolution_x, scene.render.resolution_y = 512, 768
    scene.render.film_transparent = False
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = height * 1.25
    cam.location = (center.x, center.y - 10 * height, center.z + height / 2)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam
    scene.frame_set(0)
    scene.render.filepath = cfg["preview"]
    bpy.ops.render.render(write_still=True)

# ---- export: mesh + deform bones only, one clip per action
select_only(mesh, rig)
export = dict(filepath=cfg["out"], export_format="GLB", use_selection=True, export_def_bones=True, export_apply=False)
if anims:
    export["export_animation_mode"] = "ACTIONS"
else:
    export["export_animations"] = False
bpy.ops.export_scene.gltf(**export)
print("RIG_RESULT " + json.dumps(result))
