"""`um comfy` against a fake ComfyUI: the fake validates every queued workflow the way ComfyUI does (node classes
exist, required inputs present, links point at outputs of the right type) and answers with files.

    uv run --with pytest pytest -q tests
"""
import io
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from um import cli, comfy  # noqa: E402

I, F, S = ["INT", {"default": 1}], ["FLOAT", {"default": 1.0}], ["STRING", {"default": ""}]


def node(out, req, opt=None, cat="x"):
    return {"input": {"required": req, "optional": opt or {}}, "output": out, "category": cat}


SAMPLERS = [["euler", "dpmpp_2m", "dpmpp_sde", "uni_pc", "dpmpp_3m_sde_gpu"]]
SCHEDULERS = [["normal", "karras", "simple", "exponential"]]
INFO = {
    "CheckpointLoaderSimple": node(["MODEL", "CLIP", "VAE"], {"ckpt_name": [["sd_xl_base_1.0.safetensors", "stable-audio-open-1.0.safetensors",
                                                                             "ace_step_v1_3.5b.safetensors"]]}),
    "LoraLoader": node(["MODEL", "CLIP"], {"model": ["MODEL"], "clip": ["CLIP"], "lora_name": [["pixelart_xl.safetensors"]],
                                           "strength_model": F, "strength_clip": F}),
    "CLIPTextEncode": node(["CONDITIONING"], {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}),
    "EmptyLatentImage": node(["LATENT"], {"width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}], "batch_size": I}),
    "KSampler": node(["LATENT"], {"model": ["MODEL"], "seed": I, "steps": I, "cfg": F, "sampler_name": SAMPLERS, "scheduler": SCHEDULERS,
                                  "positive": ["CONDITIONING"], "negative": ["CONDITIONING"], "latent_image": ["LATENT"], "denoise": F}),
    "VAEDecode": node(["IMAGE"], {"samples": ["LATENT"], "vae": ["VAE"]}),
    "VAEEncode": node(["LATENT"], {"pixels": ["IMAGE"], "vae": ["VAE"]}),
    "SaveImage": node([], {"images": ["IMAGE"], "filename_prefix": ["STRING", {"default": "ComfyUI"}]}),
    "LoadImage": node(["IMAGE", "MASK"], {"image": [["example.png"], {"image_upload": True}]}),
    "ImageScaleToTotalPixels": node(["IMAGE"], {"image": ["IMAGE"], "upscale_method": [["nearest-exact", "lanczos"]], "megapixels": F}),
    "RepeatLatentBatch": node(["LATENT"], {"samples": ["LATENT"], "amount": I}),
    "MaskToImage": node(["IMAGE"], {"mask": ["MASK"]}),
    "RMBG": node(["IMAGE", "MASK", "IMAGE"], {"image": ["IMAGE"], "model": [["RMBG-2.0", "BiRefNet"]]},
                 {"sensitivity": F}),
    "UpscaleModelLoader": node(["UPSCALE_MODEL"], {"model_name": [["4x-UltraSharp.pth"]]}),
    "ImageUpscaleWithModel": node(["IMAGE"], {"upscale_model": ["UPSCALE_MODEL"], "image": ["IMAGE"]}),
    "ImageScaleBy": node(["IMAGE"], {"image": ["IMAGE"], "upscale_method": [["nearest-exact", "lanczos"]], "scale_by": F}),
    "CLIPLoader": node(["CLIP"], {"clip_name": [["t5_base.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"]],
                                  "type": [["stable_diffusion", "stable_audio", "wan"]]}),
    "EmptyLatentAudio": node(["LATENT"], {"seconds": F, "batch_size": I}),
    "VAEDecodeAudio": node(["AUDIO"], {"samples": ["LATENT"], "vae": ["VAE"]}),
    "SaveAudio": node([], {"audio": ["AUDIO"], "filename_prefix": S}),
    "LoadAudio": node(["AUDIO"], {"audio": [["a.wav"]]}),
    "TextEncodeAceStepAudio": node(["CONDITIONING"], {"clip": ["CLIP"], "tags": S, "lyrics": S, "lyrics_strength": F}),
    "ConditioningZeroOut": node(["CONDITIONING"], {"conditioning": ["CONDITIONING"]}),
    "EmptyAceStepLatentAudio": node(["LATENT"], {"seconds": F, "batch_size": I}),
    "ModelSamplingSD3": node(["MODEL"], {"model": ["MODEL"], "shift": F}),
    "ModelSamplingAuraFlow": node(["MODEL"], {"model": ["MODEL"], "shift": F}),
    "ImageOnlyCheckpointLoader": node(["MODEL", "CLIP_VISION", "VAE"], {"ckpt_name": [["hunyuan3d-dit-v2.safetensors"]]}),
    "CLIPVisionEncode": node(["CLIP_VISION_OUTPUT"], {"clip_vision": ["CLIP_VISION"], "image": ["IMAGE"], "crop": [["center", "none"]]}),
    "Hunyuan3Dv2Conditioning": node(["CONDITIONING", "CONDITIONING"], {"clip_vision_output": ["CLIP_VISION_OUTPUT"]}),
    "EmptyLatentHunyuan3Dv2": node(["LATENT"], {"resolution": I, "batch_size": I}),
    "VAEDecodeHunyuan3D": node(["VOXEL"], {"samples": ["LATENT"], "vae": ["VAE"], "num_chunks": I, "octree_resolution": I}),
    "VoxelToMesh": node(["MESH"], {"voxel": ["VOXEL"], "algorithm": [["surface net", "basic"]], "threshold": F}),
    "SaveGLB": node([], {"mesh": ["MESH"], "filename_prefix": S}),
    "UNETLoader": node(["MODEL"], {"unet_name": [["wan2.2_ti2v_5B_fp16.safetensors"]], "weight_dtype": [["default", "fp8_e4m3fn"]]}),
    "VAELoader": node(["VAE"], {"vae_name": [["wan2.2_vae.safetensors"]]}),
    "Wan22ImageToVideoLatent": node(["LATENT"], {"vae": ["VAE"], "width": I, "height": I, "length": I, "batch_size": I},
                                    {"start_image": ["IMAGE"]}),
    "CreateVideo": node(["VIDEO"], {"images": ["IMAGE"], "fps": F}, {"audio": ["AUDIO"]}),
    "SaveVideo": node([], {"video": ["VIDEO"], "filename_prefix": S, "format": [["auto", "mp4"]], "codec": [["auto", "h264"]]}),
    "ChatterboxTTS": node(["AUDIO"], {"text": ["STRING", {"multiline": True}], "seed": I}, {"audio_prompt": ["AUDIO"]}, cat="audio/tts"),
}


def png(kind: str) -> bytes:
    if kind == "mask":   # white subject on black
        im = Image.new("RGB", (64, 64), (0, 0, 0))
        ImageDraw.Draw(im).rectangle((16, 16, 47, 47), fill=(255, 255, 255))
    else:                # red subject on a flat white background
        im = Image.new("RGB", (64, 64), (255, 255, 255))
        ImageDraw.Draw(im).rectangle((20, 12, 43, 51), fill=(200, 30, 30))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


SAVE = {"SaveImage": ("images", ".png"), "SaveAudio": ("audio", ".flac"), "SaveGLB": ("3d", ".glb"), "SaveVideo": ("images", ".mp4")}


def validate(wf: dict) -> list[str]:
    errs = []
    for nid, n in wf.items():
        spec = INFO.get(n["class_type"])
        if not spec:
            errs.append(f"{nid}: unknown node {n['class_type']}")
            continue
        for k, s in spec["input"]["required"].items():
            if k not in n["inputs"]:
                errs.append(f"{nid} {n['class_type']}: missing {k}")
        allspec = {**spec["input"]["required"], **spec["input"]["optional"]}
        for k, v in n["inputs"].items():
            if k not in allspec:
                errs.append(f"{nid} {n['class_type']}: unknown input {k}")
            elif isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                src = wf.get(v[0])
                if not src:
                    errs.append(f"{nid}.{k}: link to missing node {v[0]}")
                    continue
                outs = INFO[src["class_type"]]["output"]
                if v[1] >= len(outs) or outs[v[1]] != allspec[k][0]:
                    errs.append(f"{nid}.{k}: wants {allspec[k][0]}, got {outs[v[1]] if v[1] < len(outs) else '?'}")
    return errs


class Fake:
    def __init__(self):
        self.prompts, self.files, self.uploads = {}, {}, {}
        self.drop = False        # True: accept prompts but never list them (a ComfyUI that lost the job)
        self.log = []

    def history(self, pid):
        wf = self.prompts[pid]
        outs = {}
        for nid, n in wf.items():
            if n["class_type"] in SAVE:
                key, ext = SAVE[n["class_type"]]
                fn = f"{n['inputs']['filename_prefix'].split('/')[-1]}_00001_{ext}"
                src = wf[n["inputs"][next(iter(INFO[n['class_type']]['input']['required']))][0]]["class_type"]
                self.files[fn] = png("mask" if src == "MaskToImage" else "img") if ext == ".png" else b"fake" + ext.encode()
                outs[nid] = {key: [{"filename": fn, "subfolder": "um", "type": "output"}]}
        return {pid: {"outputs": outs, "status": {"status_str": "success", "completed": True, "messages": []}}}


@pytest.fixture
def fake(monkeypatch):
    state = Fake()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, obj, code=200, raw=None):
            body = raw if raw is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/system_stats":
                return self.send({"system": {"comfyui_version": "fake", "python_version": "3.12"}, "devices": []})
            if self.path == "/object_info":
                return self.send(INFO)
            if self.path == "/queue":
                return self.send({"queue_running": [], "queue_pending": []})
            if self.path.startswith("/history/"):
                return self.send({} if state.drop else state.history(self.path.split("/")[-1]))
            if self.path == "/internal/logs/raw":
                return self.send({"entries": state.log, "size": {}})
            if self.path.startswith("/view?"):
                fn = re.search(r"filename=([^&]+)", self.path).group(1)
                return self.send(None, raw=state.files[fn])
            self.send({}, 404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
                state.uploads[name] = body
                return self.send({"name": name, "subfolder": "", "type": "input"})
            if self.path == "/prompt":
                wf = json.loads(body)["prompt"]
                errs = validate(wf)
                if errs:
                    return self.send({"error": {"message": "invalid prompt", "details": "; ".join(errs)}, "node_errors": {}}, 400)
                pid = f"p{len(state.prompts) + 1}"
                state.prompts[pid] = wf
                return self.send({"prompt_id": pid, "number": 1, "node_errors": {}})
            self.send({}, 404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(comfy, "_BASE", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(comfy, "_INFO", None)
    yield state
    srv.shutdown()


def um(*argv):
    cli.main(list(map(str, argv)))


def sprite_png(path: Path) -> Path:
    path.write_bytes(png("img"))
    return path


# --------------------------------------------------------------------------- recipes end to end

def test_image_and_lora(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    um("comfy", "image", "castle key art", "--aspect", "16:9", "--lora", "pixelart:0.7", "--seed", "7", "--out", tmp_path)
    wf = next(iter(fake.prompts.values()))
    classes = [n["class_type"] for n in wf.values()]
    assert classes.count("LoraLoader") == 1 and "SaveImage" in classes
    lat = next(n for n in wf.values() if n["class_type"] == "EmptyLatentImage")["inputs"]
    assert lat["width"] > lat["height"] and lat["width"] % 64 == 0
    ks = next(n for n in wf.values() if n["class_type"] == "KSampler")["inputs"]
    assert ks["seed"] == 7 and ks["sampler_name"] == "dpmpp_2m"
    assert (tmp_path / "castle_key_art.png").exists()
    rec = json.loads((tmp_path / "comfy_manifest.jsonl").read_text().splitlines()[-1])
    assert rec["models"] == ["pixelart_xl.safetensors", "sd_xl_base_1.0.safetensors"] and rec["seed"] == 7


def test_sprite_is_cut_out(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    um("comfy", "sprite", "red robot", "--name", "bot", "--out", tmp_path)
    im = Image.open(tmp_path / "bot.png")
    assert im.mode == "RGBA" and im.size == (24, 40) and (tmp_path / "bot_raw.png").exists()


def test_rmbg_with_node_and_fallback(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    src = sprite_png(tmp_path / "in.png")
    um("comfy", "rmbg", src, "--out", tmp_path / "a")
    cut = Image.open(tmp_path / "a" / "in_cut.png")
    assert cut.size == (32, 32)   # the fake node's mask, not the red rectangle
    del INFO["RMBG"]
    try:
        comfy._INFO = None
        um("comfy", "rmbg", src, "--out", tmp_path / "b")
        assert Image.open(tmp_path / "b" / "in_cut.png").size == (24, 40)   # local flood fill
    finally:
        INFO["RMBG"] = node(["IMAGE", "MASK", "IMAGE"], {"image": ["IMAGE"], "model": [["RMBG-2.0", "BiRefNet"]]}, {"sensitivity": F})


@pytest.mark.parametrize("argv", [
    ["edit", "same robot, waving", "--ref", "{src}", "--n", "2"],
    ["upscale", "{src}", "--factor", "2"],
    ["texture", "mossy cobblestone"],
    ["pbr", "rusted metal", "--maps", "basecolor,normal,roughness,metalness,height,ao"],
    ["model3d", "{src}"],
    ["sfx", "laser shot", "--seconds", "1.5"],
    ["music", "boss battle chiptune", "--seconds", "30"],
    ["voice", "You dare?", "--ref", "{wav}"],
    ["video", "{src}", "camera orbits", "--seconds", "3"],
])
def test_recipe_builds_valid_workflow(fake, tmp_path, monkeypatch, argv):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    src = sprite_png(tmp_path / "in.png")
    (tmp_path / "v.wav").write_bytes(b"RIFF....WAVE")
    um("comfy", *[a.format(src=src, wav=tmp_path / "v.wav") for a in argv], "--out", tmp_path / "out")
    assert len(fake.prompts) == 1
    rec = json.loads((tmp_path / "out" / "comfy_manifest.jsonl").read_text())
    assert rec["files"] and all(Path(f).exists() for f in rec["files"])
    if argv[0] == "pbr":
        assert {Path(f).stem.split("_")[-1] for f in rec["files"]} >= {"basecolor", "normal", "roughness", "metalness", "height", "ao"}
    if argv[0] == "video":
        lat = next(n for n in rec["workflow"].values() if n["class_type"] == "Wan22ImageToVideoLatent")["inputs"]
        assert lat["length"] == 73 and lat["width"] % 32 == 0
    if argv[0] == "music":
        assert "TextEncodeAceStepAudio" in {n["class_type"] for n in rec["workflow"].values()}


def test_user_workflow_and_run(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    wf = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
          "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}, game art", "clip": ["1", 1]}},
          "3": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": 512, "batch_size": 1}},
          "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": "{{seed}}", "steps": 20, "cfg": 7.0, "sampler_name": "euler",
                                                     "scheduler": "normal", "positive": ["2", 0], "negative": ["2", 0],
                                                     "latent_image": ["3", 0], "denoise": 1.0}},
          "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
          "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "{{prefix}}"}}}
    p = tmp_path / "wf_api.json"
    p.write_text(json.dumps(wf))
    um("comfy", "run", p, "prompt=a goblin", "width:=768", "KSampler.steps:=33", "--out", tmp_path / "o")
    sent = fake.prompts["p1"]
    assert sent["2"]["inputs"]["text"] == "a goblin, game art" and sent["3"]["inputs"]["width"] == 768
    assert sent["4"]["inputs"]["steps"] == 33 and isinstance(sent["4"]["inputs"]["seed"], int)
    # the same file as a recipe override: {{width}} comes from the recipe
    um("comfy", "image", "a goblin", "--workflow", p, "--seed", "5", "--out", tmp_path / "o")
    assert fake.prompts["p2"]["4"]["inputs"]["seed"] == 5 and fake.prompts["p2"]["3"]["inputs"]["width"] == 1024


def test_rig_needs_workflow(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    (tmp_path / "m.glb").write_bytes(b"glTF")
    with pytest.raises(SystemExit):
        um("comfy", "rig", tmp_path / "m.glb", "--out", tmp_path)
    assert "no built-in auto-rigger" in capsys.readouterr().err


def test_check_reports_every_recipe(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    um("comfy", "check")
    out = capsys.readouterr().out
    for r in ("image", "sprite", "edit", "rmbg", "upscale", "texture", "pbr", "model3d", "sfx", "music", "voice", "video"):
        assert re.search(rf"^  {r}\s+ok", out, re.M), (r, out)
    assert re.search(r"^  rig\s+missing", out, re.M) and "ChatterboxTTS" in out


# --------------------------------------------------------------------------- offline pieces

def test_graph_defaults_links_and_combos():
    g = comfy.Graph(INFO)
    ck = g.add("CheckpointLoaderSimple", ckpt_name="sd_xl_base_1.0.safetensors")
    enc = g.add("CLIPTextEncode", ck["CLIP"], text="x")
    assert enc.g.nodes[enc.nid]["inputs"] == {"text": "x", "clip": ["1", 1]}
    lat = g.add("EmptyLatentImage")
    assert g.nodes[lat.nid]["inputs"] == {"width": 512, "height": 512, "batch_size": 1}
    with pytest.raises(comfy.Missing):
        g.add("CheckpointLoaderSimple", ckpt_name="nope.safetensors")
    with pytest.raises(comfy.Missing):
        g.add("VAEDecode", enc["CONDITIONING"])
    assert g.pick("CheckpointLoaderSimple", "ckpt_name", None, comfy.IMAGE_PREFER, comfy.IMAGE_EXCLUDE) == "sd_xl_base_1.0.safetensors"
    assert g.pick("CheckpointLoaderSimple", "ckpt_name", "ace", what="x") == "ace_step_v1_3.5b.safetensors"
    assert comfy.tts_nodes(INFO) == ["ChatterboxTTS"]


def test_fill_placeholders():
    miss = set()
    out = comfy.fill({"a": "{{seed}}", "b": "x {{prompt}} y", "c": ["{{nope}}"]}, {"seed": 3, "prompt": "cat"}, miss)
    assert out == {"a": 3, "b": "x cat y", "c": ["{{nope}}"]} and miss == {"nope"}


def test_pbr_maps_tile():
    import numpy as np
    rng = np.random.default_rng(0)
    im = Image.fromarray((rng.random((64, 64, 3)) * 255).astype(np.uint8), "RGB")
    maps = comfy.pbr_maps(im, ("normal", "height", "roughness", "ao", "metalness"), metal=1.0)
    n = np.asarray(maps["normal"]).astype(int)
    assert n.shape == (64, 64, 3) and n[..., 2].min() > 127          # normals point out of the surface
    assert np.asarray(maps["metalness"]).min() == 255
    # wrap-around blur: a tile's opposite edges stay continuous
    h = np.asarray(maps["height"]).astype(int)
    assert abs(h[:, 0] - h[:, -1]).mean() < 80


def test_apply_mask_inverts_background_white_masks():
    src = Image.new("RGBA", (40, 40), (10, 200, 10, 255))
    m = Image.new("L", (40, 40), 255)
    ImageDraw.Draw(m).rectangle((10, 10, 29, 29), fill=0)   # subject black, background white
    assert comfy.apply_mask(src, m).size == (20, 20)


def test_publish_wants_comfy_models_credited(tmp_path, capsys):
    from um import publish
    mod = tmp_path / "mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "comfy_manifest.jsonl").write_text(json.dumps({"models": ["sd_xl_base_1.0.safetensors"]}) + "\n")
    (mod / "README.md").write_text("My mod")
    publish.check(str(mod))
    assert "sd_xl_base_1.0.safetensors" in capsys.readouterr().out
    (mod / "README.md").write_text("Art made locally with ComfyUI and SDXL (sd_xl_base_1.0).")
    publish.check(str(mod))
    assert "ComfyUI-generated" not in capsys.readouterr().out


def test_dynamic_combo_and_union_link_types():
    info = {**INFO, "SaveVideo": node([], {"video": ["VIDEO"], "filename_prefix": S, "format": ["COMFY_DYNAMICCOMBO_V3", {"options": [
        {"key": "auto", "inputs": {"required": {"codec": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "auto", "inputs": {"required": {}}}]}]}}},
        {"key": "mp4", "inputs": {"required": {}}}]}]}),
        "SaveGLB": node([], {"mesh": ["MESH,FILE_3D_GLB,FILE_3D", {}], "filename_prefix": S})}
    g = comfy.Graph(info)
    mesh = g.add("VoxelToMesh", g.add("VAEDecodeHunyuan3D", g.add("KSampler", g.add("CheckpointLoaderSimple")["MODEL"],
                 g.add("CLIPTextEncode", g.add("CheckpointLoaderSimple")["CLIP"])["CONDITIONING"],
                 g.add("CLIPTextEncode", g.add("CheckpointLoaderSimple")["CLIP"])["CONDITIONING"],
                 g.add("EmptyLatentImage")["LATENT"])["LATENT"], g.add("CheckpointLoaderSimple")["VAE"])[0])
    g.add("SaveGLB", mesh[0])
    vid = g.add("CreateVideo", g.add("VAEDecode", g.add("EmptyLatentImage")["LATENT"], g.add("CheckpointLoaderSimple")["VAE"])["IMAGE"])
    sv = g.add("SaveVideo", vid[0])
    assert g.nodes[sv.nid]["inputs"]["format"] == "auto" and g.nodes[sv.nid]["inputs"]["format.codec"] == "auto"


def test_voice_through_unified_tts_engine():
    info = {**INFO,
            "ChatterBoxEngineNode": node(["TTS_ENGINE"], {"language": [["English"]], "device": [["auto", "cuda"]], "temperature": F}),
            "F5TTSEngineNode": node(["TTS_ENGINE"], {"device": [["auto"]]}),
            "ChatterBoxVoiceCapture": node(["AUDIO"], {"text": S}, cat="tts/capture"),
            "UnifiedTTSTextNode": node(["AUDIO", "STRING"], {"TTS_engine": ["TTS_ENGINE"], "text": S, "narrator_voice": [["none", "a.wav"]], "seed": I},
                                       {"opt_narrator": ["*"]})}
    assert comfy.tts_engines(info) == ["ChatterBoxEngineNode", "F5TTSEngineNode"]
    assert "ChatterBoxVoiceCapture" not in comfy.tts_nodes(info)
    g = comfy.Graph(info, {"ref.wav"})
    comfy.b_voice(g, dict(text="Hello", seed=3, audio="ref.wav", prefix="um/x"))
    t = next(n for n in g.nodes.values() if n["class_type"] == "UnifiedTTSTextNode")["inputs"]
    assert t["text"] == "Hello" and t["narrator_voice"] == "none" and t["TTS_engine"] == ["1", 0] and t["opt_narrator"][0] == "2"
    assert comfy.engines_in(g.nodes) == ["ChatterBoxEngineNode"]


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_is_silent(tmp_path):
    import subprocess
    quiet, tone = tmp_path / "q.wav", tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1", str(quiet)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", str(tone)], check=True)
    assert comfy.is_silent(str(quiet)) and not comfy.is_silent(str(tone))


def test_voice_defaults_for_engines_that_need_a_reference():
    info = {**INFO,
            "F5TTSEngineNode": node(["TTS_ENGINE"], {"device": [["auto"]]}),
            "ChatterBoxEngineNode": node(["TTS_ENGINE"], {"device": [["auto"]]}),
            "UnifiedTTSTextNode": node(["AUDIO", "STRING"], {"TTS_engine": ["TTS_ENGINE"], "text": S, "seed": I,
                                                             "narrator_voice": [["none", "voices_examples/male/male_01.wav", "voices_examples/female/female_02.wav"]]})}

    def voice(**kw):
        g = comfy.Graph(info)
        comfy.b_voice(g, dict(text="Hi", seed=1, prefix="um/x", **kw))
        return next(n for n in g.nodes.values() if n["class_type"] == "UnifiedTTSTextNode")["inputs"]["narrator_voice"]

    assert voice() == "none"                                              # ChatterBox speaks without a reference
    assert voice(engine="F5TTSEngineNode") == "voices_examples/male/male_01.wav"
    assert voice(voice="female_02") == "voices_examples/female/female_02.wav"
    with pytest.raises(comfy.Missing):
        voice(voice="nobody")


# --------------------------------------------------------------------------- review fixes

def test_wait_gives_up_on_a_lost_prompt(fake, monkeypatch):
    fake.drop = True
    monkeypatch.setattr(comfy, "GONE_POLLS", 3)
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit):
        comfy.wait("p1", timeout=3600, quiet=True)


def test_server_log_is_scoped_to_one_run(fake):
    fake.log = [{"t": "2026-10-01T10:00:00", "m": "\x1b[31mERROR old run\x1b[0m\n"}]
    mark = comfy.log_mark()
    fake.log.append({"t": "2026-10-01T10:05:00", "m": "ERROR this run\n"})
    assert comfy.server_log(10, since=mark) == "ERROR this run"
    assert "old run" in comfy.server_log(10) and "\x1b" not in comfy.server_log(10)


def test_sprite_name_with_raw_inside(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    um("comfy", "sprite", "dire wolf", "--name", "dire_raw_wolf", "--out", tmp_path)
    assert (tmp_path / "dire_raw_wolf.png").exists() and (tmp_path / "dire_raw_wolf_raw.png").exists()
    assert not (tmp_path / "dire_wolf_raw.png").exists()


def test_transparent_input_leaves_no_temp_files(fake, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    tmp = tmp_path / "tmpdir"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    src = tmp_path / "sprite.png"
    im = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((8, 8, 23, 23), fill=(200, 30, 30, 255))
    im.save(src)
    um("comfy", "edit", "same, waving", "--ref", src, "--out", tmp_path / "out")
    assert list(tmp.iterdir()) == [] and not (tmp_path / "out" / ".comfy_tmp").exists()
    assert any("_flat" in name for name in fake.uploads)     # the flattened copy is what ComfyUI received


def test_missing_model_fails_before_anything_is_uploaded(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("UM_HOME", str(tmp_path / "home"))
    (tmp_path / "m.glb").write_bytes(b"glTF" * 1000)
    with pytest.raises(SystemExit):
        um("comfy", "rig", tmp_path / "m.glb", "--out", tmp_path)
    assert fake.uploads == {} and fake.prompts == {}


def test_default_workflow_lookup_does_not_create_the_data_folder(tmp_path, monkeypatch):
    home = tmp_path / "nope"
    monkeypatch.setenv("UM_HOME", str(home))
    assert comfy.user_workflow("image", None) is None and not home.exists()


def test_shared_helpers():
    from um import common
    assert common.parse_kv(["a=b", "n:=2"]) == {"a": "b", "n": 2}
    assert common._version_key(Path("C:/Blender Foundation/Blender 10.0/blender.exe")) > common._version_key(
        Path("C:/Blender Foundation/Blender 5.2/blender.exe"))


def test_dotenv_value(tmp_path, monkeypatch):
    from um import common
    (tmp_path / ".env").write_text("OTHER=1\nCOMFY_URL = 'http://127.0.0.1:9999'\n")
    monkeypatch.chdir(tmp_path)
    assert common.dotenv_value("COMFY_URL") == "http://127.0.0.1:9999" and common.dotenv_value("NOPE") is None


def test_publish_credit_matching(tmp_path, capsys):
    from um import publish
    assert publish.credit_keys("sd_xl_base_1.0.safetensors") == ["sdxlbase10"]
    assert "wan22ti2v5b" in publish.credit_keys("wan2.2_ti2v_5B_fp16.safetensors")
    assert publish.credit_keys("ChatterBoxEngineNode", engine=True) == ["chatterbox"]
    mod = tmp_path / "mod"
    mod.mkdir()
    rec = {"models": ["sd_xl_base_1.0.safetensors", "wan2.2_ti2v_5B_fp16.safetensors"], "engines": ["ChatterBoxEngineNode", "F5TTSEngineNode"]}
    (mod / "comfy_manifest.jsonl").write_text("not json\n" + json.dumps(rec) + "\n[1, 2]\n")
    (mod / "README.md").write_text("Art: SDXL base 1.0. Video: Wan 2.2 TI2V 5B. Voices: ChatterBox.")
    publish.check(str(mod))
    out = capsys.readouterr().out
    assert "F5TTSEngineNode" in out and "sd_xl_base" not in out and "ChatterBoxEngineNode" not in out and "wan2.2" not in out
    assert "line 1 is not valid JSON" in out and "line 3 is not valid JSON" in out
