use serde_json::{Value, json};

pub(crate) fn complete(guest: &Value, transport: &Value) -> bool {
    let groups = json!({"rp-cache-multiple":5,"rp-cache-integer":4});
    let finish = json!({"ok":true,"operations":9,"entries":0,"charged_bytes":0});
    guest
        == &json!({
            "shim_late_hook":true,"external_cache_before":false,"external_cache_after":false,
            "stock_restored":true,"stock_sentinel_preserved":true,"groups":groups,
            "finish":finish,"channel_closed":true
        })
        && transport["groups"] == groups
        && transport["operations"] == finish["operations"]
        && transport["entries"] == finish["entries"]
        && transport["charged_bytes"] == finish["charged_bytes"]
        && transport["flushes"] == 0
        && transport["opened_once"] == true
        && transport["channel_open"] == false
        && transport["finished"] == true
        && transport["finish_dispatched"] == true
        && transport["pending_bytes"] == 0
        && transport["unread_response_bytes"] == 0
        && transport["request_bytes"].as_u64().is_some_and(|n| n > 0)
        && transport["response_bytes"].as_u64().is_some_and(|n| n > 0)
        && transport["response_bytes"] == transport["response_read_bytes"]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trial_requires_exact_guest_ack_and_drained_host_transport() {
        let guest = json!({"shim_late_hook":true,"external_cache_before":false,"external_cache_after":false,
            "stock_restored":true,"stock_sentinel_preserved":true,
            "groups":{"rp-cache-multiple":5,"rp-cache-integer":4},
            "finish":{"ok":true,"operations":9,"entries":0,"charged_bytes":0},"channel_closed":true});
        let transport = json!({"groups":guest["groups"],"operations":9,"entries":0,"charged_bytes":0,
            "flushes":0,"opened_once":true,"channel_open":false,"finished":true,"finish_dispatched":true,
            "pending_bytes":0,"unread_response_bytes":0,"request_bytes":10,"response_bytes":10,"response_read_bytes":10});
        assert!(complete(&guest, &transport));
        for (key, invalid) in [("channel_closed", json!(false)), ("unknown", json!(true))] {
            let mut changed = guest.clone();
            changed[key] = invalid;
            assert!(!complete(&changed, &transport));
        }
        for (key, invalid) in [
            ("response_read_bytes", json!(9)),
            ("charged_bytes", json!(1)),
            ("operations", json!(10)),
        ] {
            let mut changed = transport.clone();
            changed[key] = invalid;
            assert!(!complete(&guest, &changed));
        }
    }
}
