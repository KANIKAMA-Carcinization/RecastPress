"""Verify the frozen bootstrap host's reused bodies without changing them."""
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OLD=ROOT/'experiments/php-wasix-wp-bootstrap-probe'

# Exact before/after patches around the policy-only counters, constructor,
# read-only reporting method and its single new pure unit. Transaction bodies
# are independently compared below. No target paths are stored by the policy.
ACCESS_DIAGNOSTIC_EDITS = [
    (b'\n#[derive(Clone, Debug)]\nstruct Policy { wp:SourceFs,php:SourceFs,context:SourceFs }\nimpl Policy {\n    fn query(&self,bytes:&[u8],mode:u8)->Status {\n        if mode>7 || bytes.is_empty() || bytes.len()>MAX_PATH_BYTES {return Status::Invalid;}\n        let Ok(path)=std::str::from_utf8(bytes) else{return Status::Invalid};\n        if !path.starts_with(\'/\') || path.starts_with("//") || path.contains([\'\\0\',\'\\\\\',\':\']) || path.chars().any(char::is_control){return Status::Invalid;}\n        let mount=|root:&str| path.strip_prefix(root).filter(|rest|rest.is_empty()||rest.starts_with(\'/\'));\n        let selected=if let Some(p)=mount("/wordpress"){Some((&self.wp,p))}\n',
     b'\n#[derive(Clone, Debug)]\nstruct Policy { wp:SourceFs,php:SourceFs,context:SourceFs, invalid_reasons:Arc<Mutex<InvalidReasons>> }\nconst INVALID_REASON_NAMES:[&str;9]=["double_leading_slash","colon","backslash","control","metadata_invalid","invalid_utf8","not_absolute","path_length","mode"];\n#[derive(Debug,Default)]\nstruct InvalidReasons {counts:[usize;9]}\nimpl Policy {\n    fn invalid(&self,reason:usize)->Status {\n        let Ok(mut reasons)=self.invalid_reasons.lock() else{return Status::Io};\n        reasons.counts[reason]=reasons.counts[reason].saturating_add(1);Status::Invalid\n    }\n    fn query(&self,bytes:&[u8],mode:u8)->Status {\n        if mode>7{return self.invalid(8);}\n        if bytes.is_empty() || bytes.len()>MAX_PATH_BYTES {return self.invalid(7);}\n        let Ok(path)=std::str::from_utf8(bytes) else{return self.invalid(5)};\n        if !path.starts_with(\'/\'){return self.invalid(6);}\n        if path.starts_with("//"){return self.invalid(0);}\n        if path.contains(\':\'){return self.invalid(1);}\n        if path.contains(\'\\\\\'){return self.invalid(2);}\n        if path.contains(\'\\0\') || path.chars().any(char::is_control){return self.invalid(3);}\n        let mount=|root:&str| path.strip_prefix(root).filter(|rest|rest.is_empty()||rest.starts_with(\'/\'));\n        let selected=if let Some(p)=mount("/wordpress"){Some((&self.wp,p))}\n'),
    (b'            Err(FsError::PermissionDenied)=>Status::Denied,\n            Err(FsError::EntryNotFound|FsError::BaseNotDirectory|FsError::NotAFile)=>Status::Missing,\n            Err(FsError::InvalidInput)=>Status::Invalid,\n            Err(_)=>Status::Io,\n        }\n',
     b'            Err(FsError::PermissionDenied)=>Status::Denied,\n            Err(FsError::EntryNotFound|FsError::BaseNotDirectory|FsError::NotAFile)=>Status::Missing,\n            Err(FsError::InvalidInput)=>self.invalid(4),\n            Err(_)=>Status::Io,\n        }\n'),
    (b'    pub(crate) fn new(wp:SourceFs,php:SourceFs,context:SourceFs)->Result<Self> {\n        Ok(Self(Arc::new(Mutex::new(State {\n            policy: Policy {wp,php,context},\n            pending: Vec::new(),\n            response: None,\n',
     b'    pub(crate) fn new(wp:SourceFs,php:SourceFs,context:SourceFs)->Result<Self> {\n        Ok(Self(Arc::new(Mutex::new(State {\n            policy: Policy {wp,php,context,invalid_reasons:Arc::new(Mutex::new(InvalidReasons::default()))},\n            pending: Vec::new(),\n            response: None,\n'),
    (b'            .lock()\n            .map_err(|_| io::Error::other("access state poisoned"))\n    }\n\n',
     b'            .lock()\n            .map_err(|_| io::Error::other("access state poisoned"))\n    }\n\n    /// Counts only; paths and path hashes never enter a public observation.\n    pub(crate) fn invalid_reason_counts(&self)->Result<serde_json::Value> {\n        let state=self.lock()?;\n        let reasons=state.policy.invalid_reasons.lock().map_err(|_|io::Error::other("access reason state poisoned"))?;\n        Ok(serde_json::Value::Object(INVALID_REASON_NAMES.iter().zip(reasons.counts).map(|(name,n)|((*name).to_owned(),serde_json::json!(n))).collect()))\n    }\n\n'),
    (b'    fn frame(path:&str,mode:u8)->Vec<u8>{let mut b=b"RPA1".to_vec();b.push(mode);b.extend_from_slice(&(path.len() as u16).to_le_bytes());b.extend_from_slice(path.as_bytes());b}\n    #[test]\n    fn same_readonly_catalog_exists_readable_and_write_denial() {\n        let fs=fixture_fs();let state=fs.lock().unwrap();let p=&state.policy;\n',
     b'    fn frame(path:&str,mode:u8)->Vec<u8>{let mut b=b"RPA1".to_vec();b.push(mode);b.extend_from_slice(&(path.len() as u16).to_le_bytes());b.extend_from_slice(path.as_bytes());b}\n    #[test]\n    fn invalid_reason_counts_retain_rejections_without_paths() {\n        let fs=fixture_fs();\n        {let s=fs.lock().unwrap();let p=&s.policy;\n            for path in ["//wordpress/file.php","/wordpress/colon:file","/wordpress\\\\file.php","/wordpress/\\0file","/wordpress//file.php"] {\n                assert_eq!(p.query(path.as_bytes(),4),Status::Invalid);\n            }\n        }\n        let report=fs.invalid_reason_counts().unwrap();\n        for name in ["double_leading_slash","colon","backslash","control","metadata_invalid"] {assert_eq!(report[name],1);}\n        assert_eq!(report.as_object().unwrap().values().map(|v|v.as_u64().unwrap()).sum::<u64>(),5);\n        assert!(!report.to_string().contains("file.php"));\n    }\n    #[test]\n    fn same_readonly_catalog_exists_readable_and_write_denial() {\n        let fs=fixture_fs();let state=fs.lock().unwrap();let p=&state.policy;\n'),
]


# The sole compatibility change below adapts mount-relative leading separators.
# Global path rejection and the ordered finite-tree resolver remain authoritative.
ACCESS_MOUNT_EDITS = [
    (b'        // Pass the remaining lexical path unchanged to the SAME frozen tree.\n        // Missing prefixes/file traversal/root escape are never normalized away.\n        match fs.metadata(Path::new(if entry.is_empty(){"/"}else{entry})) {\n',
     b'        // Global leading // was rejected above. Slashes after a mount are\n        // interior separators, not a new global // prefix in the mounted tree.\n        // Preserve all remaining components: missing/file prefixes and root\n        // escape must still be checked by the SAME frozen tree in order.\n        let mounted=format!("/{}",entry.trim_start_matches(\'/\'));\n        match fs.metadata(Path::new(&mounted)) {\n'),
    (b'    #[test]\n    fn invalid_reason_counts_retain_rejections_without_paths() {\n        let fs=fixture_fs();\n        {let s=fs.lock().unwrap();let p=&s.policy;\n            for path in ["//wordpress/file.php","/wordpress/colon:file","/wordpress\\\\file.php","/wordpress/\\0file","/wordpress//file.php"] {\n                assert_eq!(p.query(path.as_bytes(),4),Status::Invalid);\n            }\n        }\n        let report=fs.invalid_reason_counts().unwrap();\n        for name in ["double_leading_slash","colon","backslash","control","metadata_invalid"] {assert_eq!(report[name],1);}\n        assert_eq!(report.as_object().unwrap().values().map(|v|v.as_u64().unwrap()).sum::<u64>(),5);\n        assert!(!report.to_string().contains("file.php"));\n    }\n',
     b'    #[test]\n    fn invalid_reason_counts_retain_rejections_without_paths() {\n        let fs=fixture_fs();\n        {let s=fs.lock().unwrap();let p=&s.policy;\n            for path in ["//wordpress/file.php","/wordpress/colon:file","/wordpress\\\\file.php","/wordpress/\\0file"] {\n                assert_eq!(p.query(path.as_bytes(),4),Status::Invalid);\n            }\n        }\n        let report=fs.invalid_reason_counts().unwrap();\n        for name in ["double_leading_slash","colon","backslash","control"] {assert_eq!(report[name],1);}\n        assert_eq!(report["metadata_invalid"],0);\n        assert_eq!(report.as_object().unwrap().values().map(|v|v.as_u64().unwrap()).sum::<u64>(),4);\n        assert!(!report.to_string().contains("file.php"));\n    }\n    #[test]\n    fn mount_interior_slashes_match_catalog_without_erasing_traversal() {\n        let fs=fixture_fs();let state=fs.lock().unwrap();let p=&state.policy;\n        for root in ["/wordpress","/experiments/php-wasix-wp-request-probe/fixtures","/rp-request"] {\n            for separator in ["/","//","////"] {\n                for (name,directory) in [("file.php",false),("dir",true),("dir//child.php",false)] {\n                    let path=format!("{root}{separator}{name}");\n                    for mode in [0,4] {assert_eq!(p.query(path.as_bytes(),mode),Status::Allow);}\n                    assert_eq!(p.query(path.as_bytes(),2),Status::Denied);\n                    assert_eq!(p.query(path.as_bytes(),1),if directory {Status::Allow}else{Status::Denied});\n                }\n                assert_eq!(p.query(format!("{root}{separator}").as_bytes(),5),Status::Allow);\n                for name in ["missing","missing/../file.php","file.php/../file.php","file.php/"] {\n                    assert_eq!(p.query(format!("{root}{separator}{name}").as_bytes(),4),Status::Missing);\n                }\n                for name in ["../file.php","dir/../../file.php"] {\n                    assert_eq!(p.query(format!("{root}{separator}{name}").as_bytes(),4),Status::Denied);\n                }\n                assert_eq!(p.query(format!("{root}{separator}dir/../file.php").as_bytes(),4),Status::Allow);\n            }\n        }\n        assert_eq!(p.query(b"//wordpress/file.php",4),Status::Invalid);\n        assert_eq!(p.query(b"/wordpress-other//file.php",4),Status::Unmanaged);\n        assert_eq!(p.query(b"/wordpress//colon:file",4),Status::Invalid);\n        assert_eq!(p.query(b"/wordpress//dir\\\\child.php",4),Status::Invalid);\n        assert_eq!(p.query(b"/wordpress//dir/\\0child.php",4),Status::Invalid);\n    }\n'),
]


def require(value,reason):
    if not value:raise ValueError(reason)


def sha(wire):return hashlib.sha256(wire).hexdigest()


def inputs():
    return [OLD/p for p in ['Cargo.toml','Cargo.lock','src/main.rs','src/db.rs','src/catalog.rs',
        'src/result_budget.rs','src/rpc_fs.rs','src/access_fs.rs','src/capture.rs','build_check.py','derivation_gate.py']]


def between(wire,start,end):
    return wire[wire.index(start):wire.index(end,wire.index(start))]


def verify():
    identities={}
    oldname=b'recastpress-php-wasix-wp-bootstrap-probe'
    newname=b'recastpress-php-wasix-wp-request-probe'
    for name in ['Cargo.toml','Cargo.lock']:
        wire=(OLD/name).read_bytes()
        require(wire.count(oldname)==1 and wire.replace(oldname,newname)==(HERE/name).read_bytes(),
            'home_workspace_name_only_derivation')
        identities[name]=sha(wire)
    for name in ['catalog.rs','result_budget.rs','rpc_fs.rs']:
        original=(OLD/'src'/name).read_bytes()
        require(original==(HERE/'src'/name).read_bytes(),'home_unchanged_runtime_body')
        identities['src/'+name]=sha(original)
    original=(OLD/'src/access_fs.rs').read_bytes()
    expected=original.replace(b'php-wasix-wp-bootstrap-probe',b'php-wasix-wp-request-probe').replace(b'/rp-bootstrap',b'/rp-request')
    unchanged=expected
    for before,after in ACCESS_DIAGNOSTIC_EDITS + ACCESS_MOUNT_EDITS:
        require(expected.count(before)==1,'home_access_diagnostic_context_changed')
        expected=expected.replace(before,after,1)
    actual=(HERE/'src/access_fs.rs').read_bytes()
    require(expected==actual,'home_access_exact_allowed_derivation')
    for start,end in [(b'#[derive(Debug)]\nstruct State',b'impl ManagedAccessFs {'),
        (b'    pub(crate) fn snapshot(',b'fn channel_entry('),(b'fn channel_entry(',b'#[cfg(test)]')]:
        require(between(unchanged,start,end)==between(actual,start,end),'home_access_transaction_body_changed')
    identities['src/access_fs.rs']=sha(original)
    original=(OLD/'src/capture.rs').read_bytes()
    require(original.count(b'const OUTPUT_LIMIT: usize = 65 * 1024;')==1,'home_original_capture_limit')
    expected=original.replace(b'const OUTPUT_LIMIT: usize = 65 * 1024;',
        b'const OUTPUT_LIMIT: usize = 3 * 1024 * 1024;')
    actual=(HERE/'src/capture.rs').read_bytes().split(b'#[cfg(test)]',1)[0]
    require(expected.rstrip()==actual.rstrip(),'home_capture_limit_only_derivation')
    identities['src/capture.rs']=sha(original)
    olddb=(OLD/'src/db.rs').read_bytes();newdb=(HERE/'src/db.rs').read_bytes()
    start,end=b'pub fn parse_request(',b'#[cfg(test)]'
    require(between(olddb,start,end)==between(newdb,start,end),'home_db_actor_query_escape_body_changed')
    start,end=b'pub type Result<T>',b'#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]'
    require(between(olddb,start,end)==between(newdb,start,end),'home_db_wire_budgets_changed')
    oldmain=(OLD/'src/main.rs').read_bytes();newmain=(HERE/'src/main.rs').read_bytes()
    require(between(oldmain,b'fn load_module(',b'fn finite(')==between(newmain,b'fn load_module(',b'fn finite('),
        'home_managed_module_gate_changed')
    return {'original_sources':identities,'original_sources_modified':False,
        'workspace_and_lock_package_name_only':True,'db_query_escape_actor_wire_budgets_unchanged':True,
        'async_rpc_catalog_and_result_budgets_unchanged':True,
        'access_mount_prefix_only':False,'access_exact_diagnostic_edits':len(ACCESS_DIAGNOSTIC_EDITS),
        'access_exact_mount_edits':len(ACCESS_MOUNT_EDITS),
        'access_transaction_bodies_unchanged':True,'access_policy_rejection_conditions_unchanged':False,
        'access_invalid_reason_counters_only':False,'access_mount_interior_separator_adapter':True,
        'access_global_leading_double_slash_rejection_unchanged':True,
        'access_ordered_missing_file_and_parent_checks_unchanged':True,'capture_limit_bytes':[66560,3145728],
        'managed_module_verification_body_unchanged':True,
        'new_input_context_home_routing_and_public_response_policy_explicit':True}
