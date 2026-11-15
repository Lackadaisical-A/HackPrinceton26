"""The names vision can find right now: detector.classes plus the operator's taught tags.

Read from vision's own files, so an item Gemini names can be marked `known` (select it at once) or not
(teach it with tag_text first). Only reads; vision owns these files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def known_labels(vision_config: Path) -> list[str]:
    """Detector classes then tag names, without duplicates. [] (with a warning) if the files cannot be read."""
    try:
        cfg = yaml.safe_load(vision_config.read_text(encoding="utf-8"))
        classes = [str(c) for c in cfg["detector"]["classes"]]
        labels_dir = Path(cfg["labels"]["dir"])
        if not labels_dir.is_absolute():
            labels_dir = vision_config.parent / labels_dir
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        log.warning("cannot read the detector classes from %s: %s", vision_config, exc)
        return []
    names = list(classes)
    library = labels_dir / "library.json"
    try:
        data = json.loads(library.read_text(encoding="utf-8"))
        names += [str(t["name"]) for t in data.get("tags", [])]
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("cannot read the taught names from %s: %s", library, exc)
    return list(dict.fromkeys(names))
