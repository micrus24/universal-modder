---
name: comfy-assets
description: Generate game assets locally with ComfyUI through `um comfy`, with no API key or cloud, on the user's own GPU. Covers sprites with cut-out backgrounds, concept art, img2img variants, background removal, pixel art, upscaling, seamless textures, PBR maps, image-to-3D (Hunyuan3D 2), sound effects (Stable Audio Open), music (ACE-Step), TTS voice lines, image-to-video (Wan 2.2) and any custom ComfyUI workflow. Use when the user wants assets generated locally or offline, mentions ComfyUI, has no fal key, or wants to use their own checkpoints, LoRAs or workflows.
---

# Game assets with a local ComfyUI

`um comfy` mirrors the `um fal` recipes, but runs them on the user's ComfyUI. The trade-offs:
- **For it:** free, private and offline, and it works with the user's own checkpoints and LoRAs (a pixel-art
  LoRA beats any prompt).
- **Against it:** each model has to be downloaded once, it needs a GPU (8 GB VRAM is workable, 12 GB+ is
  comfortable), and some recipes are weaker than the cloud ones (see the table).

## Setup (check once per session)
1. **ComfyUI must be running.** `um comfy` looks for it at:
   - `COMFY_URL` (env or `.env`);
   - else `http://127.0.0.1:8188` (from source or portable);
   - else `:8000` (ComfyUI Desktop).

   If it's not running, ask the user to start it. Don't install it into their system without asking.
2. **Run `um comfy check`.** It prints the GPU and VRAM, then one line per recipe:
   - `ok`, with the model files it will use;
   - or `missing`, with what to download or which node pack to install, and where to put it.

   Install node packs and download models only after the user agrees: they are gigabytes, and node packs
   run code.
3. **Run `um comfy models`** to list the files ComfyUI sees, per folder.

## Where to put the models
The folder is whatever the user's ComfyUI reads. Find it with
`curl -s http://127.0.0.1:8188/system_stats` (look at `argv`: `--extra-model-paths-config` and
`--base-directory` show where models live; Comfy Desktop keeps them in a shared `models` folder).
Downloads that were tested end to end (all public on Hugging Face or GitHub, no login):

| Recipe | File | Folder | Size |
|---|---|---|---|
| image, sprite, edit, texture, pbr | `stabilityai/stable-diffusion-xl-base-1.0` → `sd_xl_base_1.0.safetensors` | `checkpoints` | 6.9 GB |
| upscale | Real-ESRGAN v0.1.0 release → `RealESRGAN_x4plus.pth` | `upscale_models` | 67 MB |
| sfx, music (fallback) | `Comfy-Org/stable-audio-open-1.0_repackaged` → `stable-audio-open-1.0.safetensors` | `checkpoints` | 4.9 GB |
| sfx, music (fallback) | `google-t5/t5-base` → `model.safetensors`, saved as `t5_base.safetensors` | `text_encoders` | 0.9 GB |
| music | `Comfy-Org/ACE-Step_ComfyUI_repackaged` → `all_in_one/ace_step_v1_3.5b.safetensors` | `checkpoints` | 7.2 GB |
| model3d | `Comfy-Org/hunyuan3D_2.0_repackaged` → `split_files/hunyuan3d-dit-v2_fp16.safetensors` | `checkpoints` | 4.6 GB |
| video | `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` → `split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors` | `diffusion_models` | 9.3 GB |
| video | same repo → `split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `text_encoders` | 6.3 GB |
| video | same repo → `split_files/vae/wan2.2_vae.safetensors` | `vae` | 1.3 GB |

Download with `curl -L -C - --fail -o <file> <url>` (resumable), then re-run `um comfy check`. Ask the user
before downloading: the whole set is about 41 GB. Check free disk space first.

## Measured on an RTX 3060 (12 GB), ComfyUI 0.38
- `sprite` (SDXL, 1024 px): about 30 s.
- `upscale` (128 → 256 px): about 1 s.
- `sfx` (1.5 s): a few seconds.
- `music` (20 s, ACE-Step): about 30 s.
- `model3d` (Hunyuan3D 2.0): about 1.5 min.
- `video` (Wan 2.2 5B, 480p, 2 s): about 3 min.

## Recipes (`um comfy <recipe> --help`)

| Asset | Command | Built-in workflow needs |
|---|---|---|
| Concept / key art | `um comfy image "<prompt>" --aspect 16:9` | any SDXL/Flux checkpoint (SDXL preferred; `--ckpt`, `COMFY_CKPT`) |
| Sprite, transparent | `um comfy sprite "<subject, view, style>" --name x` | same; generated on flat white, cut out locally (`x.png`, raw in `x_raw.png`) |
| Variants / frames | `um comfy edit "<change>" --ref base.png --strength 0.5` | same (img2img: low strength keeps the design, high changes more) |
| Background removal | `um comfy rmbg in.png` | ComfyUI-RMBG, BiRefNet or Inspyrenet node pack; else a local flood fill |
| Pixel art | `um comfy pixelate in.png --size 48x48 --colors 24` | nothing (local) |
| Upscale | `um comfy upscale in.png --factor 2` | an ESRGAN-style model in `models/upscale_models`; keeps alpha |
| Seamless texture | `um comfy texture "mossy cobblestone"` | image checkpoint; tiled locally |
| PBR maps | `um comfy pbr "rusted sheet metal" [--directx] [--metal 1]` | image checkpoint; maps derived locally (heuristic, Materialize-style) |
| Image → 3D (GLB) | `um comfy model3d concept.png` | Hunyuan3D 2 (core nodes). **Shape only, no texture.** |
| Auto-rig | `um rig char.glb out.glb` (Blender Rigify, humanoids) | not ComfyUI: `um comfy rig` only runs your own `--workflow` |
| Sound effect | `um comfy sfx "laser shot, punchy" --seconds 1.5` | Stable Audio Open 1.0 + t5_base (≤ 47 s) |
| Music | `um comfy music "boss battle, chiptune, 150 bpm" --seconds 90` | ACE-Step (falls back to Stable Audio Open) |
| Voice line | `um comfy voice "You dare?" [--voice male_01 \| --ref actor.wav] [--engine F5TTSEngineNode]` | the TTS Audio Suite node pack (default engine ChatterBox, ~2 GB on first use; F5-TTS ~1.4 GB). `--voice` picks one of the pack's example voices, `--ref` clones your own sample |
| Cutscene clip | `um comfy video still.png "camera orbits"` | Wan 2.2 TI2V 5B (≤ 5 s at 24 fps) |
| Anything else | `um comfy run wf_api.json prompt="..." KSampler.steps:=40 image=@ref.png` | whatever the workflow uses |

All recipes take:
- `--seed`, `--steps`, `--cfg`;
- `--set Class.input=value` (or `node_id.input:=json`) to patch any node;
- `--workflow` to swap the whole graph.

Image recipes also take `--ckpt`, `--lora name:strength` (repeatable) and `--negative`.

## How the built-in workflows work
- **Built from the live server.** They are assembled from ComfyUI's `/object_info`:
  - links are matched by type;
  - inputs you don't set get the node's defaults;
  - model files are picked by name.

  So they survive ComfyUI updates, and a missing node or file shows up as a clear error, not a broken graph.
- **Sampler settings follow the checkpoint name.** `flux` gets cfg 1 and FluxGuidance; `turbo`/`lightning`
  gets few steps; SD 1.5 gets 512 px. Override with `--steps` / `--cfg`.
- **Transparent inputs are flattened on white before upload.** This applies to edit, model3d and video, because
  `LoadImage` keeps junk colour under alpha 0.

## Things that surprise people
- **SDXL ignores view directions** ("side view facing left" often comes out front-on). Add a pixel-art LoRA
  and check the result; for a fixed orientation, mirror or regenerate rather than fight the prompt.
- **Sprites come out big** (about 1000 px). Scale once to the engine's frame:
  `um sprite pixelate in.png out.png --size 64x64 --colors 24`.
- **`model3d` is shape only**, with a dense mesh (hundreds of thousands of triangles) and no texture. Decimate
  it in Blender, or render it to sprites with `um render3d`.
- **`model3d` → sprites works end to end:** `um render3d drone.glb out/ --preset side --canvas 128 --length 110
  --engine eevee` renders the untextured Hunyuan3D mesh in about 20 s (grey clay look; texture it in Blender or
  paint the sprite afterwards). Blender is found on PATH, via `BLENDER=`, or in `Program Files\Blender Foundation`
  on any drive.
- **A node pack can fail silently.** Some packs catch their own error and return an empty clip, so ComfyUI says
  "success". `um comfy` flags silent `sfx` / `music` / `voice` output (needs ffmpeg) and prints the last errors from
  ComfyUI's log; `um comfy logs` shows the log tail. A common cause is a pack whose Python dependencies didn't
  install ("... not available - check installation"): fix that in ComfyUI Manager, not in `um`.
- **ACE-Step music is quiet** (around -30 dB mean): `ffmpeg -i x.flac -af loudnorm=I=-16 x.wav`.
- **Stable Audio Open stops at 47 s**; use ACE-Step for longer tracks.
- **TTS engines download their weights on first use** (the node pack does it, not `um comfy`), so the first
  `voice` call per engine is slow and needs disk space. `um comfy check` lists the engines the pack offers.
  Credit the engine in the mod's README; the manifest records it under `models`.
- **Some TTS engines refuse to speak without a reference voice** (F5-TTS, IndexTTS, CosyVoice, VibeVoice): `um comfy
  voice` gives them the pack's `male_01` example unless you pass `--voice` or `--ref`. A broken Python environment
  shows up as a silent clip plus the real error in `um comfy logs` (a half-removed package, e.g. scikit-learn
  without `sklearn/utils`, breaks `transformers` and with it most TTS packs; reinstall it with
  `pip install --ignore-installed --no-deps <pkg>==<version>` in ComfyUI's venv, with ComfyUI closed because Windows
  locks DLLs it has loaded).
- **Cloud "partner" nodes** (ElevenLabs, HeyGen, ...) show up in ComfyUI too. They are paid API nodes, not local
  models, and `um comfy` never picks them for `voice`.
- **Node updates change inputs.** `check` builds every recipe against the live server, so a ComfyUI update
  that renames a node shows up there first.
- **After installing a node pack, ComfyUI restarts** and `um comfy` fails with "can't reach ComfyUI" until it
  is back. Wait, then re-run `um comfy check`.

## Better results with your own workflows
The built-ins are deliberately plain. For hero assets, use what the user's ComfyUI already does well:
1. Build the workflow in ComfyUI.
2. Export it: Workflow → Export (API).
3. Replace the values with placeholders: `"{{prompt}}"`, `"{{seed}}"`, `"{{image}}"`, `"{{prefix}}"` for
   `filename_prefix`, ... (the full list is in `um comfy --help`).
4. Pass it with `--workflow`, or save it as `$UM_HOME/comfy_workflows/<recipe>.json` to make it that recipe's
   default.

Post-processing still runs on the result: sprite cut-out, texture tiling, PBR maps. Workflows worth setting up:
- **Edit:** Qwen-Image-Edit or Flux Kontext for consistent variants and multiple references. The built-in
  img2img only changes looks, not poses.
- **3D:** Hunyuan3D 2.1 or TRELLIS node packs for textured meshes.
- **Rigging:** a UniRig node pack.

## Prompting and pipeline
The same rules as the fal-assets skill apply:
- copy the game's own style, view and orientation;
- flat backgrounds;
- one hero image, then variants;
- go 3D for many angles (`um comfy model3d` → `um render3d`).

The asset-pipeline skill turns the outputs into files the game accepts. Audio comes out as FLAC: convert it
with `ffmpeg -i x.flac -ar 44100 x.wav` (or `.ogg`).

## Licenses and credits
- **Every call is recorded.** It appends to `<out>/comfy_manifest.jsonl`: models, seed, prompt id, files and the
  full API workflow. To regenerate an asset, pass that line's workflow back with `um comfy run`.
- **Check model licenses before a mod ships.** Some are non-commercial or restricted: Flux dev, Stable Audio
  Open, Hunyuan3D (territory limits) and some LoRAs.
- **Credit the models in the README.** `um publish check` warns when a manifest's models aren't named there.
