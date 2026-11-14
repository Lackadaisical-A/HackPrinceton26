# Sample videos

Real wrist-camera footage from SO-100 and SO-101 arms, taken from public LeRobot datasets on the Hugging Face Hub. Use it to test the pipeline without the arm:

```powershell
uv run python scripts/eval_clips.py sample_videos                               # report for every clip
uv run python -m paul_vision --source sample_videos/wrist_so101_paper_cup.mp4   # watch one with the overlay
```

The clips are not in git (110 MB). Copy the folder between machines, or fetch them again from the table below.

None of this footage is from our camera or our objects. It shows how the pipeline behaves on realistic views, not how it will score on the demo table. Record our own clips with `--record` once the camera is on the arm.

## Sources

All datasets are licensed Apache-2.0. Each file is at `https://huggingface.co/datasets/<dataset>/resolve/main/<path>`.

| Clip | Dataset | Path | Kept | Shows |
|---|---|---|---|---|
| `wrist_so100_pill_bottle_1.mp4` | `shawntan72/pick_and_place_pill_bottle_1` | `videos/chunk-000/observation.images.gripper/episode_000000.mp4` | all | Pill bottle with a dark green cap on a wooden table, seen from the side and from above |
| `wrist_so100_pill_bottle_2.mp4` | same | `videos/chunk-000/observation.images.gripper/episode_000003.mp4` | all | Same scene, another episode |
| `wrist_so100_cup_1.mp4` | `jccj/so100_block_in_cup` | `videos/chunk-000/observation.images.wrist/episode_000000.mp4` | all | Tilted camera, coffee cup lying on its side, cluttered desk |
| `wrist_so100_cup_2.mp4` | same | `videos/chunk-000/observation.images.wrist/episode_000007.mp4` | all | Same scene, another episode |
| `wrist_so100_wood_block_at_home_1080p.mp4` | `jccj/so100_block_in_cup_at_home` | `videos/chunk-000/observation.images.wrist_left/episode_000000.mp4` | all | 1920x1080, a wooden block; none of our classes is in view |
| `wrist_so101_blue_box_720p.mp4` | `aYutaroKimura/so101_pp_blue_box` | `videos/chunk-000/observation.images.wrist/episode_000000.mp4` | all | 1280x720, a small blue block and a grey plate |
| `wrist_so101_bottle_1.mp4` | `RoboColosseum/so101-upright-bottle` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | all | Juice bottle in a dark scene, ends very close up |
| `wrist_so101_bottle_2.mp4` | same | `videos/observation.images.wrist/chunk-000/file-002.mp4` | all | Same scene, another episode |
| `wrist_so101_medicine_bottle_portrait.mp4` | `flyingturtleboop/so101_medicine_bottle_pickplace` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | first 45 s | Portrait frame (480x640), medicine bottle with an orange cap, mostly seen from above |
| `wrist_so101_medkit_bottle.mp4` | `therarelab/medkit_bottle_pickplace_so101` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | first 40 s | Pill bottles seen cap-first and a cardboard box, dark background, motion blur |
| `wrist_so101_paper_cup.mp4` | `hansonwen/makermods_pick-up-paper-cup` | `videos/observation.images.hand_cam/chunk-000/file-000.mp4` | first 40 s | Upright paper cup approached from the front; the easy case |
| `wrist_so101_cup.mp4` | `legalaspro/so101-greenblack-cube-cup-pnp-30hz` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | first 45 s | Paper cup lying on its side and a green cube |
| `wrist_so101_stack_cups.mp4` | `io-intelligence/so101_stack_cups` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | first 40 s | Several paper cups, from above and very close |
| `wrist_so101_cup_lighting.mp4` | `endoard/grab_cup_2cam_lights` | `videos/observation.images.handeye/chunk-000/file-000.mp4` | first 40 s | Plastic cup against a red backdrop under changing light |
| `wrist_so101_cup_distractors.mp4` | `Ibuprofene/2_distractors_grab_cup_2cam_v2` | `videos/observation.images.handeye/chunk-000/file-000.mp4` | first 40 s | Plastic cup among other objects |
| `wrist_so101_box.mp4` | `shilpitha-03/so101_cube_in_box_20261030_164459` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | first 45 s | Red cube and a blue tray |
| `wrist_so101_wrapped_can_dark.mp4` | `espejelomar/so101-can-butler` | `videos/observation.images.wrist/chunk-000/file-000.mp4` | first 40 s | Dark scene, foil-wrapped can, 15 FPS |

To fetch one again:

```bash
curl -L -o clip.mp4 "https://huggingface.co/datasets/<dataset>/resolve/main/<path>"                # whole file
ffmpeg -ss 0 -t 40 -i "https://huggingface.co/datasets/<dataset>/resolve/main/<path>" -c copy -an clip.mp4   # first 40 s only
```

The `file-000.mp4` files hold many episodes back to back and can be large, which is why only the start was kept.

Before adding a clip from another dataset, look at it. Camera names in these datasets are unreliable: three clips named "wrist" turned out to be side or overhead views and were thrown out.
