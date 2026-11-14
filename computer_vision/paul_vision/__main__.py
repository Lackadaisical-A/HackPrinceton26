"""Run the vision pipeline:  uv run python -m paul_vision --source wrist

Sources: `wrist` (camera.wrist_index from config.yaml), a camera index such as 0, or a video file.
Keys in the overlay window: q or Esc quits, s saves a snapshot, p pauses (subscribers then see `degraded`).
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config, validate

log = logging.getLogger("paul_vision")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m paul_vision", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", help="wrist | camera index | video file (default: source.default in the config)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to config.yaml")
    parser.add_argument("--backend", choices=["auto", "torch", "openvino"], help="override detector.backend")
    parser.add_argument("--device", help="override detector.device: auto | cpu | xpu | 0")
    parser.add_argument("--video-end", choices=["exit", "loop", "hold"], help="override source.video_end")
    parser.add_argument("--target-label", help="only target this label (override selection.preferred_label)")
    parser.add_argument("--no-overlay", action="store_true", help="run headless")
    parser.add_argument("--no-stream", action="store_true", help="do not serve the camera picture to the console")
    parser.add_argument("--max-frames", type=int, help="stop after this many processed frames (for benchmarks)")
    parser.add_argument("--record", metavar="FILE", help="also save the frames to FILE (.mp4 or .avi) for replay")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    try:
        cfg = load_config(args.config)
        detector_cfg = cfg.detector
        if args.backend:
            detector_cfg = dataclasses.replace(detector_cfg, backend=args.backend)
        if args.device:
            detector_cfg = dataclasses.replace(detector_cfg, device=args.device)
        source_cfg = dataclasses.replace(cfg.source, video_end=args.video_end) if args.video_end else cfg.source
        cfg = dataclasses.replace(cfg, detector=detector_cfg, source=source_cfg)
        validate(cfg)
        if args.target_label and args.target_label not in cfg.detector.classes:
            raise ConfigError(f"--target-label {args.target_label!r} is not one of detector.classes "
                              f"{cfg.detector.classes}")
    except (ConfigError, OSError) as exc:
        log.error("configuration: %s", exc)
        return 2

    # Imported late so that --help and config errors do not wait for torch and OpenCV to load.
    from .app import Recorder, run
    from .camera_control import CameraControl
    from .commands import CommandReceiver
    from .detector import create_detector
    from .overlay import OverlayWindow
    from .sources import open_source
    from .stream import FrameStream
    from .transport import VisionPublisher

    source = window = recorder = stream = commands = None
    try:
        try:
            source_spec = args.source if args.source is not None else cfg.source.default
            source = open_source(source_spec, cfg)
            source = CameraControl(source, source_spec, cfg, allow_switch=not bool(args.record))
            log.info("source: %s", source.description)
            turned = cfg.camera.rotate_deg in (90, 270)
            frame_shape = (cfg.camera.width, cfg.camera.height) if turned else (cfg.camera.height, cfg.camera.width)
            detector = create_detector(cfg.detector, frame_shape)
            log.info("detector ready: %s", detector.description)
            commands = CommandReceiver(cfg.transport.command_sub)
            log.info("listening for topic 'command' from %s", commands.endpoint)
            if cfg.stream.enabled and not args.no_stream:
                try:
                    stream = FrameStream(cfg.stream, camera_control=source).start()
                except OSError as exc:
                    # Guidance matters more than the picture, so this is not a reason to stop.
                    log.warning("no camera stream for the console: could not listen on %s:%d (%s). "
                                "Guidance is not affected. Change stream.port, or free the port.",
                                cfg.stream.host, cfg.stream.port, exc)
            # Started last: from here on, silence on the socket means a stalled feed, not a loading model.
            publisher = VisionPublisher(cfg.transport.vision_pub, cfg.transport.send_hwm, cfg.watchdog).start()
        except (RuntimeError, ValueError, OSError) as exc:
            log.error("startup failed: %s", exc)
            return 1
        if cfg.overlay.enabled and not args.no_overlay:
            window = OverlayWindow(Path(args.config).resolve().parent / "snapshots")
        if args.record:
            recorder = Recorder(args.record, cfg.camera.fps)
        show = stream.submit if stream is not None else None
        try:
            metrics = run(cfg, source, detector, publisher, window=window, recorder=recorder,
                          max_frames=args.max_frames, preferred_label=args.target_label, commands=commands,
                          on_result=(lambda frame, _result: show(frame.image)) if show else None)
        finally:
            publisher.close()
        print(metrics.report())
        return 0
    finally:
        if stream is not None:
            stream.close()
        if commands is not None:
            commands.close()
        if recorder is not None:
            recorder.close()
        if window is not None:
            window.close()
        if source is not None:
            source.close()


if __name__ == "__main__":
    sys.exit(main())
