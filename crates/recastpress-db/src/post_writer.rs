//! Editing-side raw three-field CAS, not wp_update_post or publication.
//!
//! Caller-prepared strings replace only existing title/content/excerpt. No ID,
//! date, status, slug, revision, authorization, job/outbox or public generation
//! is chosen. The fixed-schema/no-trigger fixture is the validation scope;
//! concurrent schema/engine changes and arbitrary trigger side effects are not.
//! Value equality is not an ABA-safe monotonic revision or source identity.

use super::{INCOMPATIBLE_SQL_MODES, POST_FIELDS, PostRow, Table, TablePrefix, table_engine};
use sqlx::{
    MySql, MySqlPool, QueryBuilder, Transaction,
    mysql::{MySqlConnectOptions, MySqlPoolOptions},
};
use std::fmt;

#[derive(Clone, Eq, PartialEq)]
pub struct PostTextFields {
    pub title: String,
    pub content: String,
    pub excerpt: String,
}
impl fmt::Debug for PostTextFields {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PostTextFields").finish_non_exhaustive()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PostTextWriteOutcome {
    Updated,
    NoChange,
    NotFound,
    Conflict,
}

/// Closed classifications contain no SQL error strings or article data.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PostTextWriteError {
    StorageMismatch,
    Database,
    /// COMMIT was attempted but not acknowledged. It may have committed;
    /// reconcile through a fresh authorized read, never blindly retry.
    CommitOutcomeUnknown,
}
impl fmt::Display for PostTextWriteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::StorageMismatch => "stored post fields differ from the requested exact values",
            Self::Database => "post text transaction failed before a confirmed outcome",
            Self::CommitOutcomeUnknown => {
                "post text commit outcome is unknown; reconciliation is required"
            }
        })
    }
}
impl std::error::Error for PostTextWriteError {}

/// Separate capability; no pool, mutable SQL executor or credentials escape.
/// Never supplied to public HTTP. Construction does not grant DB permissions.
pub struct EditingWriter {
    pool: MySqlPool,
    prefix: TablePrefix,
}

enum Admission {
    Write,
    Complete(PostTextWriteOutcome),
}
fn admit(current: Option<&PostRow>, expected: &PostRow, text: &PostTextFields) -> Admission {
    let Some(current) = current else {
        return Admission::Complete(PostTextWriteOutcome::NotFound);
    };
    if current != expected {
        return Admission::Complete(PostTextWriteOutcome::Conflict);
    }
    if current.post_title == text.title
        && current.post_content == text.content
        && current.post_excerpt == text.excerpt
    {
        Admission::Complete(PostTextWriteOutcome::NoChange)
    } else {
        Admission::Write
    }
}

fn stored_matches(before: &PostRow, after: &PostRow, text: &PostTextFields) -> bool {
    after.id == before.id
        && after.post_author == before.post_author
        && after.post_date == before.post_date
        && after.post_date_gmt == before.post_date_gmt
        && after.post_title == text.title
        && after.post_content == text.content
        && after.post_excerpt == text.excerpt
        && after.post_status == before.post_status
        && after.comment_status == before.comment_status
        && after.ping_status == before.ping_status
        && after.post_password == before.post_password
        && after.post_name == before.post_name
        && after.to_ping == before.to_ping
        && after.pinged == before.pinged
        && after.post_modified == before.post_modified
        && after.post_modified_gmt == before.post_modified_gmt
        && after.post_content_filtered == before.post_content_filtered
        && after.post_parent == before.post_parent
        && after.guid == before.guid
        && after.menu_order == before.menu_order
        && after.post_type == before.post_type
        && after.post_mime_type == before.post_mime_type
        && after.comment_count == before.comment_count
}
fn after_rollback(
    result: Result<PostTextWriteOutcome, PostTextWriteError>,
    rollback: Result<(), sqlx::Error>,
) -> Result<PostTextWriteOutcome, PostTextWriteError> {
    rollback.map_err(|_| PostTextWriteError::Database)?;
    result
}
fn after_commit(
    commit: Result<(), sqlx::Error>,
) -> Result<PostTextWriteOutcome, PostTextWriteError> {
    commit
        .map(|()| PostTextWriteOutcome::Updated)
        .map_err(|_| PostTextWriteError::CommitOutcomeUnknown)
}

impl EditingWriter {
    /// Same editing-session compatibility profile as the reader, in a separate
    /// private pool. Host-supplied options/credentials determine transport and
    /// permissions. No grants, global settings, schema or services are changed.
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

    /// Lock the latest row, compare all 23 fields using Rust exact equality,
    /// update three bound fields and re-read the full row before COMMIT.
    /// Rejections/no-change require an acknowledged rollback before returning.
    /// Rows-affected/found-rows and DB collation equality are never CAS tokens.
    ///
    /// Cancellation returns no outcome: SQLx Drop schedules rollback, and
    /// cancellation during COMMIT can leave an unknown committed result. A
    /// caller must reconcile, not infer rollback completion or retry blindly.
    /// Inputs, row reads and driver/allocator work are unpooled, not RSS bounds.
    pub async fn compare_and_set_post_text(
        &self,
        expected: &PostRow,
        replacement: &PostTextFields,
    ) -> Result<PostTextWriteOutcome, PostTextWriteError> {
        let mut tx = self
            .pool
            .begin()
            .await
            .map_err(|_| PostTextWriteError::Database)?;
        match self.stage(&mut tx, expected, replacement).await {
            Ok(Admission::Write) => after_commit(tx.commit().await),
            Ok(Admission::Complete(outcome)) => after_rollback(Ok(outcome), tx.rollback().await),
            Err(error) => after_rollback(Err(error), tx.rollback().await),
        }
    }

    async fn locked_post(
        &self,
        tx: &mut Transaction<'_, MySql>,
        id: u64,
    ) -> Result<Option<PostRow>, PostTextWriteError> {
        let mut query = QueryBuilder::<MySql>::new("SELECT ");
        query
            .push(POST_FIELDS)
            .push(" FROM ")
            .push(self.prefix.table(Table::Posts))
            .push(" WHERE ID = ")
            .push_bind(id)
            .push(" FOR UPDATE");
        query
            .build()
            .fetch_optional(&mut **tx)
            .await
            .map_err(|_| PostTextWriteError::Database)?
            .map(PostRow::decode)
            .transpose()
            .map_err(|_| PostTextWriteError::Database)
    }

    async fn stage(
        &self,
        tx: &mut Transaction<'_, MySql>,
        expected: &PostRow,
        replacement: &PostTextFields,
    ) -> Result<Admission, PostTextWriteError> {
        let current = self.locked_post(tx, expected.id).await?;
        // The preceding table read acquires metadata protection. The supported
        // schema remains stable; reject nontransactional tables before UPDATE.
        let name = self.prefix.raw_table(Table::Posts);
        let engine = table_engine(tx, &name)
            .await
            .map_err(|_| PostTextWriteError::Database)?;
        if !matches!(engine, Some(Some(engine)) if engine.eq_ignore_ascii_case("InnoDB")) {
            return Err(PostTextWriteError::Database);
        }
        let decision = admit(current.as_ref(), expected, replacement);
        if matches!(decision, Admission::Complete(_)) {
            return Ok(decision);
        }
        let mut update = QueryBuilder::<MySql>::new("UPDATE ");
        update
            .push(self.prefix.table(Table::Posts))
            .push(" SET post_title=")
            .push_bind(&replacement.title)
            .push(",post_content=")
            .push_bind(&replacement.content)
            .push(",post_excerpt=")
            .push_bind(&replacement.excerpt)
            .push(" WHERE ID=")
            .push_bind(expected.id);
        update
            .build()
            .execute(&mut **tx)
            .await
            .map_err(|_| PostTextWriteError::Database)?;
        // Editing sessions can run non-strict SQL modes. A successful UPDATE
        // can truncate text; exact full-row readback must pass before commit.
        let after = self.locked_post(tx, expected.id).await?;
        if !after
            .as_ref()
            .is_some_and(|row| stored_matches(expected, row, replacement))
        {
            return Err(PostTextWriteError::StorageMismatch);
        }
        Ok(Admission::Write)
    }
    pub async fn close(self) {
        self.pool.close().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row() -> PostRow {
        PostRow {
            id: 0,
            post_author: u64::MAX,
            post_date: "0000-00-00 00:00:00".into(),
            post_date_gmt: "0000-00-00 00:00:00".into(),
            post_content: "SECRET body\0".into(),
            post_title: "Café ".into(),
            post_excerpt: "old".into(),
            post_status: "custom".into(),
            comment_status: "closed".into(),
            ping_status: "closed".into(),
            post_password: "SECRET password".into(),
            post_name: "old-slug".into(),
            to_ping: "old".into(),
            pinged: "old".into(),
            post_modified: "0000-00-00 00:00:00".into(),
            post_modified_gmt: "0000-00-00 00:00:00".into(),
            post_content_filtered: "old".into(),
            post_parent: u64::MAX,
            guid: "old-guid".into(),
            menu_order: i32::MIN,
            post_type: "custom".into(),
            post_mime_type: "old".into(),
            comment_count: i64::MIN,
        }
    }
    fn fields() -> PostTextFields {
        PostTextFields {
            title: "new title".into(),
            content: "new\0🦀".into(),
            excerpt: "new excerpt".into(),
        }
    }
    #[test]
    fn admission_uses_all_row_values_before_no_change_and_never_collation_equivalence() {
        let old = row();
        let unchanged = PostTextFields {
            title: old.post_title.clone(),
            content: old.post_content.clone(),
            excerpt: old.post_excerpt.clone(),
        };
        assert!(matches!(
            admit(None, &old, &fields()),
            Admission::Complete(PostTextWriteOutcome::NotFound)
        ));
        assert!(matches!(
            admit(Some(&old), &old, &unchanged),
            Admission::Complete(PostTextWriteOutcome::NoChange)
        ));
        assert!(matches!(
            admit(Some(&old), &old, &fields()),
            Admission::Write
        ));
        for title in ["café ", "Cafe ", "Café"] {
            let mut changed = old.clone();
            changed.post_title = title.into();
            assert!(matches!(
                admit(Some(&changed), &old, &unchanged),
                Admission::Complete(PostTextWriteOutcome::Conflict)
            ));
        }
        let mut changed = old.clone();
        changed.comment_count += 1;
        assert!(matches!(
            admit(Some(&changed), &old, &fields()),
            Admission::Complete(PostTextWriteOutcome::Conflict)
        ));
    }
    #[test]
    fn readback_requires_three_exact_new_fields_and_every_other_field_unchanged() {
        let old = row();
        let text = fields();
        let mut after = old.clone();
        after.post_title = text.title.clone();
        after.post_content = text.content.clone();
        after.post_excerpt = text.excerpt.clone();
        assert!(stored_matches(&old, &after, &text));
        let mut variants = Vec::new();
        macro_rules! different {
            ($field:ident,$value:expr) => {{
                let mut changed = after.clone();
                changed.$field = $value;
                variants.push(changed);
            }};
        }
        different!(id, u64::MAX);
        different!(post_author, 0);
        different!(post_parent, 0);
        different!(menu_order, 0);
        different!(comment_count, 0);
        macro_rules! text_difference {($($field:ident),*)=>{$(different!($field,"different".into());)*};}
        text_difference!(
            post_title,
            post_content,
            post_excerpt,
            post_date,
            post_date_gmt,
            post_status,
            comment_status,
            ping_status,
            post_password,
            post_name,
            to_ping,
            pinged,
            post_modified,
            post_modified_gmt,
            post_content_filtered,
            guid,
            post_type,
            post_mime_type
        );
        assert_eq!(variants.len(), 23);
        for changed in variants {
            assert!(!stored_matches(&old, &changed, &text));
        }
    }
    #[test]
    fn rollback_ack_is_required_and_commit_errors_remain_unknown_without_secret_diagnostics() {
        for result in [
            Ok(PostTextWriteOutcome::Conflict),
            Ok(PostTextWriteOutcome::NotFound),
            Ok(PostTextWriteOutcome::NoChange),
            Err(PostTextWriteError::StorageMismatch),
        ] {
            assert_eq!(after_rollback(result, Ok(())), result);
            assert_eq!(
                after_rollback(result, Err(sqlx::Error::Protocol("SECRET article".into()))),
                Err(PostTextWriteError::Database)
            );
        }
        assert_eq!(after_commit(Ok(())), Ok(PostTextWriteOutcome::Updated));
        assert_eq!(
            after_commit(Err(sqlx::Error::Protocol("SECRET password".into()))),
            Err(PostTextWriteError::CommitOutcomeUnknown)
        );
        for error in [
            PostTextWriteError::StorageMismatch,
            PostTextWriteError::Database,
            PostTextWriteError::CommitOutcomeUnknown,
        ] {
            assert!(!format!("{error} {error:?}").contains("SECRET"));
        }
        assert!(!format!("{:?}", fields()).contains("new title"));
    }
}
