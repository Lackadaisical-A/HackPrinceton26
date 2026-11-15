"""SO-101 joint names. Importable without the servo driver (no lerobot, no serial)."""
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
BODY = JOINTS[:-1]
