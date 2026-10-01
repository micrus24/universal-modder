<p align="center">
  <img src="docs/media/banner.png" alt="universal-modder" width="100%">
</p>

<p align="center">
  <b>Skills, tools and a shared knowledge base that let any AI coding agent mod almost any PC game you own.</b><br>
  Works with Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot, OpenCode, or anything that reads <code>AGENTS.md</code>.<br>
  The agent finds the game, works out the engine and the route, reads the real code, builds the mod, makes art, 3D and sound
  on your own GPU with <a href="https://github.com/comfyanonymous/ComfyUI">ComfyUI</a> (no cloud, no API key), tests it in the running game, cuts the video, and writes down what it learned for the next agent.
</p>

<p align="center">
  <a href="#install"><img alt="any agent" src="https://img.shields.io/badge/agents-Claude%20Code%20·%20Codex%20·%20Cursor%20·%20Gemini%20·%20Copilot-B6FF3B?labelColor=0A0D12"></a>
  <a href="knowledge/INDEX.md"><img alt="knowledge base" src="https://img.shields.io/badge/knowledge%20base-field%20notes-B6FF3B?labelColor=0A0D12"></a>
  <a href="https://github.com/comfyanonymous/ComfyUI"><img alt="assets from local ComfyUI" src="https://img.shields.io/badge/assets-local%20ComfyUI-B6FF3B?labelColor=0A0D12"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-B6FF3B?labelColor=0A0D12"></a>
</p>

<p align="center">
  <img src="docs/media/teaser.gif" alt="A tactical nuke in Terraria and robotaxis in Age of Empires II, both built with universal-modder" width="560">
</p>

## About this fork: local-first, no cloud
This is a fork of [rehan-remade/universal-modder](https://github.com/rehan-remade/universal-modder) (MIT; all the
modding skills, engine playbooks, the knowledge base and the `um` tools for scanning, windows, video and publishing
come from there). The two differ in where the generated art, 3D and sound come from:

| | Upstream | This fork |
|---|---|---|
| Asset generation | [fal](https://fal.ai) cloud API: `um fal`, the fal MCP server, a paid `FAL_KEY` | **Local [ComfyUI](https://github.com/comfyanonymous/ComfyUI)**: `um comfy`, your GPU, your checkpoints, no key, nothing leaves your machine |
| Rigging | fal's Meshy auto-rigger | **Blender's Rigify**: `um rig` (humanoids; bones fitted to the mesh, skin weights, idle/walk loops, a preview to check) |
| Knowledge base | upstream's notes, PRs go to upstream | the same notes; `um kb sync` and `um kb pr` target **this fork** |

What this fork adds:
- **`um comfy`**: every `um fal` recipe (`image`, `sprite`, `edit`, `rmbg`, `pixelate`, `upscale`, `texture`, `pbr`,
  `model3d`, `rig`, `sfx`, `music`, `voice`, `video`, `run`) on a ComfyUI you already run, plus `check` (which
  recipes work with the models you have, and what is missing), `models` and `logs`. Workflows are built from your
  server's own node list, silent failures inside node packs are caught, and you can swap in any workflow you
  exported from ComfyUI. See [`skills/comfy-assets`](skills/comfy-assets/SKILL.md).
- **`um rig`**: auto-rig a humanoid GLB with Rigify, headless.
- **`um publish check`** also checks that the models in a `comfy_manifest.jsonl` are credited in the README.
- Blender is found on any Windows drive; small shared helpers (`um/common.py`).

`um fal` is still here, unchanged and optional: set `FAL_KEY` to use it, ignore it otherwise. The fal MCP server is
no longer configured by default (add it by hand if you want it: see the fal-assets skill). Nothing in this fork needs a cloud account. Examples under `examples/` were built upstream with fal
and are kept as they were; each recipe they used has a `um comfy` equivalent (the output looks different, because
the models are different).

To pull in upstream changes: `git remote add upstream https://github.com/rehan-remade/universal-modder` and
`git merge upstream/main`. This fork does not send pull requests to upstream.

## Install

Pick your agent. Each gets the same skills (Agent Skills format), and the `um` CLI.

| Agent | Install |
|---|---|
| **Claude Code** | `/plugin marketplace add micrus24/universal-modder`, then `/plugin install universal-modder@universal-modder` |
| **Codex** | `codex plugin marketplace add micrus24/universal-modder`, then `codex plugin add universal-modder@universal-modder` |
| **Gemini CLI** | `gemini extensions install https://github.com/micrus24/universal-modder` |
| **VS Code / Copilot** | Enable `chat.plugins.enabled`, run **Chat: Install Plugin From Source**, and enter this repo's URL |
| **Cursor** | Cursor Marketplace, or clone (Cursor reads `AGENTS.md`) |
| **Skills only** (any agent) | `npx skills add https://github.com/micrus24/universal-modder` |
| **Anything else** | `git clone https://github.com/micrus24/universal-modder` and start your agent inside it |

Inside a clone, each agent finds the skills where it looks for them: `.agents/skills` (Codex and friends),
`.claude/skills`, `.gemini/skills` and `.github/skills` all link to `skills/`. Instructions are in
`AGENTS.md`, which `CLAUDE.md` and `GEMINI.md` point to.

**The `um` CLI.** Plugin installs and clones put it on PATH. Anywhere else:
```bash
uv tool install git+https://github.com/micrus24/universal-modder     # or: pipx install git+...
```
**For assets,** start [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and use `um comfy` (no key, no
account). `um comfy check` lists which models each recipe still needs, and the
[comfy-assets skill](skills/comfy-assets/SKILL.md) has the downloads that were tested (an SDXL checkpoint is
enough to start with sprites, textures and concept art). Rigging needs [Blender](https://www.blender.org).

*Optional cloud route:* a [fal API key](https://fal.ai/dashboard/keys) enables `um fal` (and the hosted fal MCP server, if you add it yourself):
```bash
export FAL_KEY=...
```
You also need Python 3.10+ and ffmpeg. `uv` is recommended. Blender is needed for 3D → sprite renders.
Windows games are driven natively or from WSL.

## Try it
> Mod Terraria: add a homing missile launcher and a tactical nuke that craters the world. Make the sprites with ComfyUI.

> Make a new civilization for Age of Empires II with a unique unit rendered from 3D.

> Put real Minecraft inside GTA V story mode. Minecraft's camera should follow GTA's, and its TNT should blow up GTA cars.

> What engine is `C:\Games\Foo`, and has anyone modded it before?

The agent starts with the **mod-any-game** skill and runs the same loop every time:
1. search the knowledge base;
2. recon, then pick a route;
3. set up a safe lab (saves backed up);
4. read the actual code;
5. build one working slice;
6. generate assets;
7. verify in the real game;
8. record;
9. package;
10. write a field note for the next agent.

## A knowledge base that AIs write for AIs
[`knowledge/`](knowledge/) holds **field notes**: how specific games were actually modded, decompiled and
reverse-engineered. Each note gives:
- the exact versions that worked;
- the route, and why;
- what the engine really does;
- how it was verified;
- the gotchas (symptom → cause → fix).

**Every agent that finishes a mod can open a pull request with its note**, so the next agent starts where it
left off instead of rediscovering the same traps.

```bash
um kb search "grand theft auto"                 # before you start: prior art (works outside the repo too)
um kb new --game "Hades II" --title "A new boon god" --from-scan hades --agent "Codex (gpt-6)"
um kb check knowledge/games/hades-ii/a-new-boon-god.md
um kb pr knowledge/games/hades-ii/a-new-boon-god.md --yes    # after your human says OK: branch, push, PR
```
Browse [`knowledge/INDEX.md`](knowledge/INDEX.md). Contribution rules, for humans and AIs, are in
[`CONTRIBUTING.md`](CONTRIBUTING.md): no game files, no decompiled dumps, nothing that helps cheat online,
and an honest status and verification.

## What's inside

**Skills** (`skills/`, Agent Skills format)

| Skill | What it does |
|---|---|
| `mod-any-game` | The whole loop, hard safety rules, and **12 engine playbooks**: Unity, Unreal, .NET/XNA (Terraria, Stardew, Celeste), Godot, Source 1/2, Bethesda, Minecraft, AoE2/Genie, RE Engine/FromSoft/GTA/Cyberpunk/BG3, native C++, indie engines (GameMaker, RPG Maker, Ren'Py, Paradox, Doom, HTML5, LÖVE, Java), retro decomps |
| `game-recon` | Prior field notes, engine and version, managed or native, anti-cheat, loaders, save folders, community route → `MODDING_PLAN.md` |
| `reverse-engineering` | ILSpy / Cpp2IL / Vineflower / Ghidra and IDA over MCP / Cheat Engine / Frida / RenderDoc; reverse-engineer a file format and prove it with a round trip |
| `fal-assets` | Sprites with real transparency, consistent variants, pixel art, seamless textures, PBR maps, image-to-3D, auto-rigging, SFX, music, voice, cutscene video |
| `comfy-assets` | The same recipes on a local ComfyUI: no API key, your GPU, your checkpoints, LoRAs and workflows |
| `asset-pipeline` | Art → engine-exact frames: cutout, nearest-neighbour fit, palettes, sheets, team-colour masks, 3D → 8/16-heading sprites |
| `game-automation` | Launch, screenshot (GPU-safe), click/type safely, windowed mode, crash-reporter cleanup, in-game agent bridges |
| `showcase-video` | Record the window with only the game's audio, pick moments, cut a styled video from an EDL |
| `mashup-mods` | Game inside a game: content ports, passthrough mods (worked example: Minecraft × GTA V), decomps as libraries, reimplementations |
| `publish-mod` | Lint, package per platform, credits, the post |
| `share-field-notes` | Search the knowledge base, write your own note, open the PR |

**The `um` CLI** (Python). Every command has `--help` with examples.

| | |
|---|---|
| `um scan` | Find Steam/Epic/Xbox installs; fingerprint engine and version, .NET vs native, anti-cheat, installed loaders, save folders, ranked routes |
| `um fal` | `sprite`, `image`, `edit`, `rmbg`, `pixelate`, `upscale`, `texture`, `pbr`, `model3d`, `rig`, `sfx`, `music`, `voice`, `video`, `run`, `search`, `schema`, `price`. Plain REST, with a manifest of every generation |
| `um comfy` | The same recipes against a local ComfyUI, plus `check` (what's installed or missing), `models` and `run` for any API-format workflow |
| `um sprite` | `cutout`, `fit`, `pixelate`, `palette`, `sheet`, `slice`, `frames`, `team-mask`, `seamless`, `preview` |
| `um rig` | Humanoid GLB → rigged GLB with Blender's Rigify: fitted bones, automatic skin weights, optional idle/walk loops, and a preview render to check the fit |
| `um render3d` | GLB → sprite frames from the game's camera (`aoe2`, `iso8`, `trueiso`, `topdown`, `side`, `turntable`) with Blender |
| `um win` | `shot`, `record` (gfxcapture + process-loopback audio), `drive` (input that only reaches the game), `ps`, `kill`, `launch`, `reg` |
| `um video` | `contact` sheets, `compile` (EDL → titled, beat-cut video with music), `mux`, `beats`, `first-frame` |
| `um backup` | Snapshot, diff and restore save folders |
| `um publish check` | Blocks shipping game files, decompiled code and leaked keys |
| `um kb` | The knowledge base: `search`, `show`, `new`, `check`, `index`, `sync`, `pr` |

Two no-build Windows tools ship inside the package (`um/ps1/`): WinDrive input and ProcLoopback game-only
audio, both PowerShell with embedded C#.

<p align="center"><img src="docs/media/pipeline.png" alt="3D route: fal concept to 3D to 16 AoE2 headings. 2D route: fal art to cutout to a 64x26 Terraria sprite in game." width="100%"></p>

## Built with it
*(The three examples below were built upstream with fal.)*
- **[examples/terraria-tmodloader](examples/terraria-tmodloader)**: *Fal Arsenal* for tModLoader.
  - Weapons: a homing missile launcher, a tactical nuke (crater + mushroom cloud), a chain-lightning rifle, a
    black-hole gun and an orbital strike.
  - Three new enemies and a two-phase Drone Mothership boss.
  - Every sprite came from fal.
- **[examples/aoe2-de-civ](examples/aoe2-de-civ)**: *San Franciscans* for Age of Empires II DE.
  - A new civilization with a Robotaxi unique unit and Delivery Drones, rendered from fal image-to-3D models
    at AoE2's camera angle.
  - A Transamerica Pyramid wonder.
  - A reverse-engineered `.sld` sprite writer.
- **[examples/minecraft-gta5-passthrough](examples/minecraft-gta5-passthrough)**: real Minecraft inside
  GTA V story mode.
  - A Fabric mod and a ScriptHookV + ReShade add-on exchange camera, ground and events over a local
    WebSocket.
  - Minecraft's colour + depth are depth-composited into GTA's frame.
  - Minecraft TNT, arrows and fireworks become GTA explosions and bullets, and Minecraft mobs fight the
    police.

Each has a field note with every non-obvious lesson: [knowledge/INDEX.md](knowledge/INDEX.md).

## Rules it follows
- **Single-player and offline, on games you own.** It refuses to inject into online games with anti-cheat,
  write multiplayer cheats, or bypass anti-cheat, DRM or ownership checks.
- **It never ships game files or decompiled code.** Mods ship as code, your own assets, patches or
  converters.
- **It backs up before touching saves**, and kills processes by PID only.
- **It asks before** driving your mouse and keyboard, installing loaders into game folders, or publishing,
  PRs included.

Full reasoning: [`skills/mod-any-game/references/safety.md`](skills/mod-any-game/references/safety.md).

## Credits
- Built from real agent sessions modding Terraria, Age of Empires II and GTA V × Minecraft.
- Upstream: [rehan-remade/universal-modder](https://github.com/rehan-remade/universal-modder), the project this is
  forked from (MIT); its examples were generated with [fal](https://fal.ai).
- Local generation: [ComfyUI](https://github.com/comfyanonymous/ComfyUI), Stable Diffusion XL, Hunyuan3D 2, Wan 2.2,
  ACE-Step, Stable Audio Open, ChatterBox / F5-TTS (through TTS Audio Suite), Blender and its Rigify add-on. Check each
  model's license before you ship what it made.
- Standing on the shoulders of tModLoader, genieutils-py, AoE2ScenarioParser, ScriptHookV, ReShade, Fabric,
  BepInEx, Harmony, UE4SS, REFramework, SKSE, ILSpy, Ghidra and every modding community that documented its
  game.
- The engine playbooks also draw on the September 2026 wave of AI-built mods, and on how their creators
  explained them in public.

MIT licensed. Fonts: Space Grotesk and JetBrains Mono (SIL OFL).
