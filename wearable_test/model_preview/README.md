# Modified SO-101 display model

Generated locally from `../../so101_sim/model` by `../build_preview_model.py`.
The original license is included as `LICENSE`.

These meshes are modified: vertices are clustered on a 1 mm grid and then
reduced with quadric decimation. The manifest records the source hashes, original
and resulting triangle counts, and maximum change in axis-aligned bounds.
Material colors, visual transforms, joint transforms, axes, and limits are
preserved. Collision elements are omitted from the display URDF.

Use this copy only for the Wearable Lab animation. Use the original model for
collision geometry, physical dimensions, manufacturing, or dynamics work.

Rebuild from the repository root with:

```powershell
uv run wearable_test/build_preview_model.py --benchmark
```
