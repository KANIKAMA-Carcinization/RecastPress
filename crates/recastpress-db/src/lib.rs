//! Editing-side raw schema reads. This crate is not linked into the public server.
//! It does not execute PHP, create/upgrade a schema, authorize plugins, or publish.

pub mod budgeted_change_buffer;
pub mod budgeted_meta_view;
pub mod change_buffer;
pub mod comments;
pub mod credential_writer;
pub mod links;
pub mod meta_view;
pub mod post_name_lookup;
pub mod post_writer;
pub mod taxonomy;
pub mod user_lookup;
pub mod users;

pub use comments::{CommentMetaRow, CommentRow};
pub use credential_writer::{
    EditingCredentialWriter, UserCredentialFields, UserCredentialWriteError,
    UserCredentialWriteOutcome,
};
pub use links::LinkRow;
pub use post_name_lookup::PostNameLookupError;
pub use post_writer::{EditingWriter, PostTextFields, PostTextWriteError, PostTextWriteOutcome};
pub use taxonomy::{
    TermMetaRow, TermRelationshipKey, TermRelationshipRow, TermRow, TermTaxonomyRow,
};
pub use user_lookup::UserLookupError;
pub use users::{UserMetaRow, UserRow};

use sqlx::{
    MySql, MySqlPool, QueryBuilder, Row, Transaction,
    mysql::{MySqlConnectOptions, MySqlPoolOptions, MySqlRow},
};
use std::{fmt, num::NonZeroU16};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TablePrefix(String);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InvalidPrefix;

impl fmt::Display for InvalidPrefix {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("WordPress table prefixes permit only ASCII letters, digits, and underscores")
    }
}
impl std::error::Error for InvalidPrefix {}

#[derive(Clone, Copy)]
enum Table {
    Posts,
    PostMeta,
    Options,
    Terms,
    TermTaxonomy,
    TermRelationships,
    TermMeta,
    Comments,
    CommentMeta,
    Users,
    UserMeta,
    Links,
}

impl TablePrefix {
    /// Match wpdb::set_prefix's ASCII alphanumeric/underscore domain, including
    /// the empty prefix. The database enforces its own identifier length limit.
    pub fn new(prefix: &str) -> Result<Self, InvalidPrefix> {
        if prefix
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_')
        {
            Ok(Self(prefix.into()))
        } else {
            Err(InvalidPrefix)
        }
    }

    fn raw_table(&self, table: Table) -> String {
        let suffix = match table {
            Table::Posts => "posts",
            Table::PostMeta => "postmeta",
            Table::Options => "options",
            Table::Terms => "terms",
            Table::TermTaxonomy => "term_taxonomy",
            Table::TermRelationships => "term_relationships",
            Table::TermMeta => "termmeta",
            Table::Comments => "comments",
            Table::CommentMeta => "commentmeta",
            Table::Users => "users",
            Table::UserMeta => "usermeta",
            Table::Links => "links",
        };
        format!("{}{suffix}", self.0)
    }
    fn table(&self, table: Table) -> String {
        format!("`{}`", self.raw_table(table))
    }
}

#[derive(Debug, Eq, PartialEq)]
pub enum SnapshotSchemaError {
    MissingTable {
        table: String,
    },
    NonTransactionalTable {
        table: String,
        engine: Option<String>,
    },
}

impl fmt::Display for SnapshotSchemaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingTable { table } => write!(f, "snapshot table is missing: {table}"),
            Self::NonTransactionalTable { table, engine } => write!(
                f,
                "consistent snapshot requires an InnoDB table: {table} has {engine:?}"
            ),
        }
    }
}
impl std::error::Error for SnapshotSchemaError {}

// The outer option represents table visibility; the inner one is the nullable ENGINE column.
async fn table_engine(
    transaction: &mut Transaction<'_, MySql>,
    name: &str,
) -> Result<Option<Option<String>>, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT ENGINE FROM information_schema.TABLES \
         WHERE (@@lower_case_table_names=0 \
         AND CAST(TABLE_SCHEMA AS BINARY)=CAST(DATABASE() AS BINARY) \
         AND CAST(TABLE_NAME AS BINARY)=CAST(? AS BINARY)) \
         OR (@@lower_case_table_names<>0 AND TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?)",
    )
    .bind(name)
    .bind(name)
    .fetch_optional(&mut **transaction)
    .await
}

/// Raw editing fields. Status/type/password and zero dates remain distinct;
/// this is not a WP_Post API or an anonymous-public data projection.
#[derive(Clone, Eq, PartialEq)]
pub struct PostRow {
    pub id: u64,
    pub post_author: u64,
    pub post_date: String,
    pub post_date_gmt: String,
    pub post_content: String,
    pub post_title: String,
    pub post_excerpt: String,
    pub post_status: String,
    pub comment_status: String,
    pub ping_status: String,
    pub post_password: String,
    pub post_name: String,
    pub to_ping: String,
    pub pinged: String,
    pub post_modified: String,
    pub post_modified_gmt: String,
    pub post_content_filtered: String,
    pub post_parent: u64,
    pub guid: String,
    pub menu_order: i32,
    pub post_type: String,
    pub post_mime_type: String,
    pub comment_count: i64,
}

impl fmt::Debug for PostRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PostRow")
            .field("id", &self.id)
            .field("status", &self.post_status)
            .field("type", &self.post_type)
            .finish_non_exhaustive()
    }
}

impl PostRow {
    fn decode(row: MySqlRow) -> Result<Self, sqlx::Error> {
        Ok(Self {
            id: row.try_get("ID")?,
            post_author: row.try_get("post_author")?,
            post_date: row.try_get("post_date")?,
            post_date_gmt: row.try_get("post_date_gmt")?,
            post_content: row.try_get("post_content")?,
            post_title: row.try_get("post_title")?,
            post_excerpt: row.try_get("post_excerpt")?,
            post_status: row.try_get("post_status")?,
            comment_status: row.try_get("comment_status")?,
            ping_status: row.try_get("ping_status")?,
            post_password: row.try_get("post_password")?,
            post_name: row.try_get("post_name")?,
            to_ping: row.try_get("to_ping")?,
            pinged: row.try_get("pinged")?,
            post_modified: row.try_get("post_modified")?,
            post_modified_gmt: row.try_get("post_modified_gmt")?,
            post_content_filtered: row.try_get("post_content_filtered")?,
            post_parent: row.try_get("post_parent")?,
            guid: row.try_get("guid")?,
            menu_order: row.try_get("menu_order")?,
            post_type: row.try_get("post_type")?,
            post_mime_type: row.try_get("post_mime_type")?,
            comment_count: row.try_get("comment_count")?,
        })
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct PostMetaRow {
    pub meta_id: u64,
    pub post_id: u64,
    pub meta_key: Option<Vec<u8>>,
    pub meta_value: Option<Vec<u8>>,
}

type PostMetaLengths = (u64, Option<u64>, Option<u64>);

impl fmt::Debug for PostMetaRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PostMetaRow")
            .field("meta_id", &self.meta_id)
            .field("post_id", &self.post_id)
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct OptionRow {
    pub option_id: u64,
    pub option_name: Vec<u8>,
    pub option_value: Vec<u8>,
    pub autoload: String,
}

impl fmt::Debug for OptionRow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OptionRow")
            .field("option_id", &self.option_id)
            .field("autoload", &self.autoload)
            .finish_non_exhaustive()
    }
}

const POST_FIELDS: &str = "ID,post_author,CAST(post_date AS CHAR) AS post_date,CAST(post_date_gmt AS CHAR) AS post_date_gmt,post_content,post_title,post_excerpt,post_status,comment_status,ping_status,post_password,post_name,to_ping,pinged,CAST(post_modified AS CHAR) AS post_modified,CAST(post_modified_gmt AS CHAR) AS post_modified_gmt,post_content_filtered,post_parent,guid,menu_order,post_type,post_mime_type,comment_count";
const INCOMPATIBLE_SQL_MODES: [&str; 6] = [
    "NO_ZERO_DATE",
    "ONLY_FULL_GROUP_BY",
    "STRICT_TRANS_TABLES",
    "STRICT_ALL_TABLES",
    "TRADITIONAL",
    "ANSI",
];

/// Private pool: public serving receives a prepared projection instead of this
/// repository or its credentials. Transport/TLS options are supplied by the host.
pub struct EditingRepository {
    pool: MySqlPool,
    prefix: TablePrefix,
}

/// Observed server defaults only. These values are not a CDC readiness proof:
/// sessions can override format, and privileges, filters, retention, source
/// identity, snapshot/log alignment and actual event decoding remain unchecked.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinlogConfiguration {
    pub enabled: bool,
    pub format: String,
    pub row_image: String,
}

impl EditingRepository {
    /// Read-only R60 deployment inspection. Never enables logging, changes
    /// server settings, installs triggers or marks monitoring as healthy.
    pub async fn binlog_configuration(&self) -> Result<BinlogConfiguration, sqlx::Error> {
        let (enabled, format, row_image): (u64, String, String) = sqlx::query_as(
            "SELECT CAST(@@GLOBAL.log_bin AS UNSIGNED), @@GLOBAL.binlog_format, @@GLOBAL.binlog_row_image"
        ).fetch_one(&self.pool).await?;
        Ok(BinlogConfiguration {
            enabled: enabled != 0,
            format,
            row_image,
        })
    }

    pub async fn connect(
        options: MySqlConnectOptions,
        prefix: TablePrefix,
    ) -> Result<Self, sqlx::Error> {
        let pool = MySqlPoolOptions::new()
            .max_connections(4)
            .after_connect(|connection, _| {
                Box::pin(async move {
                    let (mode,): (String,) = sqlx::query_as("SELECT @@SESSION.sql_mode")
                        .fetch_one(&mut *connection)
                        .await?;
                    let filtered = mode
                        .split(',')
                        .filter(|mode| !INCOMPATIBLE_SQL_MODES.contains(mode))
                        .collect::<Vec<_>>()
                        .join(",");
                    sqlx::query("SET SESSION sql_mode = ?")
                        .bind(filtered)
                        .execute(&mut *connection)
                        .await?;
                    sqlx::query("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                        .execute(&mut *connection)
                        .await?;
                    Ok(())
                })
            })
            .connect_with(options)
            .await?;
        Ok(Self { pool, prefix })
    }

    pub async fn snapshot(&self) -> Result<EditingSnapshot, sqlx::Error> {
        let mut transaction = self
            .pool
            .begin_with("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            .await?;
        for table in [
            Table::Posts,
            Table::PostMeta,
            Table::Options,
            Table::Terms,
            Table::TermTaxonomy,
            Table::TermRelationships,
            Table::TermMeta,
            Table::Comments,
            Table::CommentMeta,
            Table::Users,
            Table::UserMeta,
            Table::Links,
        ] {
            let name = self.prefix.raw_table(table);
            let error = match table_engine(&mut transaction, &name).await? {
                None => Some(SnapshotSchemaError::MissingTable { table: name }),
                Some(engine)
                    if !engine
                        .as_ref()
                        .is_some_and(|value| value.eq_ignore_ascii_case("InnoDB")) =>
                {
                    Some(SnapshotSchemaError::NonTransactionalTable {
                        table: name,
                        engine,
                    })
                }
                _ => None,
            };
            if let Some(error) = error {
                return Err(sqlx::Error::Configuration(Box::new(error)));
            }
        }
        Ok(EditingSnapshot {
            transaction,
            prefix: self.prefix.clone(),
        })
    }

    pub async fn close(self) {
        self.pool.close().await;
    }
}

/// Read-only repeatable reads of InnoDB tables under a stable schema. Concurrent
/// DML is supported; external DDL/engine replacement during a scan is not covered.
pub struct EditingSnapshot {
    transaction: Transaction<'static, MySql>,
    prefix: TablePrefix,
}

impl EditingSnapshot {
    pub async fn post(&mut self, id: u64) -> Result<Option<PostRow>, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new("SELECT ");
        query
            .push(POST_FIELDS)
            .push(" FROM ")
            .push(self.prefix.table(Table::Posts))
            .push(" WHERE ID = ")
            .push_bind(id);
        query
            .build()
            .fetch_optional(&mut *self.transaction)
            .await?
            .map(PostRow::decode)
            .transpose()
    }

    /// Cursor pages keep the unsigned ID domain and never load the whole site.
    pub async fn posts_after(
        &mut self,
        last_id: u64,
        limit: NonZeroU16,
    ) -> Result<Vec<PostRow>, sqlx::Error> {
        self.posts_page(Some(last_id), limit).await
    }

    /// None starts before the first row, including a stored ID of zero. Some(0)
    /// means strictly after zero; do not use it as a beginning-of-snapshot sentinel.
    pub async fn posts_page(
        &mut self,
        after: Option<u64>,
        limit: NonZeroU16,
    ) -> Result<Vec<PostRow>, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new("SELECT ");
        query
            .push(POST_FIELDS)
            .push(" FROM ")
            .push(self.prefix.table(Table::Posts));
        if let Some(id) = after {
            query.push(" WHERE ID > ").push_bind(id);
        }
        query.push(" ORDER BY ID ASC LIMIT ").push_bind(limit.get());
        query
            .build()
            .fetch_all(&mut *self.transaction)
            .await?
            .into_iter()
            .map(PostRow::decode)
            .collect()
    }

    pub async fn post_meta(&mut self, post_id: u64) -> Result<Vec<PostMetaRow>, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new(
            "SELECT meta_id,post_id,CAST(meta_key AS BINARY) AS meta_key,CAST(meta_value AS BINARY) AS meta_value FROM ",
        );
        query
            .push(self.prefix.table(Table::PostMeta))
            .push(" WHERE post_id = ")
            .push_bind(post_id)
            .push(" ORDER BY meta_id ASC");
        query
            .build()
            .fetch_all(&mut *self.transaction)
            .await?
            .into_iter()
            .map(|row| {
                Ok(PostMetaRow {
                    meta_id: row.try_get("meta_id")?,
                    post_id: row.try_get("post_id")?,
                    meta_key: row.try_get("meta_key")?,
                    meta_value: row.try_get("meta_value")?,
                })
            })
            .collect()
    }

    /// Includes orphan metadata and preserves its original IDs for raw export.
    pub async fn post_meta_after(
        &mut self,
        last_id: u64,
        limit: NonZeroU16,
    ) -> Result<Vec<PostMetaRow>, sqlx::Error> {
        self.post_meta_page(Some(last_id), limit).await
    }

    /// None includes the complete unsigned ID domain, including zero and orphans.
    pub async fn post_meta_page(
        &mut self,
        after: Option<u64>,
        limit: NonZeroU16,
    ) -> Result<Vec<PostMetaRow>, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new(
            "SELECT meta_id,post_id,CAST(meta_key AS BINARY) AS meta_key,CAST(meta_value AS BINARY) AS meta_value FROM ",
        );
        query.push(self.prefix.table(Table::PostMeta));
        if let Some(id) = after {
            query.push(" WHERE meta_id > ").push_bind(id);
        }
        query
            .push(" ORDER BY meta_id ASC LIMIT ")
            .push_bind(limit.get());
        query
            .build()
            .fetch_all(&mut *self.transaction)
            .await?
            .into_iter()
            .map(|row| {
                Ok(PostMetaRow {
                    meta_id: row.try_get("meta_id")?,
                    post_id: row.try_get("post_id")?,
                    meta_key: row.try_get("meta_key")?,
                    meta_value: row.try_get("meta_value")?,
                })
            })
            .collect()
    }

    // Lengths only: no key/value body crosses the driver boundary. Explicit
    // UNSIGNED casts make SQLx's u64 result type independent of expression flags.
    async fn post_meta_lengths_page(
        &mut self,
        after: Option<u64>,
        limit: NonZeroU16,
    ) -> Result<Vec<PostMetaLengths>, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new(
            "SELECT meta_id,CAST(OCTET_LENGTH(CAST(meta_key AS BINARY)) AS UNSIGNED) AS key_bytes,CAST(OCTET_LENGTH(CAST(meta_value AS BINARY)) AS UNSIGNED) AS value_bytes FROM ",
        );
        query.push(self.prefix.table(Table::PostMeta));
        if let Some(id) = after {
            query.push(" WHERE meta_id > ").push_bind(id);
        }
        query
            .push(" ORDER BY meta_id ASC LIMIT ")
            .push_bind(limit.get());
        query
            .build_query_as::<PostMetaLengths>()
            .fetch_all(&mut *self.transaction)
            .await
    }

    /// An ID-only capacity probe: never fetch a potentially large meta_value
    /// just to discover that a bounded view has exceeded its row allowance.
    async fn post_meta_exists_after(&mut self, after: Option<u64>) -> Result<bool, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new("SELECT meta_id FROM ");
        query.push(self.prefix.table(Table::PostMeta));
        if let Some(id) = after {
            query.push(" WHERE meta_id > ").push_bind(id);
        }
        query.push(" ORDER BY meta_id ASC LIMIT 1");
        Ok(query
            .build()
            .fetch_optional(&mut *self.transaction)
            .await?
            .is_some())
    }

    pub async fn options_after(
        &mut self,
        last_id: u64,
        limit: NonZeroU16,
    ) -> Result<Vec<OptionRow>, sqlx::Error> {
        self.options_page(Some(last_id), limit).await
    }

    /// None includes a stored option_id of zero; Some(id) resumes strictly after it.
    pub async fn options_page(
        &mut self,
        after: Option<u64>,
        limit: NonZeroU16,
    ) -> Result<Vec<OptionRow>, sqlx::Error> {
        let mut query = QueryBuilder::<MySql>::new(
            "SELECT option_id,CAST(option_name AS BINARY) AS option_name,CAST(option_value AS BINARY) AS option_value,autoload FROM ",
        );
        query.push(self.prefix.table(Table::Options));
        if let Some(id) = after {
            query.push(" WHERE option_id > ").push_bind(id);
        }
        query
            .push(" ORDER BY option_id ASC LIMIT ")
            .push_bind(limit.get());
        query
            .build()
            .fetch_all(&mut *self.transaction)
            .await?
            .into_iter()
            .map(|row| {
                Ok(OptionRow {
                    option_id: row.try_get("option_id")?,
                    option_name: row.try_get("option_name")?,
                    option_value: row.try_get("option_value")?,
                    autoload: row.try_get("autoload")?,
                })
            })
            .collect()
    }

    pub async fn finish(self) -> Result<(), sqlx::Error> {
        self.transaction.rollback().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn prefix_keeps_wordpress_domain_without_identifier_injection() {
        for prefix in ["", "wp_", "Ab12_"] {
            assert!(TablePrefix::new(prefix).is_ok());
        }
        for prefix in [
            "wp-",
            "wp`",
            "日本",
            "wp;DROP TABLE posts",
            "wp.posts",
            "wp ",
            "wp\0",
        ] {
            assert_eq!(TablePrefix::new(prefix), Err(InvalidPrefix));
        }
        assert_eq!(
            TablePrefix::new("wp_").unwrap().table(Table::Posts),
            "`wp_posts`"
        );
    }
}

#[cfg(test)]
mod budgeted_meta_view_database_tests;
#[cfg(test)]
mod comment_database_tests;
#[cfg(test)]
mod database_tests;
#[cfg(test)]
mod meta_view_database_tests;
#[cfg(test)]
mod retained_database_tests;
