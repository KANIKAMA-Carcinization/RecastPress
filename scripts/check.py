"""Run the local development gates, stopping on the first failed command."""

import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SOURCE_FAILURE = "Development source freeze check failed"
SOURCE_FAILURE_NOTE = "Source freeze verification also failed after the command failure"
SOURCE_TREES = ("crates", "scripts", "tools", "experiments", "examples", "compatibility")
SOURCE_CONFIGS = ("rustfmt.toml", ".rustfmt.toml", "rust-toolchain", "rust-toolchain.toml")
SOURCE_SKIPPED_DIRS = frozenset({"target", "node_modules", "__pycache__", ".git", "docs"})
MAX_SOURCE_FILES = 10000
MAX_SOURCE_SCAN_ENTRIES = 20000
MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 128 * 1024 * 1024
MAX_SOURCE_DEPTH = 64
MAX_SOURCE_PATH_BYTES = 4096
SOURCE_READ_CHUNK = 64 * 1024


class SourceFreezeError(RuntimeError):
    def __init__(self):
        super().__init__(SOURCE_FAILURE)


def _source_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _source_stat(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _source_stamp(info):
    # Windows lstat/fstat can report different ctime semantics; compare portable fields.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _source_hash(path, before, remaining):
    if _source_link(before) or not stat.S_ISREG(before.st_mode):
        raise SourceFreezeError()
    limit = min(MAX_SOURCE_FILE_BYTES, remaining)
    if before.st_size < 0 or before.st_size > limit:
        raise SourceFreezeError()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _source_stamp(before) != _source_stamp(opened):
            raise SourceFreezeError()
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, min(SOURCE_READ_CHUNK, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise SourceFreezeError()
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (_source_link(current) or not stat.S_ISREG(current.st_mode)
                or _source_stamp(before) != _source_stamp(after)
                or _source_stamp(before) != _source_stamp(current) or size != before.st_size):
            raise SourceFreezeError()
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _snapshot_sources(root):
    """Bounded selected-file observations, not an atomic/compiler-input snapshot.

    Trust the resolved workspace root and its ancestors. Reject detected links,
    reparse points and special files in selected descendants without following
    them. Artifact directories/Markdown/pyc and compatibility/evidence are outside
    this selection. Visible stat checks do not prevent hostile races or ABA.
    """
    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise SourceFreezeError()
        result = {}
        total = 0
        scanned = 0

        def add(path, info):
            nonlocal total
            relative = path.relative_to(root).as_posix()
            if len(result) >= MAX_SOURCE_FILES or len(relative.encode("utf-8")) > MAX_SOURCE_PATH_BYTES:
                raise SourceFreezeError()
            size, digest = _source_hash(path, info, MAX_SOURCE_TOTAL_BYTES - total)
            result[relative] = (size, digest)
            total += size

        def walk(directory, info, depth):
            nonlocal scanned
            if _source_link(info) or not stat.S_ISDIR(info.st_mode) or depth > MAX_SOURCE_DEPTH:
                raise SourceFreezeError()
            entries = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    scanned += 1
                    if scanned > MAX_SOURCE_SCAN_ENTRIES:
                        raise SourceFreezeError()
                    entries.append(entry)
            for entry in sorted(entries, key=lambda item: item.name):
                path = directory / entry.name
                relative = path.relative_to(root).as_posix()
                if len(relative.encode("utf-8")) > MAX_SOURCE_PATH_BYTES:
                    raise SourceFreezeError()
                # Windows DirEntry.stat can omit file identity (dev/ino == 0).
                child = path.lstat()
                if _source_link(child):
                    raise SourceFreezeError()
                if stat.S_ISDIR(child.st_mode):
                    if entry.name in SOURCE_SKIPPED_DIRS or relative == "compatibility/evidence":
                        continue
                    walk(path, child, depth + 1)
                else:
                    if path.suffix.lower() in (".md", ".pyc"):
                        continue
                    add(path, child)

        for name in ("Cargo.toml", "Cargo.lock"):
            info = _source_stat(root / name)
            if info is None:
                raise SourceFreezeError()
            add(root / name, info)
        for name in SOURCE_CONFIGS:
            info = _source_stat(root / name)
            if info is not None:
                add(root / name, info)
        cargo_dir = root / ".cargo"
        info = _source_stat(cargo_dir)
        if info is not None:
            if _source_link(info) or not stat.S_ISDIR(info.st_mode):
                raise SourceFreezeError()
            # Never inspect .cargo credentials: only build configuration is in scope.
            for name in ("config", "config.toml"):
                candidate = cargo_dir / name
                child = _source_stat(candidate)
                if child is not None:
                    add(candidate, child)
        for name in SOURCE_TREES:
            directory = root / name
            info = _source_stat(directory)
            if info is not None:
                walk(directory, info, 0)
        if "compatibility/registry.json" not in result:
            raise SourceFreezeError()
        return result
    except SourceFreezeError:
        raise
    except (OSError, ValueError, OverflowError):
        raise SourceFreezeError() from None


class SourceFreeze:
    """One immutable baseline per main invocation; never rebased by verify."""
    def __init__(self, root):
        try:
            self._root = Path(root).resolve(strict=True)
        except (OSError, ValueError):
            raise SourceFreezeError() from None
        self._baseline = _snapshot_sources(self._root)

    def verify(self):
        if _snapshot_sources(self._root) != self._baseline:
            raise SourceFreezeError()


def run(command):
    print("Checking: " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-reference", action="store_true", help="verify fixed WP source and run isolated PHP comparisons")
    parser.add_argument("--with-password-generation", action="store_true", help="cross-verify randomly generated default WP bcrypt hashes with actual PHP; separate from deterministic API counts")
    parser.add_argument("--with-legacy-passwords", action="store_true", help="compare finite legacy MD5 and portable phpass WP verification subsets with actual PHP")
    parser.add_argument("--with-credential-stage", action="store_true", help="compare unfinalized username/email credential stage relations with actual PHP")
    parser.add_argument("--with-auth-cookies", action="store_true", help="compare prepared GET cookie/session relations with actual PHP builtin-clock brackets; no principal issuance")
    parser.add_argument("--with-process-smoke", action="store_true", help="build and start temporary loopback public/management servers")
    parser.add_argument("--with-v06-boundary", action="store_true", help="compare the fixed native PHP fixture with the Rust v0.6 continuation peer; does not run PHP-Wasm")
    parser.add_argument("--with-databases", action="store_true", help="start owned disposable MariaDB/MySQL fixtures and regenerate DB evidence")
    parser.add_argument("--with-binlog-probes", action="store_true", help="also build the isolated candidate and inspect finite row-binlog fixtures")
    parser.add_argument("--with-mysql-overlap", action="store_true", help="also run the separate owned MySQL GTID overlap bootstrap fixture")
    parser.add_argument("--with-mariadb-snapshot-bootstrap", action="store_true", help="also run the separate owned MariaDB snapshot bootstrap fixture")
    parser.add_argument("--with-mysql-rotation-handoff", action="store_true", help="also run the separate owned MySQL real-rotation RAM handoff fixture")
    parser.add_argument("--with-mysql-server-restart", action="store_true", help="also run the separate owned MySQL pre-COMMIT server crash/restart fixture")
    parser.add_argument("--with-alpha-publication", action="store_true", help="also run owned MariaDB/MySQL post saves through preparation and separate sealed public processes")
    parser.add_argument("--with-license-evidence", action="store_true", help="also compare recorded license-file observations with locked local registry archives")
    parser.add_argument("--with-linux-check", action="store_true", help="cross-check server and WP crates for installed x86_64-unknown-linux-gnu std; does not run Linux binaries")
    parser.add_argument("--with-pure-wp", action="store_true", help="also verify the native no-default-features WP profile and its crypto-free normal dependency graph")
    options = parser.parse_args()
    guard = SourceFreeze(ROOT)

    def checked_run(command):
        guard.verify()
        try:
            run(command)
        except BaseException as error:
            try:
                guard.verify()
            except SourceFreezeError:
                error.add_note(SOURCE_FAILURE_NOTE)
            raise
        guard.verify()

    checked_run(["cargo", "fmt", "--all", "--", "--check"])
    checked_run(["cargo", "test", "--workspace", "--offline", "--locked"])
    checked_run(["cargo", "clippy", "--workspace", "--all-targets", "--offline", "--locked", "--", "-D", "warnings"])
    if options.with_pure_wp:
        checked_run([sys.executable, "scripts/check_wp_pure_profile.py"])
    if options.with_linux_check:
        checked_run(["cargo", "check", "-p", "recastpress-server", "--all-targets", "--target", "x86_64-unknown-linux-gnu", "--offline", "--locked"])
        checked_run(["cargo", "check", "-p", "recastpress-wp", "--all-targets", "--target", "x86_64-unknown-linux-gnu", "--offline", "--locked"])
    checked_run([sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py", "-v"])
    checked_run([sys.executable, "-B", "compatibility/v06/validate_manifest.py", "--self-test"])
    checked_run([sys.executable, "scripts/dependency_inventory.py", "--check"])
    if options.with_v06_boundary:
        checked_run([sys.executable, "-B", "experiments/php-compat-v06-probe/run_check.py", "--self-test"])
    if options.with_license_evidence:
        checked_run([sys.executable, "scripts/dependency_license_evidence.py", "--check"])
    if options.with_reference:
        checked_run([sys.executable, "scripts/wordpress_reference.py", "verify"])
        checked_run([sys.executable, "scripts/inventory_wordpress.py", "--check"])
        checked_run([sys.executable, "scripts/export_remove_accents.py", "--check"])
        checked_run([sys.executable, "scripts/compare_primitives.py"])
        checked_run([sys.executable, "scripts/compare_hooks.py"])
        checked_run([sys.executable, "scripts/compare_dispatcher.py"])
        checked_run([sys.executable, "scripts/compatibility_registry.py"])
    if options.with_password_generation:
        checked_run([sys.executable, "scripts/compare_password_generation.py"])
        checked_run([sys.executable, "scripts/check_password_generation_evidence.py"])
    if options.with_legacy_passwords:
        checked_run([sys.executable, "scripts/compare_legacy_passwords.py"])
        checked_run([sys.executable, "scripts/check_legacy_password_evidence.py"])
    if options.with_credential_stage:
        checked_run([sys.executable, "scripts/compare_credential_stage.py"])
        checked_run([sys.executable, "scripts/check_credential_stage_evidence.py"])
    if options.with_auth_cookies:
        checked_run([sys.executable, "scripts/compare_auth_cookies.py"])
        checked_run([sys.executable, "scripts/check_auth_cookie_evidence.py"])
    if options.with_binlog_probes or options.with_mysql_overlap or options.with_mariadb_snapshot_bootstrap or options.with_mysql_rotation_handoff or options.with_mysql_server_restart:
        checked_run(["cargo", "fmt", "--manifest-path", "experiments/binlog-probe/Cargo.toml", "--", "--check"])
        checked_run(["cargo", "clippy", "--manifest-path", "experiments/binlog-probe/Cargo.toml", "--target-dir", "target/binlog-probe", "--all-targets", "--offline", "--locked", "--", "-D", "warnings"])
        checked_run(["cargo", "test", "--manifest-path", "experiments/binlog-probe/Cargo.toml", "--target-dir", "target/binlog-probe", "--offline", "--locked"])
    if options.with_databases or options.with_binlog_probes:
        for backend in ["mariadb", "mysql"]:
            command = [sys.executable, "scripts/database_smoke.py", "--backend", backend]
            if options.with_binlog_probes:
                command.append("--binlog-probe")
            checked_run(command)
    if options.with_binlog_probes:
        checked_run([sys.executable, "scripts/check_binlog_evidence.py"])
    if options.with_mysql_overlap:
        checked_run([sys.executable, "scripts/database_smoke.py", "--backend", "mysql", "--gtid-bootstrap-probe"])
        checked_run([sys.executable, "scripts/check_mysql_overlap_evidence.py"])
    if options.with_mariadb_snapshot_bootstrap:
        checked_run([sys.executable, "scripts/database_smoke.py", "--backend", "mariadb", "--mariadb-snapshot-bootstrap-probe"])
        checked_run([sys.executable, "scripts/check_mariadb_snapshot_bootstrap_evidence.py"])
    if options.with_mysql_rotation_handoff:
        checked_run([sys.executable, "scripts/database_smoke.py", "--backend", "mysql", "--mysql-rotation-handoff-probe"])
        checked_run([sys.executable, "scripts/check_mysql_rotation_handoff_evidence.py"])
    if options.with_mysql_server_restart:
        checked_run([sys.executable, "scripts/database_smoke.py", "--backend", "mysql", "--mysql-server-restart-probe"])
        checked_run([sys.executable, "scripts/check_mysql_server_restart_evidence.py"])
    if options.with_alpha_publication:
        for backend in ["mariadb", "mysql"]:
            checked_run([sys.executable, "scripts/database_smoke.py", "--backend", backend, "--alpha-publication-demo"])
        checked_run([sys.executable, "scripts/check_alpha_publication_evidence.py"])
    checked_run([sys.executable,"scripts/check_database_evidence.py"])
    if options.with_process_smoke:
        checked_run(["cargo", "build", "--workspace", "--offline", "--locked"])
        checked_run([sys.executable, "scripts/smoke.py"])
    guard.verify()
    print("Requested development checks passed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        if SOURCE_FAILURE_NOTE in getattr(error, "__notes__", ()):
            print(SOURCE_FAILURE_NOTE, file=sys.stderr)
        raise SystemExit(error.returncode)
    except SourceFreezeError:
        print(SOURCE_FAILURE, file=sys.stderr)
        raise SystemExit(1)
