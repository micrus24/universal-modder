"""Game assets from a local ComfyUI (https://github.com/comfyanonymous/ComfyUI): your GPU, no API key, no cloud.
Talks to ComfyUI's HTTP API. The server is found at COMFY_URL (env or .env), else http://127.0.0.1:8188
(ComfyUI from source / portable), else http://127.0.0.1:8000 (ComfyUI Desktop). --url overrides.

    um comfy check                            # is ComfyUI up, which recipes can run, what's missing
    um comfy models                           # model files ComfyUI can see, per folder
    um comfy image "key art: ..." --aspect 16:9
    um comfy sprite "a rusty scrap drone enemy, side view facing left, 16-bit pixel art" --name drone
    um comfy edit "same drone, rotors blurred" --ref assets/gen/drone.png --strength 0.5
    um comfy rmbg in.png                      # needs a background-removal node pack (ComfyUI-RMBG, ...)
    um comfy pixelate in.png --colors 24      # local, no ComfyUI needed
    um comfy upscale in.png --factor 2        # any ESRGAN-style model in models/upscale_models
    um comfy texture "mossy cobblestone"      # generated, then made seamless locally
    um comfy pbr "rusted sheet metal"         # texture + basecolor/normal/roughness/metalness/height/ao maps
    um comfy model3d concept.png              # Hunyuan3D 2 -> GLB (shape only, untextured)
    um comfy rig character.glb --workflow unirig_api.json   # no built-in rigger: bring a workflow
    um comfy sfx "laser rifle shot, sci-fi, punchy" --seconds 1.5   # Stable Audio Open
    um comfy music "tense boss battle, chiptune, 140 bpm" --seconds 60   # ACE-Step (falls back to Stable Audio)
    um comfy voice "You dare challenge the Mothership?" --ref narrator.wav   # any installed TTS node pack
    um comfy video still.png "camera orbits the boss as it powers up"   # Wan 2.2 TI2V 5B
    um comfy run my_workflow_api.json prompt="a cat" KSampler.steps:=40 image=@ref.png   # anything else

Recipes build their workflow from what your ComfyUI reports (/object_info): node inputs you don't set get
the node's defaults, and model files are picked by name (override with --ckpt, COMFY_CKPT or `--set`).
`um comfy check` says which recipes can run and which nodes or model files are missing.

Your own workflows: in ComfyUI use Workflow -> Export (API), put placeholders like "{{prompt}}" in the JSON
and pass it with --workflow (or save it as $UM_HOME/comfy_workflows/<recipe>.json to make it the default).
Placeholders: {{prompt}} {{negative}} {{seed}} {{steps}} {{cfg}} {{width}} {{height}} {{n}} {{seconds}}
{{text}} {{image}} {{model}} {{audio}} {{name}} {{prefix}}. `--set Class.input=value` (or id.input) and
`:=` for JSON values patch any node.

Every call appends a line to <out>/comfy_manifest.jsonl (recipe, models, seed, prompt id, files and the
full API workflow) so an asset can be traced and regenerated with `um comfy run`.
"""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from um.common import asset_name as _name, data_dir, die, dotenv_value, need, parse_kv as _kv, parse_size
from um.fal import SPRITE_STYLE

DEFAULT_URLS = ("http://127.0.0.1:8188", "http://127.0.0.1:8000")
MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft", ".onnx")
WIDGETS = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COMFY_DYNAMICCOMBO_V3"}
# combo inputs where an unknown value falls back to the node's default instead of failing
SOFT = {"sampler_name", "scheduler", "upscale_method", "crop", "algorithm", "weight_dtype", "format", "codec"}

IMAGE_PREFER = ("juggernaut", "sd_xl_base", "sdxl", "xl", "flux", "")
IMAGE_EXCLUDE = ("audio", "hunyuan3d", "ace_step", "svd", "wan", "ltx", "hunyuan_video", "mochi", "cosmos",
                 "refiner", "inpaint", "kontext", "qwen", "stable_cascade")
RMBG_NODES = ("RMBG", "BiRefNetRMBG", "InspyrenetRembg", "BRIA_RMBG_Zho", "Image Rembg (mtb)",
              "Image Remove Background (rembg)")
TTS_PREFER = ("chatterbox", "f5", "indextts", "index_tts", "kokoro", "xtts", "cosyvoice", "sparktts")
ENGINE_PREFER = ("ChatterBoxEngineNode", "F5TTSEngineNode", "HiggsAudioV3EngineNode", "IndexTTSEngineNode",
                 "CosyVoiceEngineNode", "VibeVoiceEngineNode", "Qwen3TTSEngineNode")
NEEDS_REF_VOICE = ("F5TTSEngineNode", "IndexTTSEngineNode", "CosyVoiceEngineNode", "VibeVoiceEngineNode")
TTS_SKIP = re.compile(r"capture|analy[sz]|edit|design|character|effects|srt|asr|rvc|convert", re.I)

DOCS = {
    "image": "an SDXL or Flux checkpoint in ComfyUI/models/checkpoints (e.g. sd_xl_base_1.0.safetensors from "
             "huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)",
    "rmbg": "a background-removal node pack: ComfyUI Manager -> Custom Nodes -> search 'ComfyUI-RMBG'",
    "upscale": "an upscale model in ComfyUI/models/upscale_models (e.g. 4x-UltraSharp.pth or RealESRGAN_x4plus.pth)",
    "model3d": "Hunyuan3D 2 for ComfyUI: https://docs.comfy.org/tutorials/3d/hunyuan3D-2 (checkpoint in models/checkpoints)",
    "sfx": "Stable Audio Open: stable-audio-open-1.0.safetensors in models/checkpoints and t5_base.safetensors in "
           "models/text_encoders (https://comfyanonymous.github.io/ComfyUI_examples/audio/)",
    "music": "ACE-Step: https://docs.comfy.org/tutorials/audio/ace-step/ace-step-v1 (ace_step_v1_3.5b.safetensors "
             "in models/checkpoints)",
    "voice": "a TTS node pack from ComfyUI Manager (e.g. ComfyUI-Chatterbox, ComfyUI-F5-TTS)",
    "video": "Wan 2.2 TI2V 5B: https://docs.comfy.org/tutorials/video/wan/wan2_2 (wan2.2_ti2v_5B_fp16 in "
             "models/diffusion_models, umt5_xxl in models/text_encoders, wan2.2_vae in models/vae)",
    "rig": "ComfyUI has no built-in auto-rigger. For humanoids use `um rig model.glb out.glb` (Blender Rigify); otherwise "
           "export a rigging workflow (e.g. a UniRig node pack) as API JSON with a \"{{model}}\" placeholder and pass --workflow",
}


class Missing(Exception):
    """A node, model file or input the recipe needs isn't available in this ComfyUI."""


# --------------------------------------------------------------------------- http

_BASE: str | None = None
_INFO: dict | None = None


def base_url() -> str:
    global _BASE
    if _BASE:
        return _BASE
    explicit = os.environ.get("COMFY_URL") or dotenv_value("COMFY_URL")
    for url in ([explicit] if explicit else DEFAULT_URLS):
        url = url.rstrip("/")
        try:
            with urllib.request.urlopen(url + "/system_stats", timeout=3) as r:
                json.loads(r.read())
            _BASE = url
            return url
        except Exception:
            continue
    die(f"can't reach ComfyUI at {explicit or ' or '.join(DEFAULT_URLS)}. Start it (python main.py, the portable "
        "run_nvidia_gpu.bat or ComfyUI Desktop) or set COMFY_URL=http://host:port")


def _prompt_error(j: dict) -> str:
    err = j.get("error") or {}
    lines = [f"ComfyUI rejected the workflow: {err.get('message', '')} {err.get('details', '')}".rstrip()]
    for nid, ne in (j.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            lines.append(f"  node {nid} ({ne.get('class_type')}): {e.get('message', '')} {e.get('details', '')}".rstrip())
    return "\n".join(lines)


def _req(method: str, path: str, body=None, headers=None, raw=False, timeout=120):
    h = {"Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
    url = base_url() + path
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                return payload if raw else (json.loads(payload) if payload else {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if path == "/prompt" and e.code == 400:
                try:
                    die(_prompt_error(json.loads(detail)))
                except json.JSONDecodeError:
                    pass
            if e.code in (500, 502, 503) and attempt < 2:
                time.sleep(2)
                continue
            die(f"ComfyUI {method} {path.split('?')[0]} -> HTTP {e.code}: {detail[:1500]}")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2)
                continue
            die(f"ComfyUI {method} {url}: {e.reason}")


def object_info() -> dict:
    global _INFO
    if _INFO is None:
        _INFO = _req("GET", "/object_info", timeout=300)
    return _INFO


def upload(path: str | Path) -> str:
    """Local file -> ComfyUI's input folder. Returns the name LoadImage / LoadAudio expect."""
    p = Path(path)
    if not p.is_file():
        die(f"no such file: {p}")
    data = p.read_bytes()
    fname = f"um_{hashlib.sha1(data).hexdigest()[:10]}_{re.sub(r'[^A-Za-z0-9._-]', '_', p.name)}"
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    b = "----um" + uuid.uuid4().hex
    body = b"".join([
        f'--{b}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="image"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode(),
        data, f"\r\n--{b}--\r\n".encode()])
    r = _req("POST", "/upload/image", body, headers={"Content-Type": f"multipart/form-data; boundary={b}"}, timeout=600)
    return (r["subfolder"] + "/" if r.get("subfolder") else "") + r["name"]


# --------------------------------------------------------------------------- graph builder


class Out:
    """One output socket of a node: [node_id, index] plus its type."""

    def __init__(self, nid: str, idx: int, typ: str):
        self.ref, self.type = [nid, idx], typ


class Node:
    def __init__(self, g: "Graph", nid: str, cls: str):
        self.g, self.nid, self.cls = g, nid, cls
        self.types = list(g.info[cls].get("output") or [])

    def __getitem__(self, k) -> Out:
        if isinstance(k, int):
            return Out(self.nid, k, self.types[k])
        if k not in self.types:
            raise Missing(f"{self.cls} has no {k} output (it has {self.types})")
        return Out(self.nid, self.types.index(k), k)


def _choices(spec) -> list | None:
    if not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options") or []
    return None


def _default(spec):
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    ch = _choices(spec)
    if ch is not None:
        if "default" in opts:
            return opts["default"]
        if not ch:
            raise Missing("empty choice list")
        return ch[0]
    if "default" in opts:
        return opts["default"]
    return {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(spec[0])


def _dynamic(inputs: dict, key: str, spec, chosen=None) -> None:
    """COMFY_DYNAMICCOMBO_V3 (SaveVideo.format, ...): the API wants `key: option` plus `key.sub: ...` for the
    option's own inputs, recursively. Defaults to the first option."""
    opts = (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}).get("options") or []
    if not opts:
        raise Missing(f"{key}: empty option list")
    pick = next((o for o in opts if chosen is not None and o["key"] == chosen), opts[0])
    inputs[key] = pick["key"]
    for sub, ss in ((pick.get("inputs") or {}).get("required") or {}).items():
        if ss and ss[0] == "COMFY_DYNAMICCOMBO_V3":
            _dynamic(inputs, f"{key}.{sub}", ss)
        else:
            inputs[f"{key}.{sub}"] = _default(ss)


def _is_link(spec) -> bool:
    t = spec[0] if spec else None
    return isinstance(t, str) and t not in WIDGETS and (t.isupper() or t == "*")


class Graph:
    """Builds an API-format workflow against a ComfyUI's /object_info: links are matched by type, widget
    inputs not given get the node's defaults, combo values are checked against what's installed."""

    def __init__(self, info: dict, uploaded: set | None = None):
        self.info, self.nodes, self.models = info, {}, []
        self.uploaded = uploaded or set()

    def has(self, cls: str) -> bool:
        return cls in self.info

    def require(self, *classes: str, hint: str = "") -> None:
        miss = [c for c in classes if c not in self.info]
        if miss:
            raise Missing(f"node {', '.join(miss)} not installed" + (f"; {hint}" if hint else
                          " (update ComfyUI: git pull, or ComfyUI Manager -> Update ComfyUI)"))

    def first(self, *classes: str, hint: str = "") -> str:
        for c in classes:
            if c in self.info:
                return c
        raise Missing(f"none of these nodes is installed: {', '.join(classes)}" + (f"; {hint}" if hint else ""))

    def choices(self, cls: str, inp: str) -> list:
        spec = self.info.get(cls, {}).get("input", {})
        s = (spec.get("required") or {}).get(inp) or (spec.get("optional") or {}).get(inp)
        return _choices(s) or []

    def pick(self, cls: str, inp: str, explicit: str | None, prefer=("",), exclude=(), what="model", hint="") -> str:
        """A model file from a loader's choice list: the explicit one, else the first matching `prefer` in order."""
        self.require(cls)
        ch = self.choices(cls, inp)
        if explicit:
            hit = [c for c in ch if c == explicit] or [c for c in ch if explicit.lower() in c.lower()]
            if not hit:
                raise Missing(f"{what} {explicit!r} not found for {cls}.{inp}; available: {', '.join(ch) or 'none'}")
            self.models.append(hit[0])
            return hit[0]
        for want in prefer:
            for c in ch:
                lc = c.lower()
                if want in lc and not any(x in lc for x in exclude):
                    self.models.append(c)
                    return c
        raise Missing(f"no {what} found for {cls}.{inp}" + (f"; {hint}" if hint else ""))

    def add(self, cls: str, *links: Out, **params) -> Node:
        self.require(cls)
        spec = self.info[cls].get("input") or {}
        free, inputs = list(links), {}
        for section, required in ((spec.get("required") or {}, True), (spec.get("optional") or {}, False)):
            for k, s in section.items():
                if s and s[0] == "COMFY_DYNAMICCOMBO_V3":
                    _dynamic(inputs, k, s, params.pop(k, None))
                elif k in params:
                    v = params.pop(k)
                    if isinstance(v, Out):
                        inputs[k] = v.ref
                        continue
                    ch = _choices(s)
                    if ch and v not in ch and v not in self.uploaded:
                        if k in SOFT:
                            print(f"  [{cls}] {k}={v!r} not available, using {_default(s)!r}", file=sys.stderr)
                            v = _default(s)
                        else:
                            raise Missing(f"{cls}.{k}={v!r} not available; choices: {', '.join(map(str, ch[:20]))}")
                    inputs[k] = v
                elif _is_link(s):
                    m = next((o for o in free if o.type in s[0].split(",") or s[0] == "*"), None)
                    if m:
                        free.remove(m)
                        inputs[k] = m.ref
                    elif required:
                        raise Missing(f"{cls}: nothing connected to required input {k!r} ({s[0]})")
                elif required:
                    try:
                        inputs[k] = _default(s)
                    except Missing:
                        raise Missing(f"{cls}.{k}: nothing to choose from (no files installed for it)") from None
        if free:
            raise Missing(f"{cls}: no input takes {[o.type for o in free]}")
        for k in params:
            print(f"  [{cls}] has no input {k!r}, ignored", file=sys.stderr)
        nid = str(len(self.nodes) + 1)
        self.nodes[nid] = {"class_type": cls, "inputs": inputs}
        return Node(self, nid, cls)


def apply_sets(wf: dict, sets: dict) -> None:
    """--set Class.input=value / id.input=value."""
    for key, val in sets.items():
        target, _, inp = key.rpartition(".")
        if not target:
            die(f"--set {key}: use Class.input=value or node_id.input=value")
        hits = [n for nid, n in wf.items() if nid == target or n.get("class_type") == target]
        if not hits:
            die(f"--set {key}: no node {target!r} in the workflow ({', '.join(sorted({n['class_type'] for n in wf.values()}))})")
        for n in hits:
            n.setdefault("inputs", {})[inp] = val


# --------------------------------------------------------------------------- user workflows

PH = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def fill(obj, v: dict, missing: set):
    """Replace {{key}} placeholders: a whole-string placeholder keeps the value's type."""
    if isinstance(obj, dict):
        return {k: fill(x, v, missing) for k, x in obj.items()}
    if isinstance(obj, list):
        return [fill(x, v, missing) for x in obj]
    if isinstance(obj, str):
        m = PH.fullmatch(obj.strip())
        if m:
            if m.group(1) in v and v[m.group(1)] is not None:
                return v[m.group(1)]
            missing.add(m.group(1))
            return obj

        def sub(mm):
            if mm.group(1) in v and v[mm.group(1)] is not None:
                return str(v[mm.group(1)])
            missing.add(mm.group(1))
            return mm.group(0)
        return PH.sub(sub, obj)
    return obj


def load_workflow(path: str | Path) -> dict:
    wf = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        die(f"{path} is a UI workflow; in ComfyUI use Workflow -> Export (API) and pass that file")
    if isinstance(wf, dict) and "prompt" in wf and isinstance(wf["prompt"], dict):
        wf = wf["prompt"]          # {"prompt": {...}} as sent to /prompt
    if isinstance(wf, dict) and "workflow" in wf and isinstance(wf["workflow"], dict):
        wf = wf["workflow"]        # a comfy_manifest.jsonl line
    if not isinstance(wf, dict) or not all(isinstance(n, dict) and "class_type" in n for n in wf.values()):
        die(f"{path}: not an API-format ComfyUI workflow")
    return wf


def user_workflow(recipe: str, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    p = data_dir(create=False) / "comfy_workflows" / f"{recipe}.json"
    return p if p.exists() else None


# --------------------------------------------------------------------------- execution


def queue(wf: dict) -> str:
    r = _req("POST", "/prompt", {"prompt": wf, "client_id": "um-" + uuid.uuid4().hex[:8]})
    if r.get("node_errors"):
        die(_prompt_error(r))
    return r["prompt_id"]


GONE_POLLS = 15     # consecutive polls with the prompt in neither the queue nor the history = it was lost


def wait(pid: str, timeout: float = 3600, quiet: bool = False) -> dict:
    t0, last, tick, gone = time.time(), "", 0.0, 0
    while True:
        h = _req("GET", f"/history/{pid}")
        if pid in h:
            e = h[pid]
            st = e.get("status") or {}
            if st.get("status_str") == "error":
                for typ, d in st.get("messages") or []:
                    if typ == "execution_error":
                        die(f"ComfyUI failed in node {d.get('node_id')} ({d.get('node_type')}): "
                            f"{d.get('exception_type', '')}: {d.get('exception_message', '').strip()}")
                die(f"ComfyUI failed: {json.dumps(st)[:1500]}")
            if st.get("completed", True):
                return e
        q = _req("GET", "/queue")
        if any(len(it) > 1 and it[1] == pid for it in q.get("queue_running", [])):
            s, gone = "running", 0
        else:
            pend = sorted(it[0] for it in q.get("queue_pending", []) if len(it) > 1 and it[1] == pid)
            ahead = sum(1 for it in q.get("queue_pending", []) if pend and it[0] < pend[0]) + len(q.get("queue_running", []))
            s = f"queued ({ahead} ahead)" if pend else "finishing"
            gone = gone + 1 if not pend else 0
            if gone >= GONE_POLLS:
                die(f"prompt {pid} is gone: ComfyUI has it in neither its queue nor its history (it was restarted or the "
                    "job was cancelled). Check `um comfy logs`, then run the command again")
        now = time.time() - t0
        if not quiet and (s != last or now - tick > 30):
            print(f"  [comfy] {s} {now:.0f}s", file=sys.stderr)
            last, tick = s, now
        if now > timeout:
            die(f"still {s} after {timeout:.0f}s (prompt {pid}); it keeps running in ComfyUI, results land in its output folder")
        time.sleep(1.0 if now < 30 else 2.0)


def outputs(entry: dict, order: list[str] | None = None) -> list[dict]:
    """Every saved file in a history entry ({filename, subfolder, type}), in workflow order."""
    outs = entry.get("outputs") or {}
    ids = sorted(outs, key=lambda n: (order.index(n) if order and n in order else 10 ** 6, int(n) if str(n).isdigit() else 0, str(n)))
    items = [it for nid in ids for key, lst in outs[nid].items() if isinstance(lst, list)
             for it in lst if isinstance(it, dict) and it.get("filename")]
    saved = [it for it in items if it.get("type", "output") == "output"]
    return saved or items


def download(entry: dict, out: Path, name: str, order=None) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for i, it in enumerate(outputs(entry, order)):
        ext = Path(it["filename"]).suffix
        path = out / (f"{name}{ext}" if i == 0 else f"{name}_{i + 1}{ext}")
        q = urllib.parse.urlencode({"filename": it["filename"], "subfolder": it.get("subfolder", ""), "type": it.get("type", "output")})
        path.write_bytes(_req("GET", f"/view?{q}", raw=True, timeout=600))
        files.append(str(path))
    return files


def models_in(wf: dict) -> list[str]:
    return sorted({v for n in wf.values() for v in (n.get("inputs") or {}).values()
                   if isinstance(v, str) and v.lower().endswith(MODEL_EXT)})


def engines_in(wf: dict) -> list[str]:
    """TTS engines used (their weights are downloaded by the node pack, so no file name is in the workflow)."""
    return sorted({n["class_type"] for n in wf.values() if n["class_type"].endswith("EngineNode")})


def manifest(out: Path, rec: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "comfy_manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------- recipes (graph builders)


def _preset(ckpt: str) -> dict:
    n = ckpt.lower()
    if "flux" in n:
        return dict(steps=20, cfg=1.0, sampler="euler", scheduler="simple", base=1024, flux=True)
    if any(t in n for t in ("turbo", "lightning", "hyper", "lcm", "schnell")):
        return dict(steps=8, cfg=2.0, sampler="dpmpp_sde", scheduler="karras", base=1024)
    if any(t in n for t in ("v1-5", "sd15", "sd_15", "1.5", "v1.5")):
        return dict(steps=25, cfg=7.0, sampler="dpmpp_2m", scheduler="karras", base=512)
    return dict(steps=30, cfg=6.0, sampler="dpmpp_2m", scheduler="karras", base=1024)


def _size(aspect: str, base: int) -> tuple[int, int]:
    a, b = (float(x) for x in aspect.split(":"))
    w = math.sqrt(base * base * a / b)
    return int(round(w / 64) * 64), int(round(base * base / w / 64) * 64)


def _sd(g: Graph, v: dict):
    """Checkpoint (+ LoRAs) -> (ckpt name, MODEL, CLIP, VAE)."""
    ck = g.pick("CheckpointLoaderSimple", "ckpt_name", v.get("ckpt") or os.environ.get("COMFY_CKPT"),
                IMAGE_PREFER, IMAGE_EXCLUDE, "image checkpoint", DOCS["image"])
    c = g.add("CheckpointLoaderSimple", ckpt_name=ck)
    model, clip = c["MODEL"], c["CLIP"]
    for lora in v.get("lora") or []:
        nm, _, s = lora.partition(":")
        f = g.pick("LoraLoader", "lora_name", nm, what="LoRA")
        s = float(s or 1)
        lo = g.add("LoraLoader", model=model, clip=clip, lora_name=f, strength_model=s, strength_clip=s)
        model, clip = lo["MODEL"], lo["CLIP"]
    return ck, model, clip, c["VAE"]


def _sample(g: Graph, v: dict, ck: str, model, clip, vae, latent, denoise: float = 1.0) -> Out:
    p = _preset(ck)
    pos = g.add("CLIPTextEncode", clip=clip, text=v["prompt"])["CONDITIONING"]
    neg = g.add("CLIPTextEncode", clip=clip, text=v.get("negative") or "")["CONDITIONING"]
    if p.get("flux") and g.has("FluxGuidance"):
        pos = g.add("FluxGuidance", conditioning=pos, guidance=v.get("guidance") or 3.5)["CONDITIONING"]
    ks = g.add("KSampler", model=model, positive=pos, negative=neg, latent_image=latent, seed=v["seed"],
               steps=v.get("steps") or p["steps"], cfg=p["cfg"] if v.get("cfg") is None else v["cfg"],
               sampler_name=p["sampler"], scheduler=p["scheduler"], denoise=denoise)
    return g.add("VAEDecode", samples=ks["LATENT"], vae=vae)["IMAGE"]


def b_image(g: Graph, v: dict):
    ck, model, clip, vae = _sd(g, v)
    w, h = _size(v.get("aspect") or "1:1", _preset(ck)["base"])
    v.update(width=w, height=h)
    lat = g.add("EmptyLatentImage", width=w, height=h, batch_size=v.get("n") or 1)["LATENT"]
    g.add("SaveImage", images=_sample(g, v, ck, model, clip, vae, lat), filename_prefix=v["prefix"])


def b_edit(g: Graph, v: dict):
    ck, model, clip, vae = _sd(g, v)
    img = g.add("LoadImage", image=v["image"])["IMAGE"]
    if g.has("ImageScaleToTotalPixels"):
        img = g.add("ImageScaleToTotalPixels", image=img, upscale_method="lanczos", megapixels=(_preset(ck)["base"] / 1024) ** 2)["IMAGE"]
    lat = g.add("VAEEncode", pixels=img, vae=vae)["LATENT"]
    if (v.get("n") or 1) > 1:
        lat = g.add("RepeatLatentBatch", samples=lat, amount=v["n"])["LATENT"]
    g.add("SaveImage", images=_sample(g, v, ck, model, clip, vae, lat, denoise=v["strength"]), filename_prefix=v["prefix"])


def b_rmbg(g: Graph, v: dict):
    cls = g.first(*RMBG_NODES, hint=DOCS["rmbg"])
    node = g.add(cls, g.add("LoadImage", image=v["image"])["IMAGE"])
    img = g.add("MaskToImage", mask=node["MASK"])["IMAGE"] if "MASK" in node.types else node["IMAGE"]
    g.add("SaveImage", images=img, filename_prefix=v["prefix"])


def b_upscale(g: Graph, v: dict):
    f = g.pick("UpscaleModelLoader", "model_name", v.get("upscaler"), ("ultrasharp", "realesrgan_x4plus", "4x", "x4", "2x", "x2", ""),
               what="upscale model", hint=DOCS["upscale"])
    m = g.add("UpscaleModelLoader", model_name=f)["UPSCALE_MODEL"]
    up = g.add("ImageUpscaleWithModel", upscale_model=m, image=g.add("LoadImage", image=v["image"])["IMAGE"])["IMAGE"]
    sc = re.search(r"(?:^|[^0-9])([1-8])x|x([1-8])(?:[^0-9]|$)", f.lower())
    native = int(sc.group(1) or sc.group(2)) if sc else 4
    if abs(v["factor"] - native) > 1e-3:
        up = g.add("ImageScaleBy", image=up, upscale_method="lanczos", scale_by=v["factor"] / native)["IMAGE"]
    g.add("SaveImage", images=up, filename_prefix=v["prefix"])


def b_model3d(g: Graph, v: dict):
    g.require("Hunyuan3Dv2Conditioning", "EmptyLatentHunyuan3Dv2", "VAEDecodeHunyuan3D", "SaveGLB", hint=DOCS["model3d"])
    ck = g.pick("ImageOnlyCheckpointLoader", "ckpt_name", v.get("ckpt"), ("hunyuan3d",), what="Hunyuan3D checkpoint", hint=DOCS["model3d"])
    c = g.add("ImageOnlyCheckpointLoader", ckpt_name=ck)
    enc = g.add("CLIPVisionEncode", c["CLIP_VISION"], g.add("LoadImage", image=v["image"])["IMAGE"])["CLIP_VISION_OUTPUT"]
    cond = g.add("Hunyuan3Dv2Conditioning", enc)
    model = c["MODEL"]
    if g.has("ModelSamplingAuraFlow"):
        model = g.add("ModelSamplingAuraFlow", model=model, shift=1.0)["MODEL"]
    lat = g.add("EmptyLatentHunyuan3Dv2", resolution=3072, batch_size=1)["LATENT"]
    ks = g.add("KSampler", model=model, positive=cond[0], negative=cond[1], latent_image=lat, seed=v["seed"],
               steps=v.get("steps") or 30, cfg=5.0 if v.get("cfg") is None else v["cfg"], sampler_name="euler", scheduler="normal", denoise=1.0)
    vox = g.add("VAEDecodeHunyuan3D", samples=ks["LATENT"], vae=c["VAE"], octree_resolution=v.get("octree") or 256)
    mesh = (g.add("VoxelToMesh", vox[0], algorithm="surface net", threshold=0.6) if g.has("VoxelToMesh")
            else g.add("VoxelToMeshBasic", vox[0], threshold=0.6))
    g.add("SaveGLB", mesh[0], filename_prefix=v["prefix"])


def b_rig(g: Graph, v: dict):
    raise Missing(DOCS["rig"])


def _stable_audio(g: Graph, v: dict, seconds: float):
    ck = g.pick("CheckpointLoaderSimple", "ckpt_name", None, ("stable_audio", "stable-audio"), what="Stable Audio Open checkpoint", hint=DOCS["sfx"])
    c = g.add("CheckpointLoaderSimple", ckpt_name=ck)
    t5 = g.pick("CLIPLoader", "clip_name", None, ("t5_base", "t5-base"), what="T5-base text encoder", hint=DOCS["sfx"])
    clip = g.add("CLIPLoader", clip_name=t5, type="stable_audio")["CLIP"]
    pos = g.add("CLIPTextEncode", clip=clip, text=v["prompt"])["CONDITIONING"]
    neg = g.add("CLIPTextEncode", clip=clip, text=v.get("negative") or "low quality, noise, distortion")["CONDITIONING"]
    lat = g.add("EmptyLatentAudio", seconds=min(seconds, 47.0), batch_size=v.get("n") or 1)["LATENT"]
    ks = g.add("KSampler", model=c["MODEL"], positive=pos, negative=neg, latent_image=lat, seed=v["seed"], steps=v.get("steps") or 50,
               cfg=4.98 if v.get("cfg") is None else v["cfg"], sampler_name="dpmpp_3m_sde_gpu", scheduler="exponential", denoise=1.0)
    g.add("SaveAudio", audio=g.add("VAEDecodeAudio", samples=ks["LATENT"], vae=c["VAE"])["AUDIO"], filename_prefix=v["prefix"])


def b_sfx(g: Graph, v: dict):
    _stable_audio(g, v, v.get("seconds") or 3.0)


def b_music(g: Graph, v: dict):
    try:
        g.require("TextEncodeAceStepAudio", "EmptyAceStepLatentAudio", hint=DOCS["music"])
        ck = g.pick("CheckpointLoaderSimple", "ckpt_name", v.get("ckpt"), ("ace_step", "ace-step"), what="ACE-Step checkpoint", hint=DOCS["music"])
        c = g.add("CheckpointLoaderSimple", ckpt_name=ck)
        model = c["MODEL"]
        if g.has("ModelSamplingSD3"):
            model = g.add("ModelSamplingSD3", model=model, shift=5.0)["MODEL"]
        pos = g.add("TextEncodeAceStepAudio", clip=c["CLIP"], tags=v["prompt"], lyrics=v.get("lyrics") or "[instrumental]",
                    lyrics_strength=0.99)["CONDITIONING"]
        neg = g.add("ConditioningZeroOut", conditioning=pos)["CONDITIONING"]
        lat = g.add("EmptyAceStepLatentAudio", seconds=v.get("seconds") or 60, batch_size=1)["LATENT"]
        ks = g.add("KSampler", model=model, positive=pos, negative=neg, latent_image=lat, seed=v["seed"], steps=v.get("steps") or 50,
                   cfg=5.0 if v.get("cfg") is None else v["cfg"], sampler_name="euler", scheduler="simple", denoise=1.0)
        g.add("SaveAudio", audio=g.add("VAEDecodeAudio", samples=ks["LATENT"], vae=c["VAE"])["AUDIO"], filename_prefix=v["prefix"])
    except Missing as ace:
        g.nodes.clear()
        g.models.clear()
        try:
            _stable_audio(g, v, v.get("seconds") or 60)
        except Missing as sa:
            raise Missing(f"{ace}\n    fallback Stable Audio: {sa}") from None
        if not v.get("dry"):
            print(f"  [comfy] ACE-Step unavailable ({ace}); using Stable Audio Open, max 47 s", file=sys.stderr)


def tts_engines(info: dict) -> list[str]:
    """Engine nodes (output TTS_ENGINE) of a unified-TTS pack such as TTS Audio Suite, best default first."""
    found = [c for c, d in info.items() if "TTS_ENGINE" in (d.get("output") or []) and not d.get("api_node")
             and not TTS_SKIP.search(c)]
    return sorted(found, key=lambda c: ENGINE_PREFER.index(c) if c in ENGINE_PREFER else len(ENGINE_PREFER))


def tts_nodes(info: dict) -> list[str]:
    """Installed nodes that look like text-to-speech: output AUDIO, take a STRING, named/categorised as TTS."""
    found = []
    for cls, d in info.items():
        if "AUDIO" not in (d.get("output") or []) or d.get("api_node") or TTS_SKIP.search(cls):   # api_node = paid cloud node
            continue
        label = f"{cls} {d.get('category', '')} {d.get('display_name', '')}".lower()
        req = (d.get("input") or {}).get("required") or {}
        if re.search(r"tts|speech|voice", label) and any(s and s[0] == "STRING" for s in req.values()):
            found.append(cls)
    return sorted(found, key=lambda c: next((i for i, p in enumerate(TTS_PREFER) if p in c.lower()), len(TTS_PREFER)))


def b_voice(g: Graph, v: dict):
    if g.has("UnifiedTTSTextNode") and not v.get("node"):
        engines = tts_engines(g.info)
        eng = v.get("engine") or (engines[0] if engines else None)
        if not eng:
            raise Missing("TTS Audio Suite is installed but has no engine node; " + DOCS["voice"])
        if eng not in g.info:
            raise Missing(f"engine {eng!r} not installed; available: {', '.join(engines)}")
        links = [g.add(eng)["TTS_ENGINE"]]
        if v.get("audio"):
            links.append(g.add("LoadAudio", audio=v["audio"])["AUDIO"])
        voices = g.choices("UnifiedTTSTextNode", "narrator_voice")
        voice = "none"
        if v.get("voice"):
            hit = [c for c in voices if v["voice"].lower() in c.lower()]
            if not hit:
                raise Missing(f"voice {v['voice']!r} not found; the pack ships: {', '.join(c for c in voices if c != 'none')}")
            voice = hit[0]
        elif eng in NEEDS_REF_VOICE and not v.get("audio"):    # these engines can't speak without a reference clip + text
            voice = next((c for c in voices if "male_01" in c), next((c for c in voices if c != "none"), "none"))
        node = g.add("UnifiedTTSTextNode", *links, text=v["text"], seed=v["seed"], narrator_voice=voice)
        g.add("SaveAudio", audio=node["AUDIO"], filename_prefix=v["prefix"])
        return
    nodes = tts_nodes(g.info)
    cls = v.get("node") or (nodes[0] if nodes else None)
    if not cls:
        raise Missing("no text-to-speech node installed; " + DOCS["voice"])
    g.require(cls)
    spec = g.info[cls].get("input") or {}
    strs = [k for sec in ("required", "optional") for k, s in (spec.get(sec) or {}).items() if s and s[0] == "STRING"]
    text_in = next((k for k in strs if "text" in k.lower()), strs[0] if strs else None)
    if not text_in:
        raise Missing(f"{cls} has no text input")
    takes_audio = any(s and s[0] == "AUDIO" for sec in ("required", "optional") for s in (spec.get(sec) or {}).values())
    if any(s and s[0] == "AUDIO" for s in (spec.get("required") or {}).values()) and not v.get("audio"):
        raise Missing(f"{cls} needs a reference voice: pass --ref sample.wav")
    links = []
    if v.get("audio"):
        if takes_audio:
            links.append(g.add("LoadAudio", audio=v["audio"])["AUDIO"])
        else:
            print(f"  [{cls}] takes no reference audio, --ref ignored", file=sys.stderr)
    params = {text_in: v["text"]}
    if any(k == "seed" for sec in ("required", "optional") for k in (spec.get(sec) or {})):
        params["seed"] = v["seed"]
    node = g.add(cls, *links, **params)
    g.add("SaveAudio", audio=node["AUDIO"], filename_prefix=v["prefix"])


def b_video(g: Graph, v: dict):
    g.require("Wan22ImageToVideoLatent", hint=DOCS["video"])
    unet = g.pick("UNETLoader", "unet_name", v.get("ckpt"), ("wan2.2_ti2v_5b", "ti2v_5b", "wan2.2_ti2v"), what="Wan 2.2 TI2V model", hint=DOCS["video"])
    te = g.pick("CLIPLoader", "clip_name", None, ("umt5_xxl", "umt5"), what="UMT5-XXL text encoder", hint=DOCS["video"])
    vae_f = g.pick("VAELoader", "vae_name", None, ("wan2.2_vae", "wan2_2_vae"), what="Wan 2.2 VAE", hint=DOCS["video"])
    model = g.add("UNETLoader", unet_name=unet)["MODEL"]
    if g.has("ModelSamplingSD3"):
        model = g.add("ModelSamplingSD3", model=model, shift=8.0)["MODEL"]
    clip = g.add("CLIPLoader", clip_name=te, type="wan")["CLIP"]
    vae = g.add("VAELoader", vae_name=vae_f)["VAE"]
    pos = g.add("CLIPTextEncode", clip=clip, text=v["prompt"])["CONDITIONING"]
    neg = g.add("CLIPTextEncode", clip=clip, text=v.get("negative") or
                "blurry, low quality, static, still frame, distorted, deformed, watermark, text, subtitles")["CONDITIONING"]
    lat = g.add("Wan22ImageToVideoLatent", vae=vae, start_image=g.add("LoadImage", image=v["image"])["IMAGE"],
                width=v["width"], height=v["height"], length=v["frames"], batch_size=1)["LATENT"]
    ks = g.add("KSampler", model=model, positive=pos, negative=neg, latent_image=lat, seed=v["seed"], steps=v.get("steps") or 20,
               cfg=5.0 if v.get("cfg") is None else v["cfg"], sampler_name="uni_pc", scheduler="simple", denoise=1.0)
    frames = g.add("VAEDecode", samples=ks["LATENT"], vae=vae)["IMAGE"]
    if g.has("CreateVideo") and g.has("SaveVideo"):
        g.add("SaveVideo", video=g.add("CreateVideo", images=frames, fps=24.0)["VIDEO"], filename_prefix=v["prefix"])
    else:
        g.add("SaveAnimatedWEBP", images=frames, filename_prefix=v["prefix"], fps=24.0)


BUILD = dict(image=b_image, sprite=b_image, texture=b_image, pbr=b_image, edit=b_edit, rmbg=b_rmbg, upscale=b_upscale,
             model3d=b_model3d, rig=b_rig, sfx=b_sfx, music=b_music, voice=b_voice, video=b_video)


# --------------------------------------------------------------------------- local post-processing


def _sprite():
    from um import sprite
    return sprite


def _blur(a, r: int):
    """Box blur with wrap-around (tiles stay seamless)."""
    np = need("numpy")
    if r < 1:
        return a
    for ax in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[ax] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad, mode="wrap"), axis=ax)
        n = a.shape[ax]
        a = (np.take(c, range(2 * r + 1, 2 * r + 1 + n), axis=ax) - np.take(c, range(n), axis=ax)) / (2 * r + 1)
    return a


def _norm(a):
    np = need("numpy")
    lo, hi = np.percentile(a, [1, 99])
    return np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)


def pbr_maps(im, maps=("basecolor", "normal", "roughness", "metalness", "height"), strength: float = 2.0,
             metal: float = 0.0, directx: bool = False) -> dict:
    """Heuristic PBR maps from one tileable texture (the Materialize approach): height from detail luminance,
    normal from its gradient, roughness from brightness, AO from cavities. Wrap-around, so maps tile."""
    np = need("numpy")
    Image = _sprite()._pil()
    rgb = np.asarray(im.convert("RGB")).astype(np.float32) / 255
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    h, w = lum.shape
    R = max(2, min(h, w) // 16)
    low = _blur(_blur(lum, R), R)
    hgt = _norm(_blur(lum - low, 1))
    res = {}
    for m in maps:
        if m == "basecolor":   # divide out baked large-scale lighting
            a = np.clip(rgb * (lum.mean() / np.maximum(low, 1e-3))[..., None], 0, 1)
            res[m] = Image.fromarray((a * 255).astype(np.uint8), "RGB")
        elif m == "height":
            res[m] = Image.fromarray((hgt * 255).astype(np.uint8), "L")
        elif m == "normal":
            k = strength * w / 128
            gx = (np.roll(hgt, -1, 1) - np.roll(hgt, 1, 1)) * 0.5 * k
            gy = (np.roll(hgt, -1, 0) - np.roll(hgt, 1, 0)) * 0.5 * k
            n = np.dstack([-gx, -gy if directx else gy, np.ones_like(gx)])   # OpenGL: green up
            n /= np.linalg.norm(n, axis=2, keepdims=True)
            res[m] = Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8), "RGB")
        elif m == "roughness":
            res[m] = Image.fromarray(((0.95 - 0.55 * _norm(lum)) * 255).astype(np.uint8), "L")
        elif m == "metalness":
            res[m] = Image.fromarray(np.full((h, w), int(metal * 255), np.uint8), "L")
        elif m == "ao":
            ao = np.clip(1 - np.maximum(_blur(hgt, max(1, R // 2)) - hgt, 0) * 2.5, 0, 1)
            res[m] = Image.fromarray((ao * 255).astype(np.uint8), "L")
        else:
            die(f"unknown PBR map {m!r} (basecolor, normal, roughness, metalness, height, ao)")
    return res


def apply_mask(src, mask):
    """Foreground mask (any convention) -> RGBA cut-out, cropped. A mask whose border is mostly white is inverted."""
    np = need("numpy")
    Image = _sprite()._pil()
    m = np.asarray(mask.convert("L").resize(src.size, Image.LANCZOS)).astype(np.float32)
    border = np.concatenate([m[0], m[-1], m[:, 0], m[:, -1]])
    if border.mean() > 127:
        m = 255 - m
    a = np.asarray(src.convert("RGBA")).copy()
    a[..., 3] = np.minimum(a[..., 3], m).astype(np.uint8)
    out = Image.fromarray(a, "RGBA")
    return out.crop(out.getbbox() or (0, 0, 1, 1))


def _flat(path: str) -> tuple[Path, str | None]:
    """Images with transparency go to ComfyUI flattened on white (LoadImage would keep junk RGB under alpha=0).
    Returns (file to upload, temp folder to delete afterwards or None)."""
    sp = _sprite()
    im = sp.load(path)
    if im.getextrema()[3][0] == 255:
        return Path(path), None
    Image = sp._pil()
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    tmp = tempfile.mkdtemp(prefix="um_comfy_")
    p = Path(tmp) / (Path(path).stem + "_flat.png")
    bg.convert("RGB").save(p)
    return p, tmp


# --------------------------------------------------------------------------- commands


def _prep(r: str, a, dry: bool = False) -> dict:
    """Recipe arguments -> placeholder/builder variables; uploads local inputs."""
    g = lambda k, d=None: getattr(a, k, d)  # noqa: E731
    v = dict(seed=g("seed") if g("seed") is not None else random.randint(0, 2 ** 32 - 1), steps=g("steps"), cfg=g("cfg"),
             ckpt=g("ckpt"), lora=g("lora"), negative=g("negative"), n=g("n") or 1, aspect=g("aspect"), dry=dry)
    text = g("prompt") or g("text") or ""
    src = g("image") or (g("ref") or [None])[0] or g("model_file")
    v["name"] = g("name") or (_name(a, text) if r in ("image", "sprite", "edit", "texture", "pbr", "sfx", "music", "voice")
                              else Path(src).stem + {"rmbg": "_cut", "upscale": "_up", "rig": "_rigged", "video": "_video"}.get(r, ""))
    v["prompt"], v["text"] = text, g("text")
    if r == "sprite":
        v["prompt"] = f"{text}. {SPRITE_STYLE}, on a plain flat white background"
        v["negative"] = v["negative"] or "background scenery, gradient background, ground shadow, text, watermark, cropped, frame"
    elif r in ("texture", "pbr"):
        v["prompt"] = f"{text}, seamless tileable texture, top-down orthographic view, flat even lighting, no shadows"
        v["negative"] = v["negative"] or "perspective, horizon, objects, text, watermark, vignette, border"
        v["aspect"] = "1:1"
    elif r == "image":
        v["negative"] = v["negative"] or "text, watermark, signature, blurry, low quality"
    elif r == "edit":
        v["strength"] = g("strength")
        if len(g("ref") or []) > 1:
            print("  [comfy] img2img uses the first --ref only; for multi-reference edits pass a Qwen-Image-Edit or "
                  "Flux Kontext workflow with --workflow", file=sys.stderr)
    elif r == "upscale":
        v.update(factor=g("factor"), upscaler=g("upscaler"))
    elif r == "model3d":
        v["octree"] = g("octree")
    elif r in ("sfx", "music"):
        v.update(seconds=g("seconds"), lyrics=g("lyrics"))
    elif r == "voice":
        v["node"], v["engine"], v["voice"] = g("node"), g("engine"), g("voice")
    elif r == "video":
        v["seconds"] = g("seconds")
        v["frames"] = min(121, int(round(g("seconds") * 24 / 4)) * 4 + 1)
    if r in ("image", "sprite", "texture", "pbr"):   # for {{width}}/{{height}}; the built-in graph re-sizes per checkpoint
        v["width"], v["height"] = _size(v["aspect"] or "1:1", _preset(v["ckpt"] or "")["base"])
    v["prefix"] = f"um/{v['name']}"
    v["dl"] = v["name"] + {"sprite": "_raw", "texture": "_raw", "pbr": "_raw", "rmbg": "_mask"}.get(r, "")

    uploaded = set()
    if src:
        v["local"] = src
        if r == "video":
            ar = 1280 / 704
            if not dry:
                im = _sprite().load(src)
                ar = im.width / im.height
            area = {"480p": 832 * 480, "720p": 1280 * 704}[g("res") or "720p"]
            v["width"] = int(round(math.sqrt(area * ar) / 32) * 32)
            v["height"] = int(round(math.sqrt(area / ar) / 32) * 32)
        key = {"rig": "model"}.get(r, "image")
        if dry:
            v[key] = "dry.png"
        else:
            send, tmp = _flat(src) if r in ("edit", "model3d", "video") else (Path(src), None)
            try:
                v[key] = upload(send)
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)
        uploaded.add(v[key])
    if g("voice_ref"):
        v["audio"] = "dry.wav" if dry else upload(g("voice_ref"))
        uploaded.add(v["audio"])
    v["_uploaded"] = uploaded
    return v


def _post(r: str, files: list[str], v: dict, a, out: Path) -> list[str]:
    sp = _sprite()
    if r == "sprite":
        res = []
        for i, f in enumerate(files):       # same numbering as download(): name, name_2, ...
            dst = out / (f"{v['name']}.png" if i == 0 else f"{v['name']}_{i + 1}.png")
            sp.cutout(sp.load(f)).save(dst)
            res.append(str(dst))
        return res + files
    if r in ("texture", "pbr"):
        tile = sp.seamless(sp.load(files[0])).convert("RGB")
        dst = out / f"{v['name']}.png"
        tile.save(dst)
        if r == "texture":
            return [str(dst)] + files
        res = []
        for m, im in pbr_maps(tile, a.maps.split(","), a.normal_strength, a.metal, a.directx).items():
            p = out / f"{v['name']}_{m}.png"
            im.save(p)
            res.append(str(p))
        return res + [str(dst)] + files
    if r == "rmbg":
        Image = sp._pil()
        got = Image.open(files[0])
        if got.mode in ("RGBA", "LA") and got.getextrema()[-1][0] < 250:
            cut = got.convert("RGBA")
            cut = cut.crop(cut.getbbox() or (0, 0, 1, 1))
        else:
            cut = apply_mask(sp.load(v["local"]), got)
        dst = out / f"{v['name']}.png"
        cut.save(dst)
        return [str(dst)] + files
    if r == "upscale":
        src = sp.load(v["local"])
        if src.getextrema()[3][0] < 255:
            Image = sp._pil()
            up = Image.open(files[0]).convert("RGBA")
            up.putalpha(src.getchannel("A").resize(up.size, Image.LANCZOS))
            up.save(files[0])
    return files


def is_silent(path: str, floor: float = -70.0) -> bool:
    """True when an audio file is (nearly) all zeros. Some node packs catch their own errors and still return an
    empty clip, so ComfyUI reports success. Needs ffmpeg; without it nothing is flagged."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    r = subprocess.run([ff, "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    return bool(m) and (m.group(1) == "-inf" or float(m.group(1)) < floor)


def _log_entries() -> list[dict]:
    try:
        return _req("GET", "/internal/logs/raw").get("entries", [])
    except SystemExit:
        return []


def log_mark() -> str:
    """Timestamp of ComfyUI's newest log line, to scope `server_log(since=...)` to one run."""
    entries = _log_entries()
    return entries[-1].get("t", "") if entries else ""


def server_log(lines: int = 60, since: str = "") -> str:
    """The tail of ComfyUI's own log (errors that a node pack swallowed show up here); since: only lines after that mark."""
    entries = [e for e in _log_entries() if e.get("t", "") > since]
    text = re.sub(r"\x1b\[[0-9;]*m", "", "".join(e.get("m", "") for e in entries))
    return "\n".join(text.splitlines()[-lines:])


def _local_rmbg(a, out: Path) -> None:
    """No background-removal node: border flood fill (works for flat backgrounds)."""
    sp = _sprite()
    name = a.name or Path(a.image).stem + "_cut"
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"{name}.png"
    sp.cutout(sp.load(a.image)).save(dst)
    manifest(out, dict(t=time.strftime("%Y-%m-%dT%H:%M:%S"), recipe="rmbg", name=name, backend="local flood fill",
                       input=a.image, files=[str(dst)]))
    print(dst)


def generate(r: str, a) -> list[str]:
    global _BASE
    if getattr(a, "url", None):
        _BASE = a.url.rstrip("/")
    out = Path(a.out)
    sets = _kv(a.set)
    wfp = user_workflow(r, a.workflow)
    if not wfp and r == "rmbg":
        try:
            Graph(object_info()).first(*RMBG_NODES)
        except Missing:
            print(f"  [comfy] no background-removal node ({DOCS['rmbg']}); using the local flood fill", file=sys.stderr)
            _local_rmbg(a, out)
            return []
    if not wfp:       # build once against dummy inputs, so a missing model or node fails before anything is uploaded
        dry = _prep(r, a, dry=True)
        gd = Graph(object_info(), dry.pop("_uploaded"))
        try:
            BUILD[r](gd, dry)
        except Missing as e:
            die(f"comfy {r}: {e}\n  (`um comfy check` lists what each recipe needs)")
    elif not a.workflow:
        print(f"  [comfy] using your default workflow {wfp}", file=sys.stderr)
    v = _prep(r, a)
    uploaded = v.pop("_uploaded")
    if wfp:
        missing: set = set()
        wf = fill(load_workflow(wfp), v, missing)
        if missing:
            die(f"{wfp}: no value for placeholder(s) {', '.join(sorted(missing))}")
    else:
        g = Graph(object_info(), uploaded)
        try:
            BUILD[r](g, v)
        except Missing as e:
            die(f"comfy {r}: {e}\n  (`um comfy check` lists what each recipe needs)")
        wf = g.nodes
    apply_sets(wf, sets)
    mark = log_mark()
    pid = queue(wf)
    entry = wait(pid, a.timeout)
    files = download(entry, out, v["dl"], list(wf))
    if not files:
        die(f"prompt {pid} finished without saving a file (does the workflow end in a Save node?)")
    if r in ("sfx", "music", "voice") and files and is_silent(files[0]):
        for f in files:
            Path(f).unlink(missing_ok=True)
        err = [l for l in server_log(400, since=mark).splitlines() if re.search(r"error|failed|not available|No module", l, re.I)]
        die(f"comfy {r}: ComfyUI finished but the audio is silent, so a node swallowed an error. "
            f"Last errors in ComfyUI's log:\n  " + "\n  ".join(err[-4:] or ["(none found; see `um comfy logs`)"]))
    files = _post(r, files, v, a, out)
    manifest(out, dict(t=time.strftime("%Y-%m-%dT%H:%M:%S"), recipe=r, name=v["name"], prompt_id=pid, seed=v["seed"],
                       models=models_in(wf), engines=engines_in(wf), files=files, workflow_file=str(wfp) if wfp else None,
                       input={k: x for k, x in v.items() if k in ("prompt", "negative", "text", "local", "seconds", "strength", "factor")},
                       workflow=wf))
    for f in files:
        print(f)
    return files


def cmd_run(a):
    global _BASE
    if a.url:
        _BASE = a.url.rstrip("/")
    wf = load_workflow(a.workflow)
    params = _kv(a.params)
    v, sets = {}, {}
    for k, x in params.items():
        if isinstance(x, str) and x.startswith("@"):
            x = upload(x[1:])
        (sets if "." in k else v)[k] = x
    name = a.name or Path(a.workflow).stem
    v.setdefault("name", name)
    v.setdefault("prefix", f"um/{name}")
    if "seed" not in v and "{{seed}}" in json.dumps(wf):
        v["seed"] = random.randint(0, 2 ** 32 - 1)
    missing: set = set()
    wf = fill(wf, v, missing)
    if missing:
        die(f"no value for placeholder(s) {', '.join(sorted(missing))}: pass them as key=value")
    apply_sets(wf, {**sets, **_kv(a.set)})
    pid = queue(wf)
    files = download(wait(pid, a.timeout), Path(a.out), name, list(wf))
    manifest(Path(a.out), dict(t=time.strftime("%Y-%m-%dT%H:%M:%S"), recipe="run", name=name, prompt_id=pid, seed=v.get("seed"),
                               models=models_in(wf), files=files, workflow_file=a.workflow, workflow=wf))
    for f in files:
        print(f)


def cmd_pixelate(a):
    sp = _sprite()
    im = sp.trim(sp.load(a.image))
    if a.size:
        w, h = parse_size(a.size)
    else:
        s = a.scale or max(im.width, im.height) / 64
        w, h = max(1, round(im.width / s)), max(1, round(im.height / s))
    res = sp.pixelate(im, w, h, a.colors, (20, 16, 24, 255) if a.outline else None)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"{a.name or Path(a.image).stem + '_px'}.png"
    res.save(dst)
    print(dst)


CHECKS = {   # dummy arguments for a dry build of each recipe
    "image": dict(prompt="x"), "sprite": dict(prompt="x"), "edit": dict(prompt="x", ref=["x.png"], strength=0.5),
    "rmbg": dict(image="x.png"), "upscale": dict(image="x.png", factor=2.0), "texture": dict(prompt="x"), "pbr": dict(prompt="x"),
    "model3d": dict(image="x.png"), "rig": dict(model_file="x.glb"), "sfx": dict(prompt="x", seconds=2.0),
    "music": dict(prompt="x", seconds=30.0), "voice": dict(text="x"), "video": dict(image="x.png", prompt="x", seconds=5),
}


def cmd_check(a):
    global _BASE
    if a.url:
        _BASE = a.url.rstrip("/")
    st = _req("GET", "/system_stats")
    sysi = st.get("system") or {}
    print(f"ComfyUI {sysi.get('comfyui_version', '?')} at {base_url()} (python {str(sysi.get('python_version', '?')).split()[0]})")
    for d in st.get("devices") or []:
        print(f"  {d.get('name')}: {d.get('vram_total', 0) / 2 ** 30:.1f} GB VRAM, {d.get('vram_free', 0) / 2 ** 30:.1f} GB free")
    info = object_info()
    print(f"  {len(info)} node types\n")
    for r, kw in CHECKS.items():
        ns = __import__("argparse").Namespace(name=None, seed=1, n=1, res="720p", out=".", **kw)
        wfp = user_workflow(r, None)
        if wfp:
            print(f"  {r:9} workflow  {wfp}")
            continue
        g = Graph(info, {"x.png", "x.glb", "dry.png", "dry"})
        try:
            v = _prep(r, ns, dry=True)
            g.uploaded |= v.pop("_uploaded")
            BUILD[r](g, v)
            print(f"  {r:9} ok        {', '.join(dict.fromkeys(g.models)) or 'built-in nodes'}")
        except Missing as e:
            if r == "rmbg":
                print(f"  {r:9} local     border flood fill only; for AI cut-outs: {DOCS['rmbg']}")
            else:
                print(f"  {r:9} missing   {e}")
    print(f"  {'pixelate':9} ok        local (no ComfyUI needed)")
    eng = tts_engines(info)
    if eng:
        print(f"\n  TTS engines (um comfy voice --engine): {', '.join(eng)}. Weights download on first use.")
    tts = [] if eng else tts_nodes(info)
    if tts:
        print(f"\n  TTS nodes found: {', '.join(tts)} (choose with `um comfy voice --node`)")


LOADERS = [("checkpoints", "CheckpointLoaderSimple", "ckpt_name"), ("diffusion_models", "UNETLoader", "unet_name"),
           ("text_encoders", "CLIPLoader", "clip_name"), ("vae", "VAELoader", "vae_name"), ("loras", "LoraLoader", "lora_name"),
           ("upscale_models", "UpscaleModelLoader", "model_name"), ("clip_vision", "CLIPVisionLoader", "clip_name")]


def cmd_models(a):
    global _BASE
    if a.url:
        _BASE = a.url.rstrip("/")
    g = Graph(object_info())
    for folder, cls, inp in LOADERS:
        ch = g.choices(cls, inp)
        print(f"{folder} ({len(ch)})")
        for c in ch:
            print(f"  {c}")


def cmd_logs(a):
    global _BASE
    if a.url:
        _BASE = a.url.rstrip("/")
    print(server_log(a.lines))


def cmd(a):
    generate(a.recipe, a)


def register(sub):
    import argparse
    p = sub.add_parser("comfy", help="generate assets with a local ComfyUI (no API key; your GPU)",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    rs = p.add_subparsers(dest="recipe", metavar="<recipe>")

    def recipe(name, help_, *positional, func=cmd, out=True, sd=False, timeout=3600):
        q = rs.add_parser(name, help=help_)
        for pos in positional:
            q.add_argument(pos)
        q.add_argument("--url", help="ComfyUI address (default COMFY_URL, then :8188, then :8000)")
        if out:
            q.add_argument("--out", default="assets/gen", help="output folder (default assets/gen)")
            q.add_argument("--name", help="output file stem")
        if func is cmd:
            q.add_argument("--workflow", help="your API-format workflow with {{placeholders}} instead of the built-in one")
            q.add_argument("--set", action="append", metavar="K=V", help="patch a node: Class.input=value or id.input:=json (repeatable)")
            q.add_argument("--seed", type=int)
            q.add_argument("--steps", type=int)
            q.add_argument("--cfg", type=float)
            q.add_argument("--timeout", type=float, default=timeout, help="seconds to wait for ComfyUI")
        if sd:
            q.add_argument("--ckpt", help="checkpoint file (substring ok); default: COMFY_CKPT, else SDXL, else Flux, else any")
            q.add_argument("--lora", action="append", metavar="NAME[:STRENGTH]", help="apply a LoRA (repeatable), e.g. pixelart:0.8")
            q.add_argument("--negative", help="negative prompt")
        q.set_defaults(func=func)
        return q

    q = recipe("image", "text -> image (SDXL / Flux / any checkpoint)", "prompt", sd=True)
    q.add_argument("--aspect", default="1:1", help="e.g. 1:1, 16:9, 3:4, 21:9")
    q.add_argument("--n", type=int, default=1)
    q = recipe("sprite", "text -> sprite on a flat background, cut out locally", "prompt", sd=True)
    q.add_argument("--aspect", default="1:1")
    q.add_argument("--n", type=int, default=1)
    q = recipe("edit", "img2img variants of a reference (strength = how much changes)", "prompt", sd=True)
    q.add_argument("--ref", action="append", required=True, help="reference image (only the first is used by the built-in workflow)")
    q.add_argument("--strength", type=float, default=0.55, help="denoise 0-1 (default 0.55)")
    q.add_argument("--n", type=int, default=1)
    recipe("rmbg", "remove the background (RMBG/BiRefNet node pack, else local flood fill)", "image")
    q = recipe("pixelate", "image -> grid-snapped pixel art (local)", "image", func=cmd_pixelate)
    q.add_argument("--size", help="target WxH (default: longest side 64)")
    q.add_argument("--scale", type=float, help="source pixels per output pixel")
    q.add_argument("--colors", type=int, default=32)
    q.add_argument("--outline", action="store_true")
    q = recipe("upscale", "upscale with an ESRGAN-style model (keeps alpha)", "image")
    q.add_argument("--factor", type=float, default=2)
    q.add_argument("--upscaler", help="file in models/upscale_models (substring ok)")
    recipe("texture", "seamless tiling texture (generated, then tiled locally)", "prompt", sd=True)
    q = recipe("pbr", "texture + heuristic PBR maps", "prompt", sd=True)
    q.add_argument("--maps", default="basecolor,normal,roughness,metalness,height", help="also: ao")
    q.add_argument("--normal-strength", type=float, default=2.0)
    q.add_argument("--metal", type=float, default=0.0, help="metalness 0-1 for the whole material")
    q.add_argument("--directx", action="store_true", help="DirectX normal map (green down: Unreal, Unity HDRP)")
    q = recipe("model3d", "image -> 3D mesh GLB (Hunyuan3D 2, untextured)", "image")
    q.add_argument("--ckpt", help="Hunyuan3D checkpoint (substring ok)")
    q.add_argument("--octree", type=int, default=256, help="mesh resolution (256-512)")
    recipe("rig", "auto-rig a model: needs --workflow (no built-in rigger in ComfyUI)", "model_file")
    q = recipe("sfx", "sound effect (Stable Audio Open, <= 47 s)", "prompt")
    q.add_argument("--seconds", type=float, default=3.0)
    q.add_argument("--negative")
    q.add_argument("--n", type=int, default=1)
    q = recipe("music", "music track (ACE-Step; falls back to Stable Audio Open)", "prompt")
    q.add_argument("--seconds", type=float, default=60)
    q.add_argument("--lyrics", help="lyrics with [verse]/[chorus] tags (default instrumental)")
    q.add_argument("--ckpt", help="ACE-Step checkpoint (substring ok)")
    q.add_argument("--negative")
    q = recipe("voice", "voice line through an installed TTS node pack", "text")
    q.add_argument("--ref", dest="voice_ref", help="reference voice sample (wav/mp3) for cloning TTS nodes")
    q.add_argument("--engine", help="TTS Audio Suite engine node (default ChatterBoxEngineNode; list: `um comfy check`)")
    q.add_argument("--voice", help="a voice the pack ships (substring, e.g. male_01, female_02, Morgan_Freeman); see its voices_examples")
    q.add_argument("--node", help="a standalone TTS node class instead of TTS Audio Suite (see `um comfy check`)")
    q = recipe("video", "image -> video clip (Wan 2.2 TI2V 5B)", "image", "prompt", timeout=7200)
    q.add_argument("--seconds", type=float, default=5, help="up to ~5 s (121 frames at 24 fps)")
    q.add_argument("--res", default="720p", choices=["480p", "720p"])
    q.add_argument("--ckpt", help="Wan model in models/diffusion_models (substring ok)")
    q.add_argument("--negative")
    q = recipe("run", "any API workflow: key=value fills {{key}}, Class.input=value patches, @file uploads", "workflow", func=cmd_run)
    q.add_argument("params", nargs="*")
    q.add_argument("--set", action="append", metavar="K=V")
    q.add_argument("--timeout", type=float, default=7200)
    recipe("check", "is ComfyUI up, which recipes can run, what's missing", func=cmd_check, out=False)
    recipe("models", "model files ComfyUI sees, per folder", func=cmd_models, out=False)
    q = recipe("logs", "tail of ComfyUI's own log (why did a node fail?)", func=cmd_logs, out=False)
    q.add_argument("--lines", type=int, default=60)
