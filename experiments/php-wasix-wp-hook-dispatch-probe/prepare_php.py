"""Verify and extract the repository's pinned upstream PHP-WASIX artifact."""

from pathlib import Path
import hashlib
import json
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ROOT / "experiments/php-runtime-candidate-probe/artifact.json"
DEST = ROOT / ".reference/php-wasix-8.5.12"


def main():
    identity = json.loads(IDENTITY.read_text(encoding="utf-8"))
    archive = DEST / "php-wasix-64.zip"
    if archive.stat().st_size != identity["zip_bytes"]:
        raise ValueError("PHP artifact ZIP byte length mismatch")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != identity["zip_sha256"]:
        raise ValueError("PHP artifact ZIP SHA-256 mismatch")
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) != 1 or entries[0].filename != "php.wasm":
            raise ValueError("unexpected PHP artifact ZIP entries")
        entry = entries[0]
        if stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size != identity["wasm_bytes"]:
            raise ValueError("invalid php.wasm entry type or length")
        with source.open(entry) as stream:
            wasm = stream.read(identity["wasm_bytes"] + 1)
    if len(wasm) != identity["wasm_bytes"] or wasm[:8] != b"\0asm\1\0\0\0":
        raise ValueError("invalid php.wasm module or length")
    if hashlib.sha256(wasm).hexdigest() != identity["wasm_sha256"]:
        raise ValueError("PHP-WASIX artifact SHA-256 mismatch")
    output = DEST / "php.wasm"
    if output.exists():
        if hashlib.sha256(output.read_bytes()).hexdigest() != identity["wasm_sha256"]:
            raise ValueError("existing php.wasm differs from pinned artifact")
    else:
        with output.open("xb") as destination:
            destination.write(wasm)
    print("Pinned PHP-WASIX artifact verified and extracted.")


if __name__ == "__main__":
    main()
