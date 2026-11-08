use mysql_async::{Conn, prelude::Queryable};

const IDENTITY: &str = "`rp_options_guard_fixture`.`identity`";

#[derive(Clone)]
pub(crate) struct SourceIdentity {
    binding: Vec<u8>,
    monitor_id: u32,
    version: Vec<u8>,
}

impl SourceIdentity {
    pub(crate) async fn from_monitor(connection: &mut Conn, nonce: &str) -> anyhow::Result<Self> {
        let (binding, observed_nonce): (Vec<u8>, Vec<u8>) = connection
            .query_first(format!("SELECT binding,nonce FROM {IDENTITY} WHERE id=1"))
            .await
            .map_err(|_| anyhow::anyhow!("autoload_binding_monitor_query"))?
            .ok_or_else(|| anyhow::anyhow!("autoload_binding_missing"))?;
        anyhow::ensure!(
            observed_nonce == nonce.as_bytes()
                && binding.len() == 48
                && binding
                    .iter()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
                && binding != observed_nonce,
            "autoload_binding_invalid"
        );
        Ok(Self {
            binding,
            monitor_id: connection.id(),
            version: connection.raw_server_version().to_vec(),
        })
    }

    pub(crate) async fn verify_actor(&self, connection: &mut Conn) -> Result<(), &'static str> {
        if connection.id() == 0
            || connection.id() == self.monitor_id
            || connection.raw_server_version() != self.version
        {
            return Err("autoload_binding_actor_identity");
        }
        let observation: Option<Vec<u8>> = connection
            .query_first(format!("SELECT binding FROM {IDENTITY} WHERE id=1"))
            .await
            .map_err(|_| "autoload_binding_actor_query")?;
        if observation.as_deref() != Some(self.binding.as_slice()) {
            return Err("autoload_binding_actor_mismatch");
        }
        Ok(())
    }
}
