"""Differential scenarios for named hooks, all listeners, stack, counters, and lifetimes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess

from compare_primitives import canonical, compare, integer, string
import wordpress_reference as reference
import compatibility_registry as registry_tool


def cases():
    output = []
    definitions = {name: {} for name in ["a", "b", "c", "d"]}
    normal = {"kind": "filter", "value": "start", "extra": ["extra"]}

    def add(name, initial, callbacks=None, calls=None):
        output.append({"id": name, "callbacks": callbacks or definitions, "initial": [{"hook": hook, "id": ident, "priority": priority, "accepted": accepted} for hook, ident, priority, accepted in initial], "calls": calls or [normal, {"kind": "action", "args": []}, normal]})

    add("empty_counts", [], calls=[normal, {"kind": "action", "args": []}, {**normal, "hook": "missing"}])
    add("normal_order", [("alpha", "a", 0, 1), ("alpha", "b", 10, 2)])
    add("all_unsliced", [("all", "c", 10, 0), ("all", "d", 20, -1), ("alpha", "a", 10, 1)])
    add("all_without_named_target", [("all", "c", 10, 0)])
    add("all_called_as_named_hook", [("all", "c", 10, 1), ("all", "d", 20, 0)], calls=[{**normal, "hook": "all"}, {"kind": "action", "hook": "all", "args": []}])
    for kind in ["filter", "action"]:
        nested = {"kind": kind, "hook": "beta", "value": "nested", "args": ["nested_action"], "extra": ["nested_extra"]}
        add(f"nested_{kind}", [("alpha", "a", 10, 1), ("beta", "b", 10, 1), ("all", "c", 10, 0)], callbacks={**definitions, "a": {"mutations": [nested]}})
    changes = [
        ("active_rebind", [{"kind": "clear"}, {"kind": "add", "id": "d", "priority": 20}, {"kind": "filter", "value": "nested"}]),
        ("all_self_remove", [{"kind": "clear", "hook": "all"}]),
        ("all_remove_target", [{"kind": "clear", "hook": "alpha"}]),
        ("all_remove_both", [{"kind": "clear", "hook": "all"}, {"kind": "clear", "hook": "alpha"}]),
        ("all_register_absent", [{"kind": "add", "hook": "beta", "id": "d", "priority": 10}]),
        ("all_rebind_then_nested", [{"kind": "clear", "hook": "all"}, {"kind": "add", "hook": "all", "id": "d", "priority": 10}, {"kind": "filter", "hook": "beta", "value": "nested"}]),
    ]
    for name, mutations in changes:
        initial = [("alpha", "a", 10, 1), ("alpha", "b", 10, 1), ("alpha", "d", 30, 1)] if name == "active_rebind" else [("all", "a", 10, 0), ("all", "b", 10, 0), ("alpha", "c", 10, 1)]
        add(name, initial, callbacks={**definitions, "a": {"mutations": mutations}}, calls=[normal, {**normal, "hook": "beta"}, normal])
    for keep_named in [True, False]:
        mutations = [{"kind": "clear", "hook": "all"}]
        if not keep_named:
            mutations.append({"kind": "clear", "hook": "alpha"})
        case_id = "all_self_remove_keep_named_stack" if keep_named else "all_self_remove_remove_named_stack"
        initial = [("all", "a", 10, 0), ("alpha", "c", 10, 1)]
        callbacks = {**definitions, "a": {"mutations": mutations}}
        add(case_id, initial, callbacks=callbacks, calls=[normal])

    generator = random.Random(71305)
    names = ["alpha", "beta", "all"]
    priorities = [-5, 0, 5, 10, 20]
    for index in range(200):
        initial = [(generator.choice(names), generator.choice(["a", "b", "c", "d"]), generator.choice(priorities), generator.choice([-1, 0, 1, 3])) for _ in range(generator.randrange(1, 10))]
        mutations = []
        for _ in range(generator.randrange(1, 4)):
            kind = generator.choice(["add", "remove", "clear", "filter", "action"])
            mutation = {"kind": kind, "hook": generator.choice(names)}
            if kind in ["add", "remove"]:
                mutation.update(id=generator.choice(["a", "b", "c", "d"]), priority=generator.choice(priorities), accepted=generator.choice([-1, 0, 1, 3]))
            elif kind == "clear": mutation["priority"] = generator.choice([None, *priorities])
            elif kind == "filter": mutation.update(value="generated_nested", extra=["extra"])
            else: mutation["args"] = ["generated_action", "extra"]
            mutations.append(mutation)
        calls = [{**normal, "hook": generator.choice(names)}, {"kind": "action", "hook": generator.choice(names), "args": generator.choice([[], ["action"]])}, normal]
        add(f"generated_{index:03d}", initial, callbacks={**definitions, "a": {"mutations": mutations}}, calls=calls)
    arrays=[{"type":"array","entries":[]},{"type":"array","entries":[{"key":string(b"01"),"value":{"type":"null"}},{"key":integer(-2),"value":string(b"\0\xff")}]}]
    pool=[{"type":"null"},{"type":"bool","value":False},{"type":"bool","value":True}]
    pool += [integer(value) for value in [-(2**63),-1,0,2**63-1]]
    pool += [{"type":"float","value":value} for value in ["-0","nan","inf","-inf"]]
    pool += [string(value) for value in [b"",b"\0\xff","日本語🦀".encode()]]+arrays

    def typed(name,initial,callbacks,calls):
        add("typed_"+name,initial,callbacks,calls)
        output[-1]["typed"]=True

    for index,value in enumerate(pool):
        following=pool[(index+1)%len(pool)]
        calls=[{"kind":"filter","value":value,"extra":[following,{"type":"null"}]},{"kind":"action","args":[value]},{"kind":"action","args":[]},{"kind":"filter","hook":"missing","value":value}]
        typed(f"empty_{index}",[],{"a":{}},calls)
        for accepted in [-1,0,1,3,-(2**63),2**63-1]:
            typed(f"all_chain_{index}_{accepted}",[("all","c",0,0),("all","d",10,-1),("alpha","a",0,accepted),("alpha","b",10,1)],
                  {"a":{"operation":"constant","return_value":following},"b":{},"c":{"operation":"constant","return_value":following},"d":{"operation":"arg","index":1}},calls)
        typed(f"all_target_{index}",[("all","a",0,1),("all","b",10,0)],{"a":{"operation":"constant","return_value":following},"b":{}},
              [{"kind":"filter","hook":"all","value":value,"extra":[following]},{"kind":"action","hook":"all","args":[]}])
    for index,value in enumerate(arrays):
        for target in ["alpha","beta"]:
            typed(f"array_copy_{index}_{target}",[(target,"a",0,1),(target,"b",10,2),("all","c",0,0)],
                  {"a":{"operation":"array_put","key":string(b"01"),"entry_value":string(b"\0\xff")},"b":{},"c":{"operation":"constant","return_value":arrays[0]}},
                  [{"kind":"filter","hook":target,"value":value,"extra":[{"type":"null"}]},{"kind":"action","hook":target,"args":[value]}])
    for index in range(60):
        value=generator.choice(pool)
        mutations=[{"kind":"clear","hook":generator.choice(names)},{"kind":"add","hook":"beta","id":"d","priority":15},
                   {"kind":"filter","hook":"beta","value":generator.choice(pool),"extra":[generator.choice(pool)]},{"kind":"action","hook":"beta","args":[generator.choice(pool)]}]
        typed(f"mutation_{index}",[("all","a",0,0),("alpha","b",0,1),("beta","c",10,1)],
              {"a":{"mutations":mutations},"b":{"operation":"constant","return_value":generator.choice(pool)},"c":{"operation":"arg","index":1},"d":{}},
              [{"kind":"filter","value":value,"extra":[generator.choice(pool)]},{"kind":"action","hook":"beta","args":[value]},{"kind":"filter","hook":"missing","value":value}])
    return output


def main(php):
    source = reference.REFERENCE / f"wordpress-{reference.VERSION}"
    archive = reference.REFERENCE / f"wordpress-{reference.VERSION}.tar.gz"
    if reference.digest_file(archive, "sha1") != reference.EXPECTED_SHA1: raise ValueError("reference archive changed")
    manifest = json.loads((source / reference.MANIFEST_NAME).read_text(encoding="utf-8"))
    original = {row["path"]: row["sha256"] for row in manifest["files"]}
    sources = {}
    for path in ["wp-includes/plugin.php", "wp-includes/class-wp-hook.php", "wp-includes/class-wp-filter-sentinel.php"]:
        sources[path] = reference.digest_file(source / path)
        if sources[path] != original[path]: raise ValueError(f"reference source changed: {path}")
    subprocess.run(["cargo", "build", "-p", "recastpress-wp", "--example", "dispatcher_probe", "--offline", "--locked"], cwd=reference.ROOT, check=True, timeout=120)
    probe = reference.ROOT / "target" / "debug" / "examples" / ("dispatcher_probe.exe" if os.name == "nt" else "dispatcher_probe")
    inputs = cases(); wire = canonical(inputs)
    rust = subprocess.run([str(probe)], input=wire, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
    oracle = subprocess.run([str(php), "-n", str(reference.ROOT / "scripts/wp_dispatcher_oracle.php"), str(source)], input=wire, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
    if rust.stderr or oracle.stderr: raise ValueError("unexpected dispatcher comparison diagnostics")
    actual = json.loads(rust.stdout); expected = json.loads(oracle.stdout)
    failures = compare(inputs, actual, expected["results"])
    output = reference.ROOT / "compatibility/evidence"; output.mkdir(exist_ok=True)
    if failures:
        (output / "dispatcher-failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")
        raise ValueError(f"{len(failures)} dispatcher mismatches: compatibility/evidence/dispatcher-failures.json")
    (output / "dispatcher-failures.json").unlink(missing_ok=True)
    tracked = sorted(registry_tool.evidence_files(registry_tool.DISPATCHER_SCOPE)["tracked_files"])
    evidence = {"format_version":1,"scope":"named_dispatcher_subset","generated_at_utc":datetime.now(timezone.utc).isoformat(),"wordpress_version":reference.VERSION,"archive_sha256":reference.digest_file(archive),"php_version":expected["php_version"],"php_binary_sha256":reference.digest_file(php),"case_count":len(inputs),"typed_value_case_count":sum(bool(case.get("typed")) for case in inputs),"legacy_string_case_count":sum(not case.get("typed") for case in inputs),"mismatches":0,"diagnostic_count":sum(len(row["diagnostics"]) for row in actual),"cases_with_diagnostics":sum(bool(row["diagnostics"]) for row in actual),"input_sha256":hashlib.sha256(wire).hexdigest(),"rust_results_sha256":hashlib.sha256(canonical(actual)).hexdigest(),"wordpress_results_sha256":hashlib.sha256(canonical(expected["results"])).hexdigest(),"source_files":sources,"tracked_files":{path:reference.digest_file(reference.ROOT/path) for path in tracked},"limitations":["Native modeled scalar/byte-string/array fixtures; PHP object/reference callback behavior and Wasm imports remain pending.","Reference-array entrypoints and PHP4-style object argument unwrapping are not implemented.","Host recursion/error handling and counter overflow are not PHP equivalence claims."]}
    (output / "dispatcher.json").write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")
    print(f"Matched {len(inputs)} named dispatcher scenarios against WordPress {reference.VERSION}, PHP {expected['php_version']}")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--php",type=Path,default=reference.REFERENCE/"php-8.5.12/php.exe"); options=parser.parse_args()
    try: main(options.php.resolve())
    except subprocess.CalledProcessError as error: parser.exit(1,f"Dispatcher command failed: {error}\n{(error.stderr or b'').decode(errors='replace')}\n{(error.stdout or b'').decode(errors='replace')}\n")
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as error: parser.exit(1,f"Dispatcher comparison failed: {error}\n")
