"""Run the shared-cache binlog probe on a fresh, owned Windows database."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
OWNER = SCRIPTS / "database_smoke.py"
OWNER_SHA256 = "35d71ab8eecfc5b16227647ba8b02288a3822d3ec2076e2fcbd281001252a9b1"


RESULT_PARSER = '''def parse_shared_cache_result(probe, backend):
    prefix = b"SHARED_CACHE_RESULT="
    output = probe.stdout
    if (probe.returncode or probe.stderr or len(output) > 4096
            or not output.startswith(prefix) or output.count(b"\\n") != 1 or not output.endswith(b"\\n")):
        raise ValueError("shared cache probe did not emit one successful result")

    def unique_keys(pairs):
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError("duplicate shared cache result key")
        return values

    expected = {
        "format_version": 2, "backend": backend, "commits": 1, "target_rows": 1,
        "rollback_preserved": True, "precommit_hit_preserved": True,
        "stale_fill_rejected": True, "refreshed_hit": True,
        "source_end_closed_cache": True, "live_cut_closed_cache": True, "final_charged_bytes": 0,
    }
    try:
        observed = json.loads(output[len(prefix):-1].decode("utf-8"), object_pairs_hook=unique_keys)
    except (ValueError, RecursionError):
        raise ValueError("invalid shared cache result JSON") from None
    if type(observed) is not dict or observed.keys() != expected.keys() or any(
        type(observed[key]) is not type(value) or observed[key] != value for key, value in expected.items()
    ):
        raise ValueError("shared cache result differs from the fixture contract")
    return observed


'''

RUN_PROBE = '''        environment["RECASTPRESS_DB_DATADIR"] = str((work / "data").resolve())
        probe_binary = root / "target/binlog-shared-cache-probe/debug/recastpress-binlog-probe.exe"
        probe_sha256 = reference.digest_file(probe_binary)
        probe_pid, probe = run_owned_probe([str(probe_binary), "--shared-cache-probe"], environment, timeout=30)
        observed = parse_shared_cache_result(probe, backend)
        if server.poll() is not None:
            raise ValueError("owned DB exited during the shared cache probe")
        shutdown = run_hidden([str(tool / "bin" / admin_exe), f"--defaults-file={config}", "shutdown"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if shutdown.returncode != 0 or server.wait(timeout=10) != 0:
            raise ValueError("owned shared cache DB did not shut down cleanly")
'''

WRITE_RECEIPT = '''    receipt = root / "target/binlog-shared-cache-probe" / f"windows-{work.name}.json"
    evidence = {
        "format_version": 1, "scope": "owned_shared_cache_binlog_fixture",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "server_version": result["server_version"],
        "transport": "ca_and_name_verified_tls" if backend == "mysql" else "loopback_plaintext_test_only",
        "database_owner_sha256": owner_sha256,
        "server_binary_sha256": identities[f"bin/{server_exe}"],
        "tool_archive_sha256": tool_manifest["archive_sha256"],
        "probe_pid": probe_pid, "probe_binary_sha256": probe_sha256,
        "server_exit_code": server.returncode, "fixture_retained": keep, "result": observed,
    }
    with receipt.open("x", encoding="utf-8", newline="\\n") as output:
        output.write(json.dumps(evidence, indent=2) + "\\n")
    print("SHARED_CACHE_RESULT=" + json.dumps(observed, separators=(",", ":")))
    print(f"Shared cache evidence recorded at {receipt}")
'''


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError("Windows database owner derivation anchor changed")
    return source.replace(before, after, 1)


def derive_owner():
    owner = OWNER.read_bytes()
    if hashlib.sha256(owner).hexdigest() != OWNER_SHA256:
        raise ValueError("Windows database owner identity changed")
    source = owner.decode("utf-8").replace("\r\n", "\n")
    source = replace_once(source, "def main(keep,backend,", RESULT_PARSER + "def main(keep,backend,")
    source = replace_once(source,
        "    log_enabled = binlog_probe or gtid_bootstrap or mariadb_snapshot_bootstrap or rotation_handoff or server_restart\n",
        "    log_enabled = True\n")
    source = replace_once(source,
        '    if log_enabled:\n        subprocess.run(["cargo","build","--manifest-path","experiments/binlog-probe/Cargo.toml","--target-dir","target/binlog-probe","--offline","--locked"],cwd=root,check=True,timeout=300)\n',
        '    subprocess.run([python_executable,"-B","experiments/binlog-shared-cache-probe/derive.py"],cwd=root,check=True,timeout=60)\n'
        '    subprocess.run(["cargo","build","--manifest-path","experiments/binlog-shared-cache-probe/Cargo.toml","--target-dir","target/binlog-shared-cache-probe","--offline","--locked"],cwd=root,check=True,timeout=300)\n')
    source = replace_once(source, '        if rotation_handoff:\n            server_text+="gtid-mode=OFF',
        '        if backend == "mysql":\n            server_text+="gtid-mode=OFF')
    start = '        if alpha_publication:\n            from alpha_publication_path import run_experiment\n'
    end = '        success=True\n    except BaseException as error:\n'
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Windows database owner experiment section changed")
    prefix, rest = source.split(start)
    _, cleanup = rest.split(end)
    cli = '\n\nif __name__=="__main__":\n'
    if cleanup.count(cli) != 1:
        raise ValueError("Windows database owner entry point changed")
    return prefix + RUN_PROBE + end + cleanup.split(cli)[0] + "\n\n" + WRITE_RECEIPT


def load_owner():
    namespace = {"__name__": "shared_cache_windows_owner", "__file__": str(OWNER),
                 "python_executable": sys.executable, "owner_sha256": OWNER_SHA256}
    code = compile(derive_owner(), str(OWNER), "exec")
    sys.path.insert(0, str(SCRIPTS))
    try:
        exec(code, namespace)
    finally:
        sys.path.pop(0)
    if namespace["reference"].ROOT.resolve() != ROOT:
        raise ValueError("Windows database owner repository differs from this checkout")
    return namespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("mysql", "mariadb"), required=True)
    parser.add_argument("--keep", action="store_true")
    options = parser.parse_args()
    try:
        owner = load_owner()
        # The original owner also imports cleanup modules while the fixture is running.
        sys.path.insert(0, str(SCRIPTS))
        try:
            owner["main"](options.keep, options.backend)
        finally:
            sys.path.pop(0)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Shared cache fixture failed: {error}\n")


if __name__ == "__main__":
    main()
