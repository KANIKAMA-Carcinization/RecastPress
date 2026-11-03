//! Synthetic result collection only; no WP, RPC, Wasm or product budget acceptance.
#![forbid(unsafe_code)]
use serde::{Deserialize, Serialize};
use std::io::{self, Write};

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
pub struct Limits {
    pub rows: usize,
    pub columns: usize,
    pub cell_bytes: usize,
    pub total_raw_cell_bytes: usize,
    pub metadata_bytes: usize,
    pub identifier_bytes: usize,
    pub encoded_reply_bytes: usize,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            rows: 256,
            columns: 16,
            cell_bytes: 65536,
            total_raw_cell_bytes: 262144,
            metadata_bytes: 4096,
            identifier_bytes: 256,
            encoded_reply_bytes: 524288,
        }
    }
}
#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BudgetError {
    InvalidLimits,
    ColumnLimit,
    MetadataLimit,
    RowLimit,
    RowWidth,
    CellLimit,
    RawBytesLimit,
    EncodedBytesLimit,
    Allocation,
    CollectorFailed,
}
#[derive(Debug, Clone)]
pub struct Column {
    pub name: Vec<u8>,
    pub column_type: u8,
    pub flags: u16,
    pub character_set: u16,
    pub declared_length: u32,
    pub decimals: u8,
    /// Raw lengths of schema/table/org_table/name/org_name, without copies.
    pub metadata_bytes: usize,
}
#[derive(Debug)]
pub struct Collector {
    limits: Limits,
    columns: Vec<Column>,
    rows: Vec<Vec<Option<Vec<u8>>>>,
    raw_bytes: usize,
    metadata_bytes: usize,
    failed: bool,
}
fn valid(l: Limits) -> bool {
    let m = Limits::default();
    l.rows <= m.rows
        && l.columns <= m.columns
        && l.cell_bytes <= m.cell_bytes
        && l.total_raw_cell_bytes <= m.total_raw_cell_bytes
        && l.metadata_bytes <= m.metadata_bytes
        && l.identifier_bytes <= m.identifier_bytes
        && l.encoded_reply_bytes <= m.encoded_reply_bytes
}
impl Collector {
    pub fn new(limits: Limits, columns: Vec<Column>) -> Result<Self, BudgetError> {
        if !valid(limits) {
            return Err(BudgetError::InvalidLimits);
        }
        if columns.len() > limits.columns {
            return Err(BudgetError::ColumnLimit);
        }
        let mut metadata_bytes = 0usize;
        for c in &columns {
            if c.name.len() > limits.identifier_bytes || c.metadata_bytes < c.name.len() {
                return Err(BudgetError::MetadataLimit);
            }
            metadata_bytes = metadata_bytes
                .checked_add(c.metadata_bytes)
                .ok_or(BudgetError::MetadataLimit)?;
            if metadata_bytes > limits.metadata_bytes {
                return Err(BudgetError::MetadataLimit);
            }
        }
        Ok(Self {
            limits,
            columns,
            rows: Vec::new(),
            raw_bytes: 0,
            metadata_bytes,
            failed: false,
        })
    }
    fn fail<T>(&mut self, error: BudgetError) -> Result<T, BudgetError> {
        self.failed = true;
        self.rows.clear();
        Err(error)
    }
    /// Validate the complete borrowed row before cloning a single cell.
    /// Driver packet/row allocation occurs outside this collector's budget.
    pub fn push(&mut self, cells: &[Option<&[u8]>]) -> Result<(), BudgetError> {
        if self.failed {
            return Err(BudgetError::CollectorFailed);
        }
        if self.rows.len() >= self.limits.rows {
            return self.fail(BudgetError::RowLimit);
        }
        if cells.len() != self.columns.len() {
            return self.fail(BudgetError::RowWidth);
        }
        let mut total = self.raw_bytes;
        for data in cells.iter().flatten() {
            if data.len() > self.limits.cell_bytes {
                return self.fail(BudgetError::CellLimit);
            }
            total = match total.checked_add(data.len()) {
                Some(n) if n <= self.limits.total_raw_cell_bytes => n,
                _ => return self.fail(BudgetError::RawBytesLimit),
            };
        }
        let mut row = Vec::new();
        if row.try_reserve_exact(cells.len()).is_err() {
            return self.fail(BudgetError::Allocation);
        }
        for cell in cells {
            row.push(match cell {
                None => None,
                Some(bytes) => {
                    let mut data = Vec::new();
                    if data.try_reserve_exact(bytes.len()).is_err() {
                        return self.fail(BudgetError::Allocation);
                    }
                    data.extend_from_slice(bytes);
                    Some(data)
                }
            });
        }
        if self.rows.try_reserve_exact(1).is_err() {
            return self.fail(BudgetError::Allocation);
        }
        self.rows.push(row);
        self.raw_bytes = total;
        Ok(())
    }
    /// Consumes the collector. Failure exposes no partial row/result accessor.
    pub fn finish(self) -> Result<Vec<u8>, BudgetError> {
        if self.failed {
            return Err(BudgetError::CollectorFailed);
        }
        let mut w = BoundedWriter::new(self.limits.encoded_reply_bytes)?;
        let encoded = (|| -> io::Result<()> {
            w.write_all(b"{\"columns\":[")?;
            for (i, c) in self.columns.iter().enumerate() {
                if i > 0 {
                    w.write_all(b",")?;
                }
                w.write_all(b"{\"name_hex\":")?;
                w.hex(&c.name)?;
                write!(
                    w,
                    ",\"type\":{},\"flags\":{},\"character_set\":{},\"declared_length\":{},\"decimals\":{}}}",
                    c.column_type, c.flags, c.character_set, c.declared_length, c.decimals
                )?;
            }
            w.write_all(b"],\"rows\":[")?;
            for (i, row) in self.rows.iter().enumerate() {
                if i > 0 {
                    w.write_all(b",")?;
                }
                w.write_all(b"[")?;
                for (j, cell) in row.iter().enumerate() {
                    if j > 0 {
                        w.write_all(b",")?;
                    }
                    match cell {
                        None => w.write_all(b"null")?,
                        Some(b) => w.hex(b)?,
                    }
                }
                w.write_all(b"]")?;
            }
            write!(
                w,
                "],\"row_count\":{},\"raw_cell_bytes\":{},\"metadata_bytes\":{}}}",
                self.rows.len(),
                self.raw_bytes,
                self.metadata_bytes
            )?;
            Ok(())
        })();
        if encoded.is_err() {
            return Err(BudgetError::EncodedBytesLimit);
        }
        Ok(w.into_bytes())
    }
}

/// Fixed-size backing allocation; writes never extend it beyond the limit.
/// Hex is emitted directly with a stack byte pair, without a per-cell String.
pub struct BoundedWriter {
    bytes: Vec<u8>,
    position: usize,
}
impl BoundedWriter {
    pub fn new(limit: usize) -> Result<Self, BudgetError> {
        if limit > 4 * 1024 * 1024 {
            return Err(BudgetError::InvalidLimits);
        }
        let mut bytes = Vec::new();
        bytes
            .try_reserve_exact(limit)
            .map_err(|_| BudgetError::Allocation)?;
        bytes.resize(limit, 0);
        Ok(Self { bytes, position: 0 })
    }
    fn hex(&mut self, bytes: &[u8]) -> io::Result<()> {
        const D: &[u8; 16] = b"0123456789abcdef";
        self.write_all(b"\"")?;
        for b in bytes {
            self.write_all(&[D[(b >> 4) as usize], D[(b & 15) as usize]])?;
        }
        self.write_all(b"\"")
    }
    pub fn into_bytes(mut self) -> Vec<u8> {
        self.bytes.truncate(self.position);
        self.bytes
    }
}
impl Write for BoundedWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let end = self
            .position
            .checked_add(bytes.len())
            .filter(|n| *n <= self.bytes.len())
            .ok_or_else(|| io::Error::other("encoded_bytes_limit"))?;
        self.bytes[self.position..end].copy_from_slice(bytes);
        self.position = end;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub schema: String,
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
    pub tls: String,
    pub tls_ca_pem: serde_json::Value,
}
pub fn parse_input(wire: &[u8]) -> Result<Input, &'static str> {
    if wire.len() > 4096 {
        return Err("input_size");
    }
    let i: Input = serde_json::from_slice(wire).map_err(|_| "input_shape")?;
    // Value-typed null is required too; serde otherwise treats a missing Value as null.
    let object: serde_json::Value = serde_json::from_slice(wire).map_err(|_| "input_shape")?;
    if object.as_object().map(|o| o.len()) != Some(8) {
        return Err("input_exact_keys");
    }
    if i.schema != "recastpress.wpdb-result-budget-input.v0.5"
        || i.host != "127.0.0.1"
        || i.port == 0
        || i.database != "rp_wpdb_probe"
        || i.user != "rp_wpdb_probe"
        || i.tls != "disabled"
        || !i.tls_ca_pem.is_null()
    {
        return Err("input_closed_profile");
    }
    if i.password.len() != 48
        || !i
            .password
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("input_secret_shape");
    }
    Ok(i)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn column() -> Column {
        Column {
            name: b"raw".to_vec(),
            column_type: 252,
            flags: 0,
            character_set: 63,
            declared_length: u32::MAX,
            decimals: 0,
            metadata_bytes: 3,
        }
    }
    fn collect(l: Limits, cells: &[Option<&[u8]>]) -> Result<Vec<u8>, BudgetError> {
        let mut c = Collector::new(l, vec![column()])?;
        for x in cells {
            c.push(&[*x])?;
        }
        c.finish()
    }
    #[test]
    fn metadata_and_zero_rows_are_preserved() {
        let wire = Collector::new(Limits::default(), vec![column()])
            .unwrap()
            .finish()
            .unwrap();
        let v: serde_json::Value = serde_json::from_slice(&wire).unwrap();
        assert_eq!(v["rows"], serde_json::json!([]));
        assert_eq!(v["columns"][0]["name_hex"], "726177");
    }
    #[test]
    fn binary_null_empty_and_raw_bytes_are_exact() {
        let wire = collect(Limits::default(), &[Some(b"\0\xff"), None, Some(b"")]).unwrap();
        let v: serde_json::Value = serde_json::from_slice(&wire).unwrap();
        assert_eq!(v["rows"], serde_json::json!([["00ff"], [null], [""]]));
        assert_eq!(v["raw_cell_bytes"], 2);
    }
    #[test]
    fn row_and_width_limits_return_no_materialized_result() {
        let mut c = Collector::new(
            Limits {
                rows: 1,
                ..Limits::default()
            },
            vec![column()],
        )
        .unwrap();
        c.push(&[None]).unwrap();
        assert_eq!(c.push(&[None]), Err(BudgetError::RowLimit));
        assert_eq!(c.finish(), Err(BudgetError::CollectorFailed));
        let mut c = Collector::new(Limits::default(), vec![column()]).unwrap();
        assert_eq!(c.push(&[]), Err(BudgetError::RowWidth));
        assert!(c.finish().is_err());
    }
    #[test]
    fn cell_and_raw_boundaries_are_exact() {
        let l = Limits {
            cell_bytes: 2,
            total_raw_cell_bytes: 2,
            ..Limits::default()
        };
        assert!(collect(l, &[Some(b"ab")]).is_ok());
        assert_eq!(collect(l, &[Some(b"abc")]), Err(BudgetError::CellLimit));
        assert_eq!(
            collect(l, &[Some(b"ab"), Some(b"c")]),
            Err(BudgetError::RawBytesLimit)
        );
    }
    #[test]
    fn encoded_boundary_and_metadata_limits_fail_closed() {
        let n = collect(Limits::default(), &[Some(b"abc")]).unwrap().len();
        assert!(
            collect(
                Limits {
                    encoded_reply_bytes: n,
                    ..Limits::default()
                },
                &[Some(b"abc")]
            )
            .is_ok()
        );
        assert_eq!(
            collect(
                Limits {
                    encoded_reply_bytes: n - 1,
                    ..Limits::default()
                },
                &[Some(b"abc")]
            ),
            Err(BudgetError::EncodedBytesLimit)
        );
        assert!(matches!(
            Collector::new(
                Limits {
                    metadata_bytes: 2,
                    ..Limits::default()
                },
                vec![column()]
            ),
            Err(BudgetError::MetadataLimit)
        ));
    }
    #[test]
    fn limits_cannot_enlarge_candidate_budget() {
        assert!(matches!(
            Collector::new(
                Limits {
                    rows: 257,
                    ..Limits::default()
                },
                vec![]
            ),
            Err(BudgetError::InvalidLimits)
        ));
        assert!(matches!(
            Collector::new(
                Limits {
                    columns: 0,
                    ..Limits::default()
                },
                vec![column()]
            ),
            Err(BudgetError::ColumnLimit)
        ));
    }
    #[test]
    fn input_unknown_duplicate_missing_and_destination_rejected() {
        let mut v = serde_json::json!({"schema":"recastpress.wpdb-result-budget-input.v0.5","host":"127.0.0.1","port":1,"database":"rp_wpdb_probe","user":"rp_wpdb_probe","password":"a".repeat(48),"tls":"disabled","tls_ca_pem":null});
        let wire = serde_json::to_vec(&v).unwrap();
        assert!(parse_input(&wire).is_ok());
        let mut duplicate = wire[..wire.len() - 1].to_vec();
        duplicate.extend_from_slice(b",\"host\":\"127.0.0.1\"}");
        assert!(parse_input(&duplicate).is_err());
        v.as_object_mut().unwrap().remove("tls_ca_pem");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v["tls_ca_pem"] = serde_json::Value::Null;
        v["host"] = serde_json::json!("localhost");
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
        v["host"] = serde_json::json!("127.0.0.1");
        v["extra"] = serde_json::Value::Null;
        assert!(parse_input(&serde_json::to_vec(&v).unwrap()).is_err());
    }
}
