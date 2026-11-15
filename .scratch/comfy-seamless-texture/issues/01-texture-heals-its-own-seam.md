# 01: `um comfy texture` heals its own seam

**What to build:** `um comfy texture "<prompt>"` returns a texture that repeats with no visible seam, with no finishing step in the user's project. Today the local tiling leaves the raw image's edges as a faint cross through the middle of each tile, so a line repeats once per tile. The command heals it the way Bonsai did by hand: roll the tiled result by half a tile (the cross moves to the edges, the seamless edges to the middle), repaint the centre with an img2img pass at about strength 0.5 with the same prompt and seed, and blend the repaint in with a feathered mask that keeps the rolled pixels near the tile edges. `um comfy pbr` gets the same, since it tiles the same way before deriving its maps. A flag turns the heal off for anyone who wants the old output.

Evidence: Bonsai (micrus24/bonsai) retro 0028; its `tools/finish-texture.py` is a working reference, and its MODLOG records that a second heal pass over a finished texture paints a pebbly speckle (a dead end) and that a quiet, low-contrast texture can keep a faint tone step at its edges.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A `texture` result tiled 2x2 shows no line through the tile centre or at the tile edges (image opened)
- [ ] A `pbr` result's base colour and maps tile the same way (images opened)
- [ ] The heal reuses the run's prompt and seed, and the manifest records the heal pass
- [ ] A flag skips the heal and gives today's output
- [ ] A test covers the roll-and-blend step without ComfyUI (the mask keeps the rolled edges, the result wraps)
- [ ] The comfy-assets skill's "`texture` tiles, but not seamlessly" note becomes one line saying the heal is built in
