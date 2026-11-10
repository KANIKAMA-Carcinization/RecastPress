"""Pin an isolated Windows PHP CLI for development comparisons, not production."""

import json
import os
from pathlib import Path
import stat
import urllib.request
import uuid
import zipfile

import wordpress_reference as reference

VERSION = "8.5.12"
FILENAME = "php-8.5.12-nts-Win32-vs17-x64.zip"
SHA256 = "2ab242af38d76bdf417cde5ec048b1bcd76a5a40f39a3d15d5f046f38571c477"
URL = "https://downloads.php.net/~windows/releases/archives/" + FILENAME
MAX_BYTES = 64 * 1024 * 1024


def main():
    if os.name != "nt":
        raise SystemExit("This helper pins the Windows x64 development CLI only; pass a separately installed PHP CLI to comparison tools on other platforms.")
    reference.REFERENCE.mkdir(exist_ok=True)
    archive = reference.REFERENCE / FILENAME
    target = reference.REFERENCE / f"php-{VERSION}"
    if not archive.exists():
        partial = reference.REFERENCE / f"php-{uuid.uuid4().hex}.part"
        with urllib.request.urlopen(URL, timeout=30) as response, partial.open("xb") as output:
            if not response.url.startswith("https://downloads.php.net/~windows/"):
                raise ValueError("unexpected PHP distribution redirect")
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("PHP archive exceeds size limit")
                output.write(chunk)
        if reference.digest_file(partial) != SHA256:
            raise ValueError("PHP download differs from pinned official SHA-256")
        partial.rename(archive)
    if reference.digest_file(archive) != SHA256:
        raise ValueError("PHP archive differs from pinned official SHA-256")
    if target.exists():
        manifest = json.loads((target / "recastpress-tool.json").read_text(encoding="utf-8"))
        if manifest["archive_sha256"] != SHA256:
            raise ValueError("PHP tool manifest differs from pinned distribution")
        for row in manifest["files"]:
            relative = reference.safe_relative("wordpress/" + row["path"])
            if reference.digest_file(target.joinpath(*relative.parts)) != row["sha256"]:
                raise ValueError("installed PHP tool was modified")
        print(target / "php.exe")
        return
    staging = reference.REFERENCE / f"php-extract-{uuid.uuid4().hex}"
    staging.mkdir()
    files = []
    seen = set()
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 10000 or sum(member.file_size for member in members) > 512 * 1024 * 1024:
            raise ValueError("PHP archive exceeds expansion limits")
        for member in members:
            relative = reference.safe_relative("wordpress/" + member.filename)
            name = relative.as_posix()
            if name.casefold() in seen or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("duplicate/link entry in PHP distribution")
            seen.add(name.casefold())
            destination = staging.joinpath(*relative.parts)
            if not destination.resolve().is_relative_to(staging.resolve()):
                raise ValueError("PHP archive escaped staging directory")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as data, destination.open("xb") as output:
                while chunk := data.read(1024 * 1024):
                    output.write(chunk)
            files.append({"path": name, "sha256": reference.digest_file(destination)})
    (staging / "recastpress-tool.json").write_text(json.dumps({"php_version": VERSION, "archive_url": URL, "archive_sha256": SHA256, "files": files}, indent=2) + "\n", encoding="utf-8")
    staging.rename(target)
    print(target / "php.exe")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit(f"PHP development tool setup failed: {error}")
