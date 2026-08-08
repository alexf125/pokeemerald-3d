# Voxel converter

This directory contains a small Python pipeline that turns pokeemerald-3d map
layout data into simple voxel geometry for inspection and browser preview.

## What it reads

- `data/layouts/layouts.json` for layout size, map binaries, and tileset symbols
- `data/maps/<MapName>/map.json` for map-to-layout lookup
- `data/tilesets/*/*/metatiles.bin`
- `data/tilesets/*/*/metatile_attributes.bin`
- `data/tilesets/*/*/tiles.png`
- `data/tilesets/*/*/palettes/*.pal`
- `include/constants/metatile_behaviors.h`

## Repo-specific notes

The current repo stores metatiles as **8 tile entries per metatile**: 4 bottom
layer tiles plus 4 top layer tiles. The converter samples the tileset atlas
directly, tracks per-block opacity/row coverage, and emits **4×4 voxel columns
per metatile** so each exported tile keeps sub-voxel color detail while still
fitting MagicaVoxel-sized exports.

Voxel shape resolution combines map block elevation, metatile behavior, and
local tile context in priority order:

- authored overrides (if any are configured in the generator)
- detected building facades anchored from door/front tiles
- sparse per-block object silhouettes based on drawn opacity
- tile-level fallbacks for water, rock/cliff, structure, and walkable ground

That allows the generator to:

- keep regular elevation levels at `voxel_height = elevation * 2`
- collapse multi-row building art into raised front facades instead of leaving
  the roof rows behind as flat depressions
- preserve `ELEVATION_MULTI_LEVEL` tiles by inheriting nearby walkable heights
- emit stair/ladder/escalator ramps as local per-block gradients
- treat sparse props as measured-height voxel objects instead of solid slabs

glTF / GLB exports also apply directional face shading and simple ambient
occlusion so the raised geometry reads more clearly in viewers.

The generator still keeps one visible surface voxel even at ground level so
flat terrain remains visible in exported models.

## Usage

From the repository root:

```sh
python tools/voxel_converter/convert.py LittlerootTown --format vox --format gltf --format json
```

Outputs are written to `build/voxels/` by default:

- `build/voxels/LittlerootTown.vox`
- `build/voxels/LittlerootTown.gltf`
- `build/voxels/LittlerootTown.json`

You can also choose a different output directory:

```sh
python tools/voxel_converter/convert.py Route101 --format all --output-dir /tmp/voxels
```

## Material mapping

`behavior_map.json` maps `MB_*` behavior names to coarse voxel materials:

- `ground`
- `grass`
- `tall_grass`
- `water`
- `sand`
- `rock`
- `ice`
- `structure`

Unmapped behaviors fall back to:

- `structure` for covered/split or colliding metatiles
- `ground` otherwise

## Export formats

- `.vox` — MagicaVoxel-compatible palette voxels
- `.gltf` — glTF 2.0 with vertex colors and material alpha for water/ice
- `.glb` — binary glTF variant for direct viewer loading
- `.json` — full debug dump of parsed voxel output

## Adding new behavior mappings

1. Find the behavior symbol in `include/constants/metatile_behaviors.h`
2. Add it to `behavior_map.json`
3. Re-run the converter for the target map
