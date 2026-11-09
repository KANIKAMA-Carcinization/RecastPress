"""Start only a pinned disposable local DB, run explicit Rust tests, then stop it."""

import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import uuid

import wordpress_reference as reference
from binlog_probe_result import parse_result, parse_restore_result, validate_restore_rejection, validate_process_restore
from check_database_evidence import required_files, validate_schema_generator_files
from crash_probe_result import parse_crash_result, parse_ready, validate_crash_recovery
from owned_probe import require_success_output, run_until_killed
from mysql_overlap_result import parse_overlap_result
from check_mysql_overlap_evidence import tracked_files as overlap_tracked_files
from check_mariadb_snapshot_bootstrap_evidence import parse_result as parse_maria_bootstrap_result, tracked_files as maria_bootstrap_tracked_files, SCOPE as MARIA_BOOTSTRAP_SCOPE
from check_mysql_rotation_handoff_evidence import parse_result as parse_rotation_result, tracked_files as rotation_tracked_files, SCOPE as ROTATION_SCOPE
from setup_mariadb import SHA256 as MARIADB_SHA256
from setup_mysql import FINGERPRINT as MYSQL_SIGNER


def run_hidden(command,**kwargs):
    isolated=os.environ.copy()
    for name in ["MYSQL_HOME","MYSQL_TCP_PORT","MYSQL_UNIX_PORT","MYSQL_PWD","MYSQL_TEST_LOGIN_FILE"]:isolated.pop(name,None)
    kwargs.setdefault("env",isolated)
    return subprocess.run(command,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,**kwargs)


def run_owned_probe(command, environment, timeout=30, input_bytes=None):
    """Observe/reap a captured child without implicit unbounded Popen waits."""
    from owned_probe import run_captured_probe
    return run_captured_probe(command, environment, timeout=timeout, input_bytes=input_bytes)


def disposable_client_command(backend, executable, config, *arguments, no_login_paths=False):
    """Build commands for the owned fixture's selected client transport only."""
    if backend not in ("mysql", "mariadb"):
        raise ValueError("unknown disposable DB backend")
    command=[str(executable),f"--defaults-file={config}"]
    if backend=="mysql" and no_login_paths:
        command.append("--no-login-paths")
    if backend=="mariadb":
        # The established MariaDB evidence profile is loopback plaintext. Its
        # pinned Windows client defaults to TLS, which requires credentials.
        command.append("--skip-ssl")
    command.extend(arguments)
    return command


def disposable_binlog_settings(backend, option_enabled, binlog_path):
    """Match the pinned database evidence profile inside the owned fixture."""
    if backend not in ("mysql", "mariadb"):
        raise ValueError("unknown disposable DB backend")
    if option_enabled or backend == "mariadb":
        return (f"server-id=7\nlog-bin={Path(binlog_path).as_posix()}\n"
                "binlog-format=ROW\nbinlog-row-image=FULL\n")
    return "skip-log-bin\n"


def probe_tls_certificate(data):
    """Sign a loopback SAN certificate using only this disposable server's CA."""
    openssl=Path("C:/Program Files/Git/usr/bin/openssl.exe")
    if not openssl.is_file():raise ValueError("the TLS probe requires the existing Git for Windows OpenSSL")
    isolated=os.environ.copy();isolated["MSYS_NO_PATHCONV"]="1"
    commands=[
        [str(openssl),"req","-newkey","rsa:2048","-nodes","-keyout",str(data/"probe-server-key.pem"),"-out",str(data/"probe-server.csr"),"-subj","/CN=localhost","-addext","subjectAltName=IP:127.0.0.1,DNS:localhost"],
        [str(openssl),"x509","-req","-in",str(data/"probe-server.csr"),"-CA",str(data/"ca.pem"),"-CAkey",str(data/"ca-key.pem"),"-CAcreateserial","-out",str(data/"probe-server-cert.pem"),"-days","1","-copy_extensions","copy"],
    ]
    for command in commands:
        result=run_hidden(command,env=isolated,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        if result.returncode:raise ValueError("disposable TLS certificate generation failed: "+result.stderr.decode(errors='replace'))


def main(keep,backend,exercise_cleanup=False,binlog_probe=False,gtid_bootstrap=False,mariadb_snapshot_bootstrap=False,rotation_handoff=False,server_restart=False,alpha_publication=False):
    if alpha_publication and any((exercise_cleanup,binlog_probe,gtid_bootstrap,mariadb_snapshot_bootstrap,rotation_handoff,server_restart)):
        raise ValueError("Alpha publication requires its own fresh fixture profile")
    if server_restart and (backend != "mysql" or any((binlog_probe,exercise_cleanup,gtid_bootstrap,mariadb_snapshot_bootstrap,rotation_handoff))):
        raise ValueError("Server restart requires its own fresh MySQL fixture profile")
    if rotation_handoff and (backend != "mysql" or binlog_probe or exercise_cleanup or gtid_bootstrap or mariadb_snapshot_bootstrap):
        raise ValueError("Rotation handoff requires its own MySQL fixture profile")
    if gtid_bootstrap and (backend != "mysql" or binlog_probe or exercise_cleanup or mariadb_snapshot_bootstrap or rotation_handoff):
        raise ValueError("GTID bootstrap requires its own MySQL fixture profile")
    if mariadb_snapshot_bootstrap and (backend != "mariadb" or binlog_probe or exercise_cleanup or gtid_bootstrap or rotation_handoff):
        raise ValueError("MariaDB snapshot bootstrap requires its own MariaDB fixture profile")
    log_enabled = binlog_probe or gtid_bootstrap or mariadb_snapshot_bootstrap or rotation_handoff or server_restart
    root=reference.ROOT
    version="11.8.9" if backend=="mariadb" else "8.4.13"
    server_exe="mariadbd.exe" if backend=="mariadb" else "mysqld.exe"
    client_exe="mariadb.exe" if backend=="mariadb" else "mysql.exe"
    admin_exe="mariadb-admin.exe" if backend=="mariadb" else "mysqladmin.exe"
    fixture_user="recastpress_rotation" if rotation_handoff else ("root" if backend=="mariadb" else "recastpress_fixture")
    tool=reference.REFERENCE/f"{backend}-{version}"
    if os.name!="nt" or not (tool/"bin"/server_exe).is_file():raise ValueError(f"run setup_{backend}.py first; this harness currently pins Windows development tools")
    tool_manifest=json.loads((tool/"recastpress-tool.json").read_text(encoding="utf-8"))
    expected=MARIADB_SHA256 if backend=="mariadb" else tool_manifest["archive_sha256"]
    if backend=="mysql" and tool_manifest.get("signer_fingerprint")!=MYSQL_SIGNER:raise ValueError("MySQL tool lacks verification by the pinned official release key")
    if tool_manifest["archive_sha256"]!=expected or reference.digest_file(reference.REFERENCE/f"{backend}-{version}-winx64.zip")!=expected:raise ValueError("development DB archive identity changed")
    identities={row["path"]:row["sha256"] for row in tool_manifest["files"]}
    executables=[server_exe,client_exe,admin_exe]+(["mariadb-install-db.exe"] if backend=="mariadb" else [])
    for executable in executables:
        path=f"bin/{executable}"
        if reference.digest_file(tool/path)!=identities[path]:raise ValueError("development DB executable changed")
    metadata=json.loads((root/"compatibility/schema/reference.json").read_text(encoding="utf-8"))
    validate_schema_generator_files(metadata)
    schema=root/f"compatibility/schema/wordpress-{reference.VERSION}-single-site.sql"
    if reference.digest_file(schema)!=metadata["sql_sha256"]:raise ValueError("reference DDL was modified")
    if metadata["wordpress_version"]!=reference.VERSION or metadata["context"]["table_prefix"]!="wp_":raise ValueError("unexpected reference DDL fixture context")
    built=run_hidden(["cargo","test","-p","recastpress-db","--lib","--offline","--locked","--no-run","--message-format=json","--target-dir","target"],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    if built.returncode:raise ValueError("owned DB contract build failed: "+built.stderr.decode(errors="replace"))
    artifacts=[json.loads(line) for line in built.stdout.splitlines()]
    executables=[item["executable"] for item in artifacts if item.get("reason")=="compiler-artifact"
        and item.get("target",{}).get("name")=="recastpress_db" and item.get("target",{}).get("kind")==["lib"]
        and item.get("profile",{}).get("test") is True and item.get("executable")]
    if len(executables)!=1 or not Path(executables[0]).is_file():raise ValueError("Cargo did not identify exactly one owned DB contract executable")
    contract_executable=executables[0]
    if alpha_publication:
        subprocess.run(["cargo","build","-p","recastpress-server","--bin","recastpress","--offline","--locked","--target-dir","target"],cwd=root,check=True,timeout=180)
    if log_enabled:
        subprocess.run(["cargo","build","--manifest-path","experiments/binlog-probe/Cargo.toml","--target-dir","target/binlog-probe","--offline","--locked"],cwd=root,check=True,timeout=300)
    runs=(reference.REFERENCE/"db-runs").resolve()
    if not runs.is_relative_to(root.resolve()):raise ValueError("disposable run directory is outside workspace")
    runs.mkdir(exist_ok=True)
    work=runs/f"{backend}-{uuid.uuid4().hex}";work.mkdir()
    assert work.resolve().parent==runs and work.name.startswith(backend+"-")
    password=secrets.token_hex(24)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1",0));port=reservation.getsockname()[1]
    server=None;log=None;success=False;forced_abort=False
    owned_servers=[]
    config=work/"client.ini"
    config.write_text(f"[client]\nhost=127.0.0.1\nport={port}\nuser={fixture_user}\npassword={password}\nprotocol=tcp\ndefault-character-set=utf8mb4\n",encoding="utf-8")
    client=disposable_client_command(backend,tool/"bin"/client_exe,config,
        "--batch","--skip-column-names",no_login_paths=backend=="mysql")
    original_error=None
    try:
        initialize=[str(tool/"bin/mariadb-install-db.exe"),f"--datadir={work/'data'}",f"--password={password}",f"--port={port}","--silent"] if backend=="mariadb" else [str(tool/"bin"/server_exe),"--no-defaults","--initialize-insecure",f"--basedir={tool}",f"--datadir={work/'data'}","--console"]
        initialized=run_hidden(initialize,cwd=tool,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        if initialized.returncode:raise ValueError(f"disposable DB initialization failed ({initialized.returncode}): {initialized.stderr.decode(errors='replace')}")
        server_config=work/"server.ini"
        server_text=f"[mysqld]\nbasedir={tool.as_posix()}\ndatadir={(work/'data').as_posix()}\npid-file={(work/'server.pid').as_posix()}\nbind-address=127.0.0.1\nport={port}\nskip-name-resolve\nmax-connections=8\ninnodb-buffer-pool-size=64M\nsql-mode=STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION\n"
        if backend=="mariadb":server_text+="innodb-log-file-size=32M\n"
        else:
            bootstrap=work/"bootstrap.sql"
            grants=f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{password}';\nCREATE USER '{fixture_user}'@'127.0.0.1' IDENTIFIED BY '{password}';\nGRANT ALL PRIVILEGES ON recastpress_fixture.* TO '{fixture_user}'@'127.0.0.1';\nGRANT SHUTDOWN ON *.* TO '{fixture_user}'@'127.0.0.1';\n"
            if log_enabled:grants+=f"GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO '{fixture_user}'@'127.0.0.1';\n"
            # Controller-only capability in this distinct ephemeral profile.
            # This does not prescribe privileges for production CDC readers.
            if rotation_handoff:grants+=f"GRANT RELOAD ON *.* TO '{fixture_user}'@'127.0.0.1';\n"
            bootstrap.write_text(grants,encoding="utf-8")
            server_text+="mysqlx=0\nlog-error-verbosity=3\nperformance-schema=OFF\ninnodb-redo-log-capacity=32M\n"
            if not server_restart:server_text+=f"init-file={bootstrap.as_posix()}\n"
            with config.open("a",encoding="utf-8") as output:output.write(f"ssl-mode=VERIFY_CA\nssl-ca={(work/'data/ca.pem').as_posix()}\n")
        server_text+=disposable_binlog_settings(backend,log_enabled,work/"probe-bin")
        if gtid_bootstrap:
            server_text+="gtid-mode=ON\nenforce-gtid-consistency=ON\nbinlog-order-commits=ON\nbinlog-transaction-compression=OFF\n"
        if rotation_handoff:
            server_text+="gtid-mode=OFF\nbinlog-transaction-compression=OFF\n"
        if server_restart:
            server_text+="gtid-mode=OFF\nbinlog-transaction-compression=OFF\nsync-binlog=1\ninnodb-flush-log-at-trx-commit=1\ninnodb-force-recovery=0\n"
        if log_enabled and backend=="mysql":
            probe_tls_certificate(work/'data')
            server_text+=f"ssl-ca={(work/'data/ca.pem').as_posix()}\nssl-cert={(work/'data/probe-server-cert.pem').as_posix()}\nssl-key={(work/'data/probe-server-key.pem').as_posix()}\n"
        server_config.write_text(server_text,encoding="utf-8")
        log=(work/"server.log").open("wb")
        server_command=[str(tool/"bin"/server_exe),f"--defaults-file={server_config}"]
        if backend=="mysql":server_command.append("--no-monitor")
        server_command.append("--console")
        first_command=server_command+([f"--init-file={bootstrap.as_posix()}"] if server_restart else [])
        server=subprocess.Popen(first_command,cwd=work,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        owned_servers.append(server)
        deadline=time.monotonic()+30
        while True:
            if server.poll() is not None:raise ValueError("disposable DB exited before readiness; see retained server.log")
            ready=run_hidden([*client,"--execute=SELECT VERSION(), @@datadir, @@port"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
            if ready.returncode==0:
                fields=ready.stdout.decode(errors="replace").strip().split('\t')
                expected_version=version+"-MariaDB" if backend=="mariadb" else version
                if len(fields)!=3 or expected_version not in fields[0] or Path(fields[1]).resolve()!=(work/'data').resolve() or fields[2]!=str(port):raise ValueError("local DB is not the disposable instance; refusing all DDL/data writes")
                if int((work/'server.pid').read_text(encoding='ascii').strip())!=server.pid:raise ValueError("DB worker PID differs from the owned process; refusing fixture writes")
                break
            if time.monotonic()>deadline:raise ValueError("disposable DB readiness timed out: "+ready.stderr.decode(errors="replace"))
            time.sleep(0.1)
        if exercise_cleanup:
            forced_abort=True;success=True
            print("Exercising forced cleanup of the owned DB process before fixture writes")
            return
        sql="SET SESSION sql_mode='ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';\nCREATE DATABASE recastpress_fixture CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\nUSE recastpress_fixture;\n"+schema.read_text(encoding="utf-8")
        created=run_hidden(client,input=sql.encode("utf-8"),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if created.returncode:raise ValueError("reference DDL failed on disposable DB: "+created.stderr.decode(errors="replace"))
        environment=os.environ.copy()
        environment.update(RECASTPRESS_DB_FIXTURE="disposable",RECASTPRESS_DB_PORT=str(port),RECASTPRESS_DB_USER=fixture_user,RECASTPRESS_DB_PASSWORD=password,RECASTPRESS_DB_EXPECT_VERSION=expected_version)
        environment.pop("RECASTPRESS_DB_CA",None)
        if backend=="mysql":environment["RECASTPRESS_DB_CA"]=str(work/"data/ca.pem")
        if alpha_publication:environment["RECASTPRESS_ALPHA_POST_PATH"]="disposable"
        else:environment.pop("RECASTPRESS_ALPHA_POST_PATH",None)
        contract_pid,tested=run_owned_probe([contract_executable,"database_tests::","--ignored","--nocapture"],environment,timeout=120)
        if tested.returncode:raise ValueError("Rust disposable DB contract failed:\n"+tested.stdout.decode(errors="replace")+tested.stderr.decode(errors="replace"))
        result_lines=[line.split("DB_CONTRACT_RESULT=",1)[1] for line in tested.stdout.decode().splitlines() if line.startswith("DB_CONTRACT_RESULT=")]
        if len(result_lines)!=1:raise ValueError("DB contract test did not emit exactly one result")
        result=json.loads(result_lines[0])
        if alpha_publication:
            from alpha_publication_path import run_experiment
            if type(result.get("alpha_publication_process_pid")) is not int or result["alpha_publication_process_pid"]!=contract_pid:
                raise ValueError("alpha DB contract PID differs from the captured child")
            run_experiment(root=root,work=work,backend=backend,result=result,contract_pid=contract_pid,
                database_pid=server.pid,database_alive=lambda:server.poll() is None)
            success=True
            print(f"Disposable alpha publication passed: {result['server_version']}; {len(result['checks'])} DB checks")
            return
        if server_restart:
            from mysql_server_restart import run_experiment
            run_experiment(root=root,work=work,server=server,servers=owned_servers,
                restart_command=server_command,log=log,client=client,environment=environment,
                run_hidden=run_hidden,run_probe=run_owned_probe)
            success=True
            print(f"Disposable server restart contract passed: {result['server_version']}; {len(result['checks'])} baseline checks")
            return
        if gtid_bootstrap:
            environment["RECASTPRESS_DB_DATADIR"]=str((work/'data').resolve())
            probe_binary=str(root/'target/binlog-probe/debug/recastpress-binlog-probe.exe')
            probe_pid,probe=run_owned_probe([probe_binary,"--mysql-overlap-bootstrap"],environment,timeout=60)
            summary=require_success_output(probe,"MySQL overlapping bootstrap")
            observed=parse_overlap_result(summary,expected_pid=probe_pid)
            if server.poll() is not None:raise ValueError("owned DB exited during overlapping bootstrap")
            paths=sorted(overlap_tracked_files())
            evidence={"format_version":1,"scope":"candidate_overlapping_mysql_bootstrap_fixture_only",
                "generated_at_utc":datetime.now(timezone.utc).isoformat(),"server_version":result["server_version"],
                "transport":"ca_and_name_verified_tls","process_pid":probe_pid,"result":summary,
                "tracked_files":{path:reference.digest_file(root/path) for path in paths},
                "limitations":["Independent unpublished RAM bootstrap experiment only; not an adopted production snapshot/log protocol.",
                    "Fresh MySQL 8.4.13, one retained binlog file, empty purged GTIDs, one origin, ROW/FULL, stable schema and ordered commits only.",
                    "No durable bootstrap journal, publication, rotation, source replacement, missing-log recovery or server/OS crash proof."]}
            (root/"compatibility/evidence/mysql-overlap-bootstrap.json").write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")
            print(summary,end="")
        if mariadb_snapshot_bootstrap:
            environment["RECASTPRESS_DB_DATADIR"]=str((work/'data').resolve())
            probe_binary=str(root/'target/binlog-probe/debug/recastpress-binlog-probe.exe')
            probe_pid,probe=run_owned_probe([probe_binary,"--maria-snapshot-bootstrap"],environment,timeout=60)
            summary=require_success_output(probe,"MariaDB snapshot bootstrap")
            parse_maria_bootstrap_result(summary,expected_pid=probe_pid)
            if server.poll() is not None:raise ValueError("owned DB exited during MariaDB snapshot bootstrap")
            paths=sorted(maria_bootstrap_tracked_files())
            evidence={"format_version":1,"scope":MARIA_BOOTSTRAP_SCOPE,
                "generated_at_utc":datetime.now(timezone.utc).isoformat(),"server_version":result["server_version"],
                "transport":"loopback_plaintext_test_only","process_pid":probe_pid,"result":summary,
                "tracked_files":{path:reference.digest_file(root/path) for path in paths},
                "limitations":["Independent unpublished RAM bootstrap experiment only; not an adopted production snapshot/log protocol.",
                    "Fresh MariaDB 11.8.9, one same-session read-only InnoDB snapshot anchor, one retained binlog file, ROW/FULL and stable schema only. Internal server synchronization is not excluded.",
                    "No durable bootstrap journal, publication, rotation, source replacement, missing-log recovery or server/OS crash proof."]}
            (root/"compatibility/evidence/mariadb-snapshot-bootstrap.json").write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")
            print(summary,end="")
        if rotation_handoff:
            environment["RECASTPRESS_DB_DATADIR"]=str((work/'data').resolve())
            nonce=secrets.token_hex(16)
            environment["RECASTPRESS_ROTATION_NONCE"]=nonce
            probe_binary=str(root/'target/binlog-probe/debug/recastpress-binlog-probe.exe')
            probe_pid,probe=run_owned_probe([probe_binary,"--mysql-rotation-handoff"],environment,timeout=60)
            summary=require_success_output(probe,"MySQL rotation handoff")
            parse_rotation_result(summary,expected_pid=probe_pid,expected_nonce=nonce)
            if server.poll() is not None:raise ValueError("owned DB exited during rotation handoff")
            evidence={"format_version":1,"scope":ROTATION_SCOPE,"generated_at_utc":datetime.now(timezone.utc).isoformat(),
                "server_version":result["server_version"],"transport":"ca_and_name_verified_tls","process_pid":probe_pid,"nonce":nonce,"result":summary,
                "tracked_files":{path:reference.digest_file(root/path) for path in sorted(rotation_tracked_files())},
                "limitations":["Unpublished memory-only handoff between two one-file strict sessions, not a production rotation or restart protocol.",
                    "Fresh MySQL 8.4.13 GTID OFF, ROW/FULL/CRC32, exactly two retained files, stable schema, one controller FLUSH BINARY LOGS after COMMIT. RELOAD is granted only to this ephemeral test controller, not specified as a production reader requirement.",
                    "Metadata alone never advances the outer pair. Unknown-file, missing-header and open-transaction faults are injected into the same metadata verifier; CRC faults mutate actual Rotate/FDE frames.",
                    "No durable cross-file journal, source-replacement identity, retention recovery, public generation, automatic allowance or total-memory bound is established."]}
            (root/"compatibility/evidence/mysql-rotation-handoff.json").write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")
            print(summary,end="")
        if binlog_probe:
            environment["RECASTPRESS_DB_DATADIR"]=str((work/'data').resolve())
            probe_binary=str(root/'target/binlog-probe/debug/recastpress-binlog-probe.exe')
            producer_pid,probe=run_owned_probe([probe_binary],environment)
            if probe.returncode:raise ValueError("Binlog capability probe did not pass:\n"+probe.stderr.decode(errors='replace'))
            probe_summary=probe.stdout.decode().strip()
            observed=parse_result(probe_summary,backend)
            if observed["probe_pid"] != producer_pid:raise ValueError("producer PID differs from the owned process")
            if server.poll() is not None:raise ValueError("owned DB exited before fresh-process restore")
            restore_environment=environment.copy()
            restore_environment.update(RECASTPRESS_RESTORE_FILE=observed["postmeta_source_file"],
                                       RECASTPRESS_RESTORE_POSITION=str(observed["postmeta_saved_position"]),
                                       RECASTPRESS_RESTORE_ROWS=str(observed["postmeta_saved_rows"]))
            restore_pid,restored=run_owned_probe([probe_binary,"--restore-postmeta"],restore_environment)
            if restored.returncode:raise ValueError("Fresh-process projection restore failed:\n"+restored.stderr.decode(errors="replace"))
            restore_summary=restored.stdout.decode().strip()
            parse_restore_result(restore_summary,observed,expected_pid=restore_pid)
            if server.poll() is not None:raise ValueError("owned DB exited during fresh-process restore")
            rejections=[]
            for case in ("wrong_file","wrong_position","wrong_rows","missing_fixture_flag","unknown_argument"):
                rejected_environment=restore_environment.copy()
                rejected_command=[probe_binary,"--restore-postmeta"]
                if case=="wrong_file":rejected_environment["RECASTPRESS_RESTORE_FILE"]="probe-bin.999999"
                elif case=="wrong_position":rejected_environment["RECASTPRESS_RESTORE_POSITION"]=str(observed["postmeta_saved_position"]+1)
                elif case=="wrong_rows":rejected_environment["RECASTPRESS_RESTORE_ROWS"]=str(observed["postmeta_saved_rows"]+1)
                elif case=="missing_fixture_flag":rejected_environment.pop("RECASTPRESS_DB_FIXTURE",None)
                else:rejected_command.append("--force")
                rejected_pid,rejected=run_owned_probe(rejected_command,rejected_environment)
                if rejected.stdout:raise ValueError("refused restore emitted a success/output payload")
                rejection={"case":case,"process_pid":rejected_pid,"exit_code":rejected.returncode,
                           "stderr":rejected.stderr.decode(errors="replace").strip()}
                validate_restore_rejection(rejection)
                rejections.append(rejection)
            confirmation_pid,confirmation=run_owned_probe([probe_binary,"--restore-postmeta"],restore_environment)
            if confirmation.returncode:raise ValueError("Restore after negative cases failed:\n"+confirmation.stderr.decode(errors="replace"))
            confirmation_summary=confirmation.stdout.decode().strip()
            if server.poll() is not None:raise ValueError("owned DB exited during restore refusal/confirmation")
            process_restore={"result":restore_summary,"producer_pid":producer_pid,"restore_pid":restore_pid,
                             "producer_exited_before_restore":True,"database_stayed_running":True,
                             "rejections":rejections,"confirmation_result":confirmation_summary,"confirmation_pid":confirmation_pid}
            validate_process_restore(process_restore,observed)
            crash_environment=environment.copy()
            nonce=secrets.token_hex(16)
            crash_environment["RECASTPRESS_CRASH_NONCE"]=nonce
            epoch=work.name.encode("ascii")
            killed=run_until_killed([probe_binary,"--prepare-postmeta-crash"],crash_environment,
                lambda wire,expected_pid:parse_ready(wire,expected_pid=expected_pid,nonce=nonce,epoch=epoch,producer=observed))
            if server.poll() is not None:raise ValueError("owned DB exited during client-process kill")
            resume_pid,resumed=run_owned_probe([probe_binary,"--resume-postmeta-crash"],crash_environment,input_bytes=killed.ready_line.encode("utf-8"))
            crash_summary=require_success_output(resumed,"Postmeta crash recovery")
            parse_crash_result(crash_summary,killed.ready,expected_pid=resume_pid)
            final_pid,final_restore=run_owned_probe([probe_binary,"--restore-postmeta"],restore_environment)
            final_summary=require_success_output(final_restore,"Normal restore after crash experiment")
            if server.poll() is not None:raise ValueError("owned DB exited during crash recovery/confirmation")
            crash_recovery={"ready":killed.ready_line,"process_pid":killed.process_pid,"exit_code":killed.exit_code,
                "was_running_before_kill":True,"forced_kill":True,"database_stayed_running":True,
                "nonce":nonce,"epoch_hex":epoch.hex(),"result":crash_summary,"resume_pid":resume_pid,
                "confirmation_result":final_summary,"confirmation_pid":final_pid}
            validate_crash_recovery(crash_recovery,observed,backend)
            probe_files=["experiments/binlog-probe/src/fresh_restore.rs","experiments/binlog-probe/src/postmeta_journal.rs","experiments/binlog-probe/src/postmeta_probe.rs","crates/recastpress-db/src/meta_view.rs","experiments/binlog-probe/Cargo.toml","experiments/binlog-probe/Cargo.lock","experiments/binlog-probe/src/main.rs","experiments/binlog-probe/src/buffer_adapter.rs","experiments/binlog-probe/src/durable_journal.rs","experiments/binlog-probe/src/ddl_probe.rs","experiments/binlog-probe/src/missing_log_probe.rs","experiments/binlog-probe/src/row_types_probe.rs","scripts/binlog_probe_result.py","crates/recastpress-db/src/change_buffer.rs","crates/recastpress-db/src/lib.rs","crates/recastpress-db/src/taxonomy.rs","crates/recastpress-db/src/comments.rs","crates/recastpress-db/src/users.rs","crates/recastpress-db/src/links.rs","crates/recastpress-db/Cargo.toml","Cargo.toml","scripts/database_smoke.py"]
            probe_files += ["experiments/binlog-probe/src/snapshot_anchor_probe.rs", "experiments/binlog-probe/src/mysql_snapshot_race.rs"]
            probe_files += ["experiments/binlog-probe/src/filtered_postmeta.rs", "experiments/binlog-probe/src/postmeta_process_crash.rs", "scripts/crash_probe_result.py", "scripts/owned_probe.py"]
            probe_files += ["experiments/binlog-probe/src/mysql_overlap_bootstrap.rs", "scripts/mysql_overlap_result.py", "scripts/check_mysql_overlap_evidence.py"]
            probe_files += ["experiments/binlog-probe/src/mariadb_snapshot_bootstrap.rs", "scripts/check_mariadb_snapshot_bootstrap_evidence.py"]
            probe_files += ["experiments/binlog-probe/src/mysql_rotation_handoff.rs", "scripts/check_mysql_rotation_handoff_evidence.py"]
            probe_files = sorted(set(probe_files) | required_files())
            probe_evidence={"format_version":1,"scope":"candidate_nonblocking_binlog_fixture_only","generated_at_utc":datetime.now(timezone.utc).isoformat(),"server_version":result["server_version"],"transport":"ca_and_name_verified_tls" if backend=="mysql" else "loopback_plaintext_test_only","result":probe_summary,"fresh_process_restore":process_restore,"maria_snapshot_anchor":observed["maria_snapshot_anchor"],"mysql_snapshot_race":observed["mysql_snapshot_race"],"sql_disconnect_resets":observed["sql_disconnect_resets"],"event_count":observed["events"],"verified_checksums":observed["checksums"],"committed_row_changes":3,"commits":3,"decoded_values_match":True,"rollback_row_absent":True,"corrupt_checksum_rejected":True,"buffer_applied_batches":3,"apply_failure_preserved_batch":True,"incomplete_transaction_rejected":True,"missing_frame_rejected":True,"derived_interior_positions":observed["derived_positions"],"fixture_durable_batches":3,"fixture_duplicate_receipts":1,"ack_loss_after_commit":True,"conflicting_replay_rejected":True,"source_reconnected_from_durable_checkpoint":True,"journal_connection_reopened":True,"wrong_checkpoint_context_rejected":True,"missing_log_rejected":observed["missing_log_rejected"],"missing_log_error_code":observed["missing_log_error_code"],"missing_log_driver_fatal":observed["missing_log_driver_fatal"],"checkpoint_preserved_after_missing_log":observed["checkpoint_preserved_after_missing_log"],"postmeta_source_resumed":observed["postmeta_source_resumed"],"postmeta_rollback_preserved":observed["postmeta_rollback_preserved"],"postmeta_context_rejected":observed["postmeta_context_rejected"],"postmeta_durable_commits":observed["postmeta_durable_commits"],"postmeta_durable_replays":observed["postmeta_durable_replays"],"postmeta_ack_loss_preserved":observed["postmeta_ack_loss_preserved"],"postmeta_conflict_rejected":observed["postmeta_conflict_rejected"],"postmeta_projection_reopened":observed["postmeta_projection_reopened"],"postmeta_changes":observed["postmeta_changes"],"postmeta_memory_commits":observed["postmeta_memory_commits"],"postmeta_projected_matches":observed["postmeta_projected_matches"],"postmeta_old_view_preserved":observed["postmeta_old_view_preserved"],"postmeta_apply_refusal_preserved":observed["postmeta_apply_refusal_preserved"],"typed_rows_verified":observed["typed_rows"],"typed_unsigned_reinterpreted":observed["typed_unsigned_reinterpreted"],"actual_ddl_rejected":True,"ddl_rejection_fault":"SchemaChanged","ddl_prefix_rejected":observed["ddl_prefix_rejected"],"tracked_files":{path:reference.digest_file(root/path) for path in probe_files},"limitations":["Finite synthetic fixture stream, not production CDC. MariaDB 11.8.9 positively observes a session snapshot anchor across a later writer commit; MySQL 8.4.13 negatively observes ordinary global binlog status including a commit invisible to its read-only snapshot. These finite backend experiments do not complete a production snapshot/log protocol or prove all MySQL anchoring methods impossible.","FULL row images in an owned disposable instance; general row types, GTID, rotation, server restarts, abrupt process crashes, source replacement, filtering and retention remain unverified. A normally exited producer is followed by a distinct restore process against the same still-running fixture server.","The fixture atomically journals opaque row-event bytes and position in temporary SQL tables; production data projection, durable source identity, indexes and public invalidation remain unimplemented."]}
            probe_evidence["filtered_postmeta"]=observed["filtered_postmeta"]
            probe_evidence["postmeta_logical_limit_preserved"]=observed["postmeta_logical_limit_preserved"]
            probe_evidence["postmeta_limited_commits"]=observed["postmeta_limited_commits"]
            probe_evidence["postmeta_byte_limited_indexes"]=observed["postmeta_byte_limited_indexes"]
            probe_evidence["postmeta_shared_pool_commits"]=observed["postmeta_shared_pool_commits"]
            probe_evidence["postmeta_shared_pool_refusal_preserved"]=observed["postmeta_shared_pool_refusal_preserved"]
            probe_evidence["postmeta_shared_pool_released"]=observed["postmeta_shared_pool_released"]
            probe_evidence["process_crash_recovery"]=crash_recovery
            probe_evidence["limitations"][1]="FULL row images in owned disposable instances. A normal-exit cold restore and a distinct pre-COMMIT client-process kill/rollback/finite replay on cloned source tables are verified; COMMIT-time ambiguity, DB/server/OS crashes, power loss, general types, GTID, rotation, source replacement, continuous filtering and retention remain unverified."
            (root/f"compatibility/evidence/binlog-probe-{backend}.json").write_text(json.dumps(probe_evidence,indent=2)+"\n",encoding="utf-8")
            print(probe_summary)
            print(restore_summary)
            print(crash_summary,end="")
        tracked=sorted(required_files())
        tracked.append("scripts/setup_mysql.py")
        evidence={"format_version":1,"scope":"isolated_editing_schema_reader_fixture","generated_at_utc":datetime.now(timezone.utc).isoformat(),"wordpress_version":reference.VERSION,"schema_sha256":metadata["sql_sha256"],"server_version":result["server_version"],"tls_cipher":result["tls_cipher"],"checks":result["checks"],"fixture_transport":"verified_CA_TLS" if backend=="mysql" else "loopback_plaintext_test_only","server_binary_sha256":reference.digest_file(tool/"bin"/server_exe),"tool_archive_sha256":tool_manifest["archive_sha256"],"tracked_files":{path:reference.digest_file(root/path) for path in tracked},"limitations":["Windows portable development fixture only; production Linux and the product support-version matrix remain pending.","No PHP plugin permissions, production database credentials, or existing databases are used.","Default prefix and utf8mb4_unicode_ci fixture; legacy charsets, custom physical placement, and concurrent DDL remain pending."]}
        (root/f"compatibility/evidence/database-{backend}.json").write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")
        print(f"Disposable DB contract passed: {result['server_version']}; {len(result['checks'])} recorded checks")
        success=True
    except BaseException as error:
        original_error=error
        raise
    finally:
        cleanup_errors=[]
        try:
            if server_restart:
                from mysql_server_restart import reap_owned_servers
                reap_owned_servers(owned_servers)
            elif server is not None and server.poll() is None:
                if not forced_abort:
                    try:run_hidden(disposable_client_command(backend,tool/"bin"/admin_exe,config,"shutdown"),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                    except (OSError,subprocess.SubprocessError):pass
                try:server.wait(timeout=0.1 if forced_abort else 10)
                except subprocess.TimeoutExpired:
                    # Keep the owned parent alive until taskkill captures its tree.
                    try:run_hidden(["taskkill.exe","/PID",str(server.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                    except (OSError,subprocess.SubprocessError):pass
                    try:server.wait(timeout=5)
                    except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
        except BaseException as cleanup_error:cleanup_errors.append(cleanup_error)
        try:
            from smoke import reap_servers
            reap_servers()
        except BaseException as cleanup_error:cleanup_errors.append(cleanup_error)
        try:
            from owned_probe import reap_owned_probes
            reap_owned_probes()
        except BaseException as cleanup_error:cleanup_errors.append(cleanup_error)
        if log:log.close()
        if cleanup_errors:
            success=False
            print(f"Retained disposable DB files after cleanup failure: {work}")
            if original_error is None:
                raise RuntimeError("; ".join(str(error) for error in cleanup_errors)) from cleanup_errors[0]
            for cleanup_error in cleanup_errors:original_error.add_note(str(cleanup_error))
        if success and not keep:
            resolved=work.resolve()
            if not resolved.is_relative_to(root.resolve()) or resolved.parent!=runs or not resolved.name.startswith(backend+"-"):raise ValueError("refusing cleanup outside the disposable DB run directory")
            shutil.rmtree(resolved)
        else:print(f"Retained disposable DB files: {work}")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--keep",action="store_true");parser.add_argument("--backend",choices=["mariadb","mysql"],default="mariadb");parser.add_argument("--exercise-cleanup",action="store_true");parser.add_argument("--binlog-probe",action="store_true",help="enable row logging only on the owned fixture and run the independent capability experiment");parser.add_argument("--gtid-bootstrap-probe",action="store_true",help="run the separate fresh MySQL GTID-enabled unpublished overlap experiment");parser.add_argument("--mariadb-snapshot-bootstrap-probe",action="store_true",help="run the separate fresh MariaDB unpublished snapshot bootstrap experiment");parser.add_argument("--mysql-rotation-handoff-probe",action="store_true",help="run the separate owned MySQL real-rotation RAM handoff fixture")
    parser.add_argument("--mysql-server-restart-probe",action="store_true",help="run the separate owned MySQL abrupt-server-restart fixture")
    parser.add_argument("--alpha-publication-demo",action="store_true",help="run owned post CAS saves through prepared bundles and separate sealed public processes")
    options=parser.parse_args()
    try:
        main(options.keep,options.backend,options.exercise_cleanup,options.binlog_probe,options.gtid_bootstrap_probe,options.mariadb_snapshot_bootstrap_probe,options.mysql_rotation_handoff_probe,options.mysql_server_restart_probe,options.alpha_publication_demo)
        if options.exercise_cleanup:print("Owned-process abort cleanup passed")
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:parser.exit(1,f"Database smoke failed: {error}\n")
