"""Derive the shared-dependency probe's database actor from the accepted session actor."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "php-wasix-wp-request-probe" / "src" / "db.rs"
SOURCE_SHA = "cadce4f3f3e9cc803f9d90da636b90a3ddc43caea644590e9c70aae598064b40"
GENERATED = HERE / "src" / "db.rs"

PARSE_CLOSE = '        "close" => check(seq > 1 && bytes.is_empty(), "close_request_shape")?,'
PARSE_DEPENDENCY = '''        "read_fixture_dependency" => check(
            seq > 1 && bytes.is_empty(),
            "read_fixture_dependency_shape",
        )?,
'''
ACTOR_START = "    pub fn start(input: Input) -> Result<Self> {"
ACTOR_START_WITH_DEPENDENCY = '''    pub fn start(input: Input, dependency: Arc<crate::dependency::Dependency>) -> Result<Self> {'''
DISPATCH_CALL = "dispatch(&input,&job.request,&job.bytes,&mut connection,&mut status,&shared,sequence)"
DISPATCH_WITH_DEPENDENCY = "dispatch(&input,&job.request,&job.bytes,&mut connection,&mut status,&shared,sequence,&dependency)"
DISPATCH_SIGNATURE_END = '''    sequence: u64,
) -> Result<(bool, Value)> {'''
DISPATCH_SIGNATURE_WITH_DEPENDENCY = '''    sequence: u64,
    dependency: &crate::dependency::Dependency,
) -> Result<(bool, Value)> {'''
DISPATCH_CLOSE = '        "close" => {'
DISPATCH_DEPENDENCY = '''        "read_fixture_dependency" => {
            check(sequence > 1 && bytes.is_empty(), "read_fixture_dependency_shape")?;
            let connection = conn.as_mut().ok_or("dependency_without_connection")?;
            Ok((true, dependency.read(connection).await?))
        }
'''
TEST_ANCHOR = '''    #[test]
    fn rpc_nonce_sequence_hex_and_utf8_escape_fail_before_dispatch() {'''
DEPENDENCY_TEST = '''    #[test]
    fn fixture_dependency_read_requires_empty_payload_after_hello() {
        let nonce = "b".repeat(48);
        let operation = "read_fixture_dependency";
        assert!(parse_request(&request(2, operation, b""), &nonce, 2).is_ok());
        assert!(parse_request(&request(1, operation, b""), &nonce, 1).is_err());
        assert!(parse_request(&request(2, operation, b"x"), &nonce, 2).is_err());
    }
'''


def derived_bytes():
    if SOURCE.resolve(strict=True) != SOURCE or not SOURCE.is_file():
        raise ValueError("actor_source_path")
    wire = SOURCE.read_bytes()
    if sha256(wire).hexdigest() != SOURCE_SHA:
        raise ValueError("actor_source_sha256")

    source = wire.decode("utf-8")
    splices = (
        (PARSE_CLOSE, PARSE_DEPENDENCY + PARSE_CLOSE),
        (ACTOR_START, ACTOR_START_WITH_DEPENDENCY),
        (DISPATCH_CALL, DISPATCH_WITH_DEPENDENCY),
        (DISPATCH_SIGNATURE_END, DISPATCH_SIGNATURE_WITH_DEPENDENCY),
        (DISPATCH_CLOSE, DISPATCH_DEPENDENCY + DISPATCH_CLOSE),
        (TEST_ANCHOR, DEPENDENCY_TEST + TEST_ANCHOR),
    )
    for original, replacement in splices:
        if source.count(original) != 1:
            raise ValueError("actor_source_splice")
        source = source.replace(original, replacement, 1)
    return source.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    wire = derived_bytes()
    if GENERATED.resolve() != GENERATED:
        raise ValueError("actor_generated_alias")
    if GENERATED.exists():
        if not GENERATED.is_file() or GENERATED.read_bytes() != wire:
            raise ValueError("actor_generated_changed")
    elif args.verify_only:
        raise ValueError("actor_generated_missing")
    else:
        GENERATED.parent.mkdir(exist_ok=True)
        with GENERATED.open("xb") as output:
            output.write(wire)
    print(sha256(wire).hexdigest())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        print("Shared dependency actor derivation failed", file=sys.stderr)
        sys.exit(1)
