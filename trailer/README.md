# Battle Pyramid walking capture

A twelve-second Battle Pyramid walking capture at 1920 × 1280 and 30 fps. The view transition completes after four seconds, leaving eight seconds fully zoomed out in the 3D view. The player walks inside and greets the attendant at the end.

## Re-record

```sh
# From the repository root. Omit --no-build after gameplay changes.
make wasm
node trailer/scripts/capture-gameplay.mjs --no-build
```

The script verifies the capture dimensions, visual settings, path movement, and final map position before writing `trailer/public/gameplay/walking.mp4` and the 960 × 640 README encode at `trailer/public/gameplay/walking-readme.mp4`. Temporary replay frames live under `build/capture-battle-pyramid/`.
