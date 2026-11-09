#!/usr/bin/env python3
"""Check inherited provenance, bounded claims and current version; no PHP or DB."""

import argparse
import copy
import json
from pathlib import Path, PurePosixPath
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")
KINDS = {"RustCore", "PhpSurface", "PhpCoreDelegate", "ThirdPartyRuntime", "Unavailable"}
AXES = {"status", "integration_status", "differential_status", "retirement_status"}
DOMAINS = {"bootstrap", "query", "saving", "authentication", "hooks", "standard_ui"}
CLAIMS = {"all_wordpress_files_migrated", "all_wordpress_apis_migrated", "v06_core_implementation_complete"}


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_json(path):
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("manifest size limit")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)


def repository_file(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        return False
    resolved = (ROOT / value).resolve()
    return resolved.is_relative_to(ROOT) and resolved.is_file()


def validate(data, registry, version):
    errors = []
    if not isinstance(data, dict):
        return ["manifest must be an object"]
    if data.get("schema") != "recastpress.compatibility.v0.6":
        errors.append("unexpected schema")
    if data.get("product_version") != version:
        errors.append("product version differs from Cargo workspace")
    wp = data.get("wordpress", {})
    if not isinstance(wp, dict) or (
        wp.get("version") != registry["wordpress_version"]
        or wp.get("archive_sha256") != registry["archive_sha256"]
        or wp.get("source_inventory") != "compatibility/registry.json"
    ):
        errors.append("pinned WordPress provenance differs from inherited registry")
    if data.get("universe_enumerated") is not False:
        errors.append("this inventory remains explicitly bounded")
    for key, commit in (("baseline", "b3c819b"), ("design", "ad2c286")):
        if not isinstance(data.get(key), dict) or data[key].get("commit") != commit:
            errors.append(f"{key} commit differs from its fixed checkpoint")
    claims = data.get("claims", {})
    if not isinstance(claims, dict) or set(claims) != CLAIMS or any(value is not False for value in claims.values()):
        errors.append("bounded inventory cannot assert whole-core completion")
    if set(data.get("evidence_axes", [])) != AXES or set(data.get("implementation_kinds", [])) != KINDS:
        errors.append("evidence axes or implementation kinds differ from contract")

    domains = data.get("domains", [])
    if not isinstance(domains, list) or any(not isinstance(item, dict) for item in domains):
        return errors + ["domains must be a list of objects"]
    domain_ids = [item.get("id") for item in domains]
    if any(not isinstance(item, str) for item in domain_ids) or len(set(domain_ids)) != len(domain_ids) or not DOMAINS <= set(domain_ids):
        errors.append("required migration domains are missing or duplicated")
    for item in domains:
        label = item.get("id")
        if item.get("implementation_kind") not in KINDS:
            errors.append(f"domain {label}: invalid implementation kind")
        for axis in AXES:
            if not isinstance(item.get(axis), str) or not item[axis]:
                errors.append(f"domain {label}: missing {axis}")
        delegate = item.get("temporary_delegate")
        if delegate is not None or item.get("implementation_kind") == "PhpCoreDelegate":
            if item.get("implementation_kind") != "PhpCoreDelegate" or not isinstance(delegate, dict) or not delegate.get("scope") or not delegate.get("exit_stage"):
                errors.append(f"domain {label}: standard PHP delegate must retain scope and exit stage")

    features = data.get("features", [])
    if not isinstance(features, list) or any(not isinstance(item, dict) for item in features):
        return errors + ["features must be a list of objects"]
    expected = {}
    for kind, rows in (("api", registry["apis"]), ("component", registry["components"])):
        for row in rows:
            symbol = row.get("wordpress_symbol") or row.get("wordpress_class") or row.get("wordpress_component")
            expected[kind, symbol] = row
    seen_ids, seen_symbols = set(), set()
    for feature in features:
        label = feature.get("id")
        if not isinstance(label, str) or not label or label in seen_ids:
            errors.append("missing or duplicate feature ID")
        else:
            seen_ids.add(label)
        key = (feature.get("kind"), feature.get("symbol"))
        if key in seen_symbols or key not in expected:
            errors.append(f"{label}: unknown or duplicate inherited symbol")
            continue
        seen_symbols.add(key)
        source = expected[key]
        if feature.get("wordpress_source") != {
            "path": source["source_path"], "line": source["source_line"], "sha256": source["source_sha256"]
        }:
            errors.append(f"{label}: pinned source path/line/hash changed")
        if feature.get("rust_target") != {
            "symbol": source.get("rust_symbol", source.get("rust_type")),
            "path": source["rust_source"], "tests": source["tests"]
        }:
            errors.append(f"{label}: inherited Rust target or test mapping changed")
        if not repository_file(source["rust_source"]) or not repository_file(source["test_source"]):
            errors.append(f"{label}: inherited implementation/test file missing")
        if feature.get("source_registry_status") != source["status"] or feature.get("limitations") != source.get("limitations", []):
            errors.append(f"{label}: inherited limitations or source status changed")
        evidence = source.get("differential_evidence")
        if feature.get("historical_differential_evidence") != evidence or (evidence and not repository_file(evidence)):
            errors.append(f"{label}: historical evidence reference changed or missing")
        if feature.get("implementation_kind") != "RustCore" or feature.get("status") != "inherited_subset" or feature.get("integration_status") != "not_v06_validated" or feature.get("differential_status") != "historical_evidence_only" or feature.get("retirement_status") != "not_applicable_api_surface":
            errors.append(f"{label}: inherited subset cannot be promoted without new evidence")
    if seen_symbols != set(expected):
        errors.append("inherited API/component subset is incomplete")
    inventory = data.get("inherited_inventory", {})
    if not isinstance(inventory, dict) or inventory.get("api_count") != len(registry["apis"]) or inventory.get("component_count") != len(registry["components"]):
        errors.append("inherited inventory counts differ from source registry")
    return errors


def self_test(data, registry, version):
    cases = [
        lambda x: x.update(product_version="0.5.0"),
        lambda x: x.update(universe_enumerated=True),
        lambda x: x["claims"].update(v06_core_implementation_complete=True),
        lambda x: x["features"][0]["wordpress_source"].update(sha256="0" * 64),
        lambda x: x["features"][0]["rust_target"].update(path="../outside.rs"),
        lambda x: x["features"][0].update(differential_status="v06_passed"),
        lambda x: x["features"].append(copy.deepcopy(x["features"][0])),
        lambda x: x["features"].pop(),
        lambda x: x["domains"][0].update(temporary_delegate=None),
        lambda x: x["domains"][-1].update(implementation_kind="PhpSurface"),
        lambda x: x["features"][0].update(limitations=[]),
    ]
    for index, mutate in enumerate(cases):
        changed = copy.deepcopy(data)
        mutate(changed)
        if not validate(changed, registry, version):
            raise ValueError(f"manifest mutation {index} was accepted")
    try:
        json.loads('{"a":1,"a":2}', object_pairs_hook=unique_object)
    except ValueError:
        pass
    else:
        raise ValueError("duplicate JSON keys were accepted")
    print(f"manifest negative checks: {len(cases) + 1} passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    data = load_json(MANIFEST)
    registry = load_json(ROOT / "compatibility/registry.json")
    version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]["package"]["version"]
    errors = validate(data, registry, version)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.self_test:
        self_test(data, registry, version)
    print(f"valid inherited inventory: {len(registry['apis'])} APIs, {len(registry['components'])} components; product {version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"inventory validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
