from pathlib import Path
import re, sys

root = Path(sys.argv[1]).resolve()

def replace_once(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

browser = root/"crates/adp-browser/src/lib.rs"
replace_once(
    browser,
    '["delete","remove","purchase","buy now","pay","checkout","place order","transfer","confirm delete","send payment"]',
    '["publish","delete","remove","purchase","buy now","pay","checkout","place order","transfer","confirm delete","send payment"]',
    "publish risk classification",
)

core = root/"crates/adp-core/src/lib.rs"
replace_once(core, "use std::io::{Read, Write};\n", "", "unused std::io imports")
replace_once(
    core,
    "pub struct MissionStore { directory: PathBuf, runs_path: PathBuf, project: PathBuf }",
    "pub struct MissionStore { directory: PathBuf, runs_path: PathBuf }",
    "unused MissionStore project field",
)
replace_once(
    core,
    'Ok(Self{directory,runs_path:paths.missions.join("runs.jsonl"),project:paths.project.clone()})',
    'Ok(Self{directory,runs_path:paths.missions.join("runs.jsonl")})',
    "MissionStore constructor project field",
)
replace_once(
    core,
    "#[cfg(test)]\nmod tests {\n    use super::*;\n",
    "#[cfg(test)]\nmod tests {\n    use super::*;\n    use std::io::Write;\n",
    "test-only zip Write import",
)

models = root/"crates/adp-models/src/lib.rs"
text = models.read_text(encoding="utf-8")
pat = re.compile(
    r"\nasync fn run_external\(program:&str,args:&\[String\],prompt:&str,project:&Path\)->Result<\(String,String,i32\)>\{.*?\n\}\n",
    re.S,
)
text2, n = pat.subn("\n", text, count=1)
if n != 1:
    raise SystemExit(f"obsolete run_external: expected one match, found {n}")
models.write_text(text2, encoding="utf-8")

main = root/"crates/adp-cli/src/main.rs"
replace_once(
    main,
    "for (label,ok,text) in rows.iter().filter(|(_,ok,_)|!*ok).take(4) {",
    "for (label,_ok,text) in rows.iter().filter(|(_,ok,_)|!*ok).take(4) {",
    "unused compact failure ok variable",
)
replace_once(main, "    let mut last_response=None;\n", "", "unused last_response initialization")
replace_once(
    main,
    '        match run_operator_turns(paths,browser,&req,&candidate,repair_model).await {\n'
    '            Ok(resp)=>{\n'
    '                let elapsed=started.elapsed().as_millis().min(u64::MAX as u128) as u64;\n'
    '                last_response=Some((resp,elapsed));\n'
    '            }\n'
    '            Err(e)=>{\n'
    '                let elapsed=started.elapsed().as_millis().min(u64::MAX as u128) as u64;\n'
    '                record_provider_outcome(paths,route,&candidate,repair_model,None,elapsed,false)?;\n'
    '                evidence=format!("provider {candidate} repair failed: {e}");continue;\n'
    '            }\n'
    '        }\n',
    '        let (resp,elapsed)=match run_operator_turns(paths,browser,&req,&candidate,repair_model).await {\n'
    '            Ok(resp)=>{\n'
    '                let elapsed=started.elapsed().as_millis().min(u64::MAX as u128) as u64;\n'
    '                (resp,elapsed)\n'
    '            }\n'
    '            Err(e)=>{\n'
    '                let elapsed=started.elapsed().as_millis().min(u64::MAX as u128) as u64;\n'
    '                record_provider_outcome(paths,route,&candidate,repair_model,None,elapsed,false)?;\n'
    '                evidence=format!("provider {candidate} repair failed: {e}");continue;\n'
    '            }\n'
    '        };\n',
    "repair response flow",
)
replace_once(
    main,
    '            let (resp,elapsed)=last_response.take().expect("repair response");\n',
    "",
    "redundant repair response unpack",
)
replace_once(
    main,
    '        if let Some((resp,elapsed))=last_response.take(){record_provider_outcome(paths,route,&candidate,repair_model,Some(&resp),elapsed,false)?;}\n',
    '        record_provider_outcome(paths,route,&candidate,repair_model,Some(&resp),elapsed,false)?;\n',
    "repair failure outcome",
)

health = root/"crates/adp-cli/src/provider_health.rs"
replace_once(health, "const QUOTA_CACHE_SECS:i64=45;\n", "", "unused quota cache constant")
replace_once(
    health,
    'pub fn quota_is_fresh(q:&ProviderQuotaSnapshot)->bool{\n'
    '    DateTime::parse_from_rfc3339(&q.fetched_at).map(|x|Utc::now().signed_duration_since(x.with_timezone(&Utc)).num_seconds().abs()<=QUOTA_CACHE_SECS).unwrap_or(false)\n'
    '}\n\n',
    "",
    "unused quota freshness helper",
)

print("Rust migration safety + warning cleanups applied.")
