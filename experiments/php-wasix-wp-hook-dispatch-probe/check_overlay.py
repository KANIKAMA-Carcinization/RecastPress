"""Rebuild the pinned WP handoff overlay and compare committed evidence."""

import json
from pathlib import Path
import tempfile

from prepare_overlay import PLUGIN, prepare

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent / "evidence" / "overlay-v5"


def main():
    with tempfile.TemporaryDirectory(prefix="recastpress-wp-hook-overlay-") as temporary:
        generated = Path(temporary) / "overlay"
        prepare(ROOT / ".reference" / "wordpress-7.1.3", generated, allow_temporary=True)
        expected_receipt = json.loads((EVIDENCE / "overlay.json").read_text(encoding="utf-8"))
        actual_receipt = json.loads((generated / "overlay.json").read_text(encoding="utf-8"))
        if actual_receipt != expected_receipt:
            raise ValueError("rebuilt overlay receipt differs from committed evidence")
        for path in (PLUGIN, Path("wp-includes/class-wp-hook.php")):
            if (generated / path).read_bytes() != (EVIDENCE / path).read_bytes():
                raise ValueError(f"rebuilt {path.as_posix()} overlay differs from committed evidence")
        generated_files = {
            item.relative_to(generated).as_posix()
            for item in generated.rglob("*")
            if item.is_file()
        }
        if generated_files != {
            "overlay.json",
            "wp-includes/plugin.php",
            "wp-includes/class-wp-hook.php",
        }:
            raise ValueError("rebuilt overlay has an unexpected file set")
    print("source-bound WordPress hook overlay verified")


if __name__ == "__main__":
    main()
