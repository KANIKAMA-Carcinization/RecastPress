"""Derive a private fixed plugin worker from the accepted Wasm request worker."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys

import derive_guest


HERE = Path(__file__).resolve().parent
ACCEPTED = HERE.parent / 'php-wasix-wp-private-worker-probe'
CATALOG = HERE.parent / 'php-wasix-wp-request-probe/src/catalog.rs'
INPUTS = {
    'src/main.rs': 'e6dca46c07b88deb322e8c022f4f957344508e0c8fd9327b08a62a8df4d764bb',
    'Cargo.toml': '702057cff379e5d74e4ab5deac5759f16402c4e4b136d146ac95bae2b4f050a7',
    'Cargo.lock': '0f4a52e43a1bea2b8b8e4ff438cdfd1dab24fe8288d363071d8a10bae1431266',
    'catalog.rs': '6f6ec144e47dba2612b0976aea13ad6b01cfa6f1e67a925b7f3289aeb8d8aaf7',
}
PLUGIN_SHA = '038309389588dceb42897ea9638c646559dcdc3021e53b9de3bb85aca5abbd83'
PLUGIN = 'rp-admission-benign/rp-admission-benign.php'
OLD_NAME = b'recastpress-php-wasix-wp-private-worker-probe'
NAME = b'recastpress-php-wasix-wp-plugin-admission-probe'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def pinned(path, name):
    require(path.resolve(strict=True) == path and path.is_file(), 'plugin_worker_fixed_input')
    wire = path.read_bytes()
    require(sha256(wire).hexdigest() == INPUTS[name], 'plugin_worker_input_changed')
    return wire


def replace(source, previous, replacement, count=1):
    require(source.count(previous) == count, 'plugin_worker_edit_count')
    return source.replace(previous, replacement)


STAGE = '''#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
enum Stage { Activate, Render }
impl Stage {
    fn as_str(self) -> &'static str {
        match self { Self::Activate => "activate", Self::Render => "render" }
    }

    fn home(self) -> &'static [u8] {
        match self {
            Self::Activate => include_bytes!("../fixtures/home-activate.php"),
            Self::Render => include_bytes!("../fixtures/home-render.php"),
        }
    }
}

const PLUGIN: &str = "rp-admission-benign/rp-admission-benign.php";
'''


STAGE_COMPLETION = '''fn fixed_active_plugins(value: &Value) -> bool {
    value == &json!([]) || value == &json!([PLUGIN])
}

fn stage_completion(stage: Stage, value: &Value) -> bool {
    let before = &value["active_before"];
    let after = &value["active_after"];
    match stage {
        Stage::Activate => before == &json!([]) && after == &json!([PLUGIN])
            && value["activation_succeeded"] == true && value.get("response") == Some(&Value::Null)
            && value.get("context_after_dispatch").is_none()
            && ["request_dispatch_completed", "template_loader_completed", "anonymous_request", "theme_rendered"]
                .iter().all(|key| value[*key] == false),
        Stage::Render => fixed_active_plugins(before) && before == after
            && value["activation_succeeded"] == false
            && ["request_dispatch_completed", "template_loader_completed", "anonymous_request", "theme_rendered"]
                .iter().all(|key| value[*key] == true),
    }
}
'''

ACTIVATION_TEST = '''    #[test]
    fn private_activation_cannot_pass_with_a_render_or_unapproved_active_plugin() {
        let activation = json!({"active_before":[], "active_after":[PLUGIN],
            "activation_succeeded":true, "response":null,
            "request_dispatch_completed":false, "template_loader_completed":false,
            "anonymous_request":false, "theme_rendered":false});
        assert!(stage_completion(Stage::Activate, &activation));
        assert!(!stage_completion(Stage::Render, &activation));
        for (key, value) in [("response", json!({"status":200})),
            ("context_after_dispatch", json!({})), ("active_after", json!(["unapproved/plugin.php"])),
            ("theme_rendered", json!(true))] {
            let mut changed = activation.clone();
            changed[key] = value;
            assert!(!stage_completion(Stage::Activate, &changed));
        }
    }
'''


def catalog():
    source = pinned(CATALOG, 'catalog.rs').decode('utf-8')
    source = replace(source, 'dropin:Arc<[u8]>)->Result<SourceFs>',
        'dropin:Arc<[u8]>,plugin:Arc<[u8]>)->Result<SourceFs>')
    source = replace(source, '    let overlay=SourceFs::new(files,dirs)', f'''    check(sha(&plugin)=="{PLUGIN_SHA}","trial_plugin_identity")?;
    let mut dirs=dirs;
    check(dirs.insert("wp-content/plugins/rp-admission-benign".into()),"trial_plugin_dir_collision")?;
    check(files.insert("wp-content/plugins/{PLUGIN}".into(),plugin).is_none(),"trial_plugin_file_collision")?;
    let overlay=SourceFs::new(files,dirs)''')
    return replace(source, 'overlay.file_count()==3783 && overlay.directory_count()==467',
        'overlay.file_count()==3784 && overlay.directory_count()==468').encode('utf-8')


def worker():
    source = pinned(ACCEPTED / 'src/main.rs', 'src/main.rs').decode('utf-8')
    source = replace(source, '#[path = "../../php-wasix-wp-request-probe/src/catalog.rs"]\nmod catalog;', 'mod catalog;')
    source = replace(source, 'mod strict_json;',
        '#[path = "../../php-wasix-wp-private-worker-probe/src/strict_json.rs"]\nmod strict_json;')
    source = replace(source, '#[derive(serde::Deserialize)]\n#[serde(deny_unknown_fields)]\nstruct Envelope',
        STAGE + '#[derive(serde::Deserialize)]\n#[serde(deny_unknown_fields)]\nstruct Envelope')
    for old, new in (
        ('struct Envelope { schema: String, case: Case, database: db::Input }',
         'struct Envelope { schema: String, stage: Stage, database: db::Input }'),
        ('Result<(Case, db::Input)>', 'Result<(Stage, db::Input)>'),
        ('Ok((typed.case, input))', 'Ok((typed.stage, input))'),
        ('fn load_sources(root: &Path, report:', 'fn load_sources(root: &Path, stage: Stage, report:'),
        ('        dropin.context("dropin_missing")?,',
         '        dropin.context("dropin_missing")?,\n        Arc::<[u8]>::from(&include_bytes!("../fixtures/rp-admission-benign.php")[..]),'),
        ('    Ok((finite(fixtures)?, wp, manifest))',
         '''    ensure!(fixtures.insert("home.php".into(), Arc::<[u8]>::from(stage.home())).is_some(), "trial_home_overlay");
    ensure!(fixtures.insert("bootstrap.php".into(), Arc::<[u8]>::from(&include_bytes!("../fixtures/bootstrap.php")[..])).is_some(),
        "trial_bootstrap_overlay");
    Ok((finite(fixtures)?, wp, manifest))'''),
        ('"verification_scope":"compiled_fixtures_and_core_only"',
         '"verification_scope":"compiled_fixtures_core_and_admitted_plugin","trial_plugin_files":1'),
        ('target/php-wasix-wp-private-worker-probe/private-capture', 'target/php-wasix-wp-plugin-admission-probe/private-capture'),
        ('fn completion(mode: db::Mode, value: &Value)', STAGE_COMPLETION + '\nfn completion(stage: Stage, mode: db::Mode, value: &Value)'),
        ('''        && value["request_dispatch_completed"] == true
        && value["template_loader_completed"] == true
        && value["anonymous_request"] == true
        && value["theme_rendered"] == true''', '        && stage_completion(stage, value)'),
        ('    Value::Object(safe)\n}', '''    for key in ["active_before", "active_after"] {
        if fixed_active_plugins(&value[key]) {
            safe.insert(key.into(), value[key].clone());
        }
    }
    if value["activation_succeeded"].is_boolean() {
        safe.insert("activation_succeeded".into(), value["activation_succeeded"].clone());
    }
    Value::Object(safe)
}'''),
        ('fn probe(input: db::Input, case: Case, report: &mut Value) -> Result<()> {',
         'fn probe(input: db::Input, stage: Stage, report: &mut Value) -> Result<()> {\n    let case = Case::Home;'),
        ('ensure!(completion(mode, &php) && php["case"] == case.as_str()\n'
         '        && php["is_home"] == (case == Case::Home) && php["is_404"] == (case == Case::Missing)\n'
         '        && private_contexts_valid(&php, case)',
         'ensure!(completion(stage, mode, &php) && php["case"] == case.as_str()\n'
         '        && context_snapshots_valid(&php, case, &["before_bootstrap", "after_bootstrap"])'),
        ('''    commit_private_response(report, &php, case)?;
    report["phase"] = json!("full_private_response_verified");''', '''    match stage {
        Stage::Activate => {
            ensure!(private_output_ready(report), "private_activation_completion");
            report["phase"] = json!("full_private_activation_verified");
        }
        Stage::Render => {
            commit_private_response(report, &php, case)?;
            report["phase"] = json!("full_private_response_verified");
        }
    }'''),
        ('fn private_contexts_valid(value: &Value, case: Case) -> bool {', '''fn private_contexts_valid(value: &Value, case: Case) -> bool {
    context_snapshots_valid(value, case, &["before_bootstrap", "after_bootstrap", "after_dispatch"])
}
fn context_snapshots_valid(value: &Value, case: Case, stages: &[&str]) -> bool {'''),
        ('    ["before_bootstrap", "after_bootstrap", "after_dispatch"].iter().all(|stage| {',
         '    stages.iter().all(|stage| {'),
        ('"phase":"input","mode":null,"operations":[]', '"phase":"input","mode":null,"stage":null,"operations":[]'),
        ('        let (case, input) = parse_input(&wire)?;', '        let (stage, input) = parse_input(&wire)?;'),
        ('        report["case"] = json!(case.as_str());\n        probe(input, case, &mut report)',
         '        report["case"] = json!("home");\n        report["stage"] = json!(stage.as_str());\n        probe(input, stage, &mut report)'),
        ('    fn wrapped(case: &str) -> Value {', '    fn wrapped(stage: &str) -> Value {'),
        ('"case":case,\n            "database"', '"stage":stage,\n            "database"'),
        ('let valid = wrapped("home");', 'let valid = wrapped("render");'),
        (r'wire.replacen("\"case\":\"home\"", "\"case\":\"home\",\"case\":\"missing\"",1)',
         r'wire.replacen("\"stage\":\"render\"", "\"stage\":\"render\",\"stage\":\"activate\"",1)'),
        ('let (case,input) = parse_input(&serde_json::to_vec(&wrapped("missing")).unwrap()).unwrap();',
         'let (_,input) = parse_input(&serde_json::to_vec(&wrapped("activate")).unwrap()).unwrap();'),
        ('        let context = guest_context(&input,case);', '        let context = guest_context(&input,Case::Home);'),
        ('assert_eq!(context["case"],"missing")', 'assert_eq!(context["case"],"home")'),
        ('fn closed_cases_select_guest_entry_without_changing_database_mode() {\n'
         '        for (name,status) in [("home",200),("missing",404)] {\n'
         '            let (case,input) = parse_input(&serde_json::to_vec(&wrapped(name)).unwrap()).unwrap();\n'
         '            assert_eq!(input.mode,db::Mode::Home); assert_eq!(case.status(),status);\n'
         '            let env: BTreeMap<_,_> = environment(case).into_iter().collect();\n'
         '            assert_eq!(env["SCRIPT_FILENAME"],format!("/{GUEST_BASE}fixtures/{name}.php"));',
         'fn closed_stages_select_the_same_guest_entry_without_changing_database_mode() {\n'
         '        for name in ["activate","render"] {\n'
         '            let (stage,input) = parse_input(&serde_json::to_vec(&wrapped(name)).unwrap()).unwrap();\n'
         '            assert_eq!(input.mode,db::Mode::Home); assert_eq!(stage.as_str(),name);\n'
         '            let env: BTreeMap<_,_> = environment(Case::Home).into_iter().collect();\n'
         '            assert_eq!(env["SCRIPT_FILENAME"],format!("/{GUEST_BASE}fixtures/home.php"));'),
        ('completion(db::Mode::Home, &actual)', 'completion(Stage::Render, db::Mode::Home, &actual)'),
    ):
        source = replace(source, old, new)
    source = replace(source, 'load_sources(&root, report)?', 'load_sources(&root, stage, report)?', 3)
    source = replace(source, 'include_str!("../../php-wasix-wp-request-context-probe/fixtures/bootstrap.php")',
        'include_str!("../fixtures/bootstrap.php")', 2)
    source = replace(source, 'recastpress.php-wasix-wp-private-worker-input.v0.5',
        'recastpress.php-wasix-wp-plugin-admission-input.v0.5', 2)
    source = replace(source, 'recastpress.php-wasix-wp-private-worker-observation.v0.5',
        'recastpress.php-wasix-wp-plugin-admission-observation.v0.5', 2)
    source = replace(source, 'fixed_private_wp_worker_failed', 'fixed_plugin_admission_worker_failed', 2)
    source = replace(source, 'fixed_private_wp_worker_passed', 'fixed_plugin_admission_worker_passed')
    source = replace(source, 'mod tests {\n    use super::*;\n', 'mod tests {\n    use super::*;\n' + ACTIVATION_TEST)
    return source.encode('utf-8')


def outputs():
    for name in ('Cargo.toml', 'Cargo.lock'):
        require((HERE / name).read_bytes() == replace(pinned(ACCEPTED / name, name), OLD_NAME, NAME),
            'plugin_worker_dependency_derivation')
    files = derive_guest.guest_files()
    require(set(files) == {'home-activate.php', 'home-render.php', 'bootstrap.php', 'context.php',
        'helpers.php', 'adapter.php', 'db.php', 'rp-admission-benign.php'}, 'plugin_worker_guest_files')
    require(sha256(files['rp-admission-benign.php']).hexdigest() == PLUGIN_SHA, 'plugin_worker_trial_identity')
    return {'src/main.rs': worker(), 'src/catalog.rs': catalog(),
        **{'fixtures/' + name: wire for name, wire in files.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    files = outputs()
    for name, wire in sorted(files.items()):
        path = HERE / name
        require(path.resolve() == path, 'plugin_worker_output_alias')
        if path.exists():
            require(path.is_file() and path.read_bytes() == wire, 'plugin_worker_generated_changed')
        else:
            require(not args.verify_only, 'plugin_worker_generated_missing')
            path.parent.mkdir(exist_ok=True)
            with path.open('xb') as stream:
                stream.write(wire)
    for name, wire in sorted(files.items()):
        print(f'{sha256(wire).hexdigest()}  {name}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'WordPress plugin worker derivation failed: {error}', file=sys.stderr)
        sys.exit(1)
