//! Editing-only raw user_pass/user_activation_key CAS, not wp_set_password.
//!
//! Caller chooses both replacement values, including any explicit empty key.
//! No generation, algorithm, rehash, auth/session, cache or action integration.
//! Stable InnoDB schema/no triggers is the supported scope; value equality is
//! not an ABA-safe revision, source identity or arbitrary DDL/trigger guarantee.

use super::{INCOMPATIBLE_SQL_MODES, Table, TablePrefix, UserRow, table_engine};
use sqlx::{
    ConnectOptions, MySql, MySqlPool, QueryBuilder, Row, Transaction,
    mysql::{MySqlConnectOptions, MySqlPoolOptions, MySqlRow},
};
use std::fmt;

#[derive(Clone, Eq, PartialEq)]
pub struct UserCredentialFields {
    pub user_pass: Vec<u8>,
    pub user_activation_key: Vec<u8>,
}
impl fmt::Debug for UserCredentialFields {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("UserCredentialFields")
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UserCredentialWriteOutcome {
    Updated,
    NoChange,
    NotFound,
    Conflict,
}

/// Closed classifications never retain a credential, key or driver message.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UserCredentialWriteError {
    StorageMismatch,
    Database,
    /// COMMIT was attempted without an acknowledgement; reconcile before retry.
    CommitOutcomeUnknown,
}
impl fmt::Display for UserCredentialWriteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::StorageMismatch => "stored user fields differ from the requested exact values",
            Self::Database => "user credential transaction failed before a confirmed outcome",
            Self::CommitOutcomeUnknown => {
                "user credential commit outcome is unknown; reconciliation is required"
            }
        })
    }
}
impl std::error::Error for UserCredentialWriteError {}

/// A distinct editing capability, with no public pool/executor or credentials.
/// Does not expand EditingWriter or the read-only repository capabilities.
pub struct EditingCredentialWriter {
    pool: MySqlPool,
    prefix: TablePrefix,
}

const USER_FIELDS: &str = "ID,CAST(user_login AS BINARY) AS user_login,CAST(user_pass AS BINARY) AS user_pass,CAST(user_nicename AS BINARY) AS user_nicename,CAST(user_email AS BINARY) AS user_email,CAST(user_url AS BINARY) AS user_url,CAST(user_registered AS CHAR) AS user_registered,CAST(user_activation_key AS BINARY) AS user_activation_key,user_status,CAST(display_name AS BINARY) AS display_name";

fn decode(row: MySqlRow) -> Result<UserRow, sqlx::Error> {
    Ok(UserRow {
        id: row.try_get("ID")?,
        user_login: row.try_get("user_login")?,
        user_pass: row.try_get("user_pass")?,
        user_nicename: row.try_get("user_nicename")?,
        user_email: row.try_get("user_email")?,
        user_url: row.try_get("user_url")?,
        user_registered: row.try_get("user_registered")?,
        user_activation_key: row.try_get("user_activation_key")?,
        user_status: row.try_get("user_status")?,
        display_name: row.try_get("display_name")?,
    })
}

enum Admission {
    Write,
    Complete(UserCredentialWriteOutcome),
}
fn admit(
    current: Option<&UserRow>,
    expected: &UserRow,
    replacement: &UserCredentialFields,
) -> Admission {
    let Some(current) = current else {
        return Admission::Complete(UserCredentialWriteOutcome::NotFound);
    };
    if current != expected {
        return Admission::Complete(UserCredentialWriteOutcome::Conflict);
    }
    if current.user_pass == replacement.user_pass
        && current.user_activation_key == replacement.user_activation_key
    {
        Admission::Complete(UserCredentialWriteOutcome::NoChange)
    } else {
        Admission::Write
    }
}

fn stored_matches(before: &UserRow, after: &UserRow, replacement: &UserCredentialFields) -> bool {
    after.id == before.id
        && after.user_login == before.user_login
        && after.user_pass == replacement.user_pass
        && after.user_nicename == before.user_nicename
        && after.user_email == before.user_email
        && after.user_url == before.user_url
        && after.user_registered == before.user_registered
        && after.user_activation_key == replacement.user_activation_key
        && after.user_status == before.user_status
        && after.display_name == before.display_name
}
fn after_rollback(
    result: Result<UserCredentialWriteOutcome, UserCredentialWriteError>,
    rollback: Result<(), sqlx::Error>,
) -> Result<UserCredentialWriteOutcome, UserCredentialWriteError> {
    rollback.map_err(|_| UserCredentialWriteError::Database)?;
    result
}
fn after_commit(
    commit: Result<(), sqlx::Error>,
) -> Result<UserCredentialWriteOutcome, UserCredentialWriteError> {
    commit
        .map(|()| UserCredentialWriteOutcome::Updated)
        .map_err(|_| UserCredentialWriteError::CommitOutcomeUnknown)
}

impl EditingCredentialWriter {
    /// Host-supplied editing credentials/TLS; separate private pool, same
    /// SQL-mode/RR session policy. Statement/slow-statement logging is disabled.
    /// Does not grant privileges, change schema/globals or create a service.
    pub async fn connect(
        options: MySqlConnectOptions,
        prefix: TablePrefix,
    ) -> Result<Self, UserCredentialWriteError> {
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
            .connect_with(options.disable_statement_logging())
            .await
            .map_err(|_| UserCredentialWriteError::Database)?;
        Ok(Self { pool, prefix })
    }

    /// Lock the latest full row, compare all ten raw fields in Rust, bind only
    /// the selected pair, read back all fields, then require COMMIT ACK.
    /// Confirmed rejection/no-change requires rollback ACK; rows-affected and
    /// database collation are not CAS tokens. Existing ID0/MAX are not inserts.
    ///
    /// Cancellation has no returned outcome. SQLx Drop schedules rollback, not
    /// an ACK, and cancellation during COMMIT may leave an unknown committed
    /// result. Reconcile via a fresh authorized read; do not blindly retry.
    /// Caller/row/driver allocations are unpooled and not guaranteed erased.
    pub async fn compare_and_set_user_credentials(
        &self,
        expected: &UserRow,
        replacement: &UserCredentialFields,
    ) -> Result<UserCredentialWriteOutcome, UserCredentialWriteError> {
        let mut tx = self
            .pool
            .begin()
            .await
            .map_err(|_| UserCredentialWriteError::Database)?;
        match self.stage(&mut tx, expected, replacement).await {
            Ok(Admission::Write) => after_commit(tx.commit().await),
            Ok(Admission::Complete(outcome)) => after_rollback(Ok(outcome), tx.rollback().await),
            Err(error) => after_rollback(Err(error), tx.rollback().await),
        }
    }

    async fn locked_user(
        &self,
        tx: &mut Transaction<'_, MySql>,
        id: u64,
    ) -> Result<Option<UserRow>, UserCredentialWriteError> {
        let mut query = QueryBuilder::<MySql>::new("SELECT ");
        query
            .push(USER_FIELDS)
            .push(" FROM ")
            .push(self.prefix.table(Table::Users))
            .push(" WHERE ID=")
            .push_bind(id)
            .push(" FOR UPDATE");
        query
            .build()
            .fetch_optional(&mut **tx)
            .await
            .map_err(|_| UserCredentialWriteError::Database)?
            .map(decode)
            .transpose()
            .map_err(|_| UserCredentialWriteError::Database)
    }

    async fn stage(
        &self,
        tx: &mut Transaction<'_, MySql>,
        expected: &UserRow,
        replacement: &UserCredentialFields,
    ) -> Result<Admission, UserCredentialWriteError> {
        let current = self.locked_user(tx, expected.id).await?;
        // The table read obtains metadata protection. Stable-schema scope;
        // nontransactional tables must be rejected before any UPDATE.
        let name = self.prefix.raw_table(Table::Users);
        let engine = table_engine(tx, &name)
            .await
            .map_err(|_| UserCredentialWriteError::Database)?;
        if !matches!(engine, Some(Some(engine)) if engine.eq_ignore_ascii_case("InnoDB")) {
            return Err(UserCredentialWriteError::Database);
        }
        let decision = admit(current.as_ref(), expected, replacement);
        if matches!(decision, Admission::Complete(_)) {
            return Ok(decision);
        }
        let mut update = QueryBuilder::<MySql>::new("UPDATE ");
        update
            .push(self.prefix.table(Table::Users))
            .push(" SET user_pass=")
            .push_bind(&replacement.user_pass)
            .push(",user_activation_key=")
            .push_bind(&replacement.user_activation_key)
            .push(" WHERE ID=")
            .push_bind(expected.id);
        update
            .build()
            .execute(&mut **tx)
            .await
            .map_err(|_| UserCredentialWriteError::Database)?;
        let after = self.locked_user(tx, expected.id).await?;
        if !after
            .as_ref()
            .is_some_and(|row| stored_matches(expected, row, replacement))
        {
            return Err(UserCredentialWriteError::StorageMismatch);
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

    fn row() -> UserRow {
        UserRow {
            id: 0,
            user_login: "Café ".as_bytes().to_vec(),
            user_pass: b"SECRET old hash\0".to_vec(),
            user_nicename: b"old nice".to_vec(),
            user_email: b"old@example.invalid".to_vec(),
            user_url: b"https://example.invalid".to_vec(),
            user_registered: "0000-00-00 00:00:00".into(),
            user_activation_key: b"SECRET old key\0".to_vec(),
            user_status: i32::MIN,
            display_name: "非公開🦀".as_bytes().to_vec(),
        }
    }
    fn replacement() -> UserCredentialFields {
        UserCredentialFields {
            user_pass: b"SECRET replacement\0".to_vec(),
            user_activation_key: Vec::new(),
        }
    }
    fn mutate(row: &mut UserRow, field: usize) {
        match field {
            0 => row.id = u64::MAX,
            1 => row.user_login.push(b'x'),
            2 => row.user_pass.push(b'x'),
            3 => row.user_nicename.push(b'x'),
            4 => row.user_email.push(b'x'),
            5 => row.user_url.push(b'x'),
            6 => row.user_registered.push('x'),
            7 => row.user_activation_key.push(b'x'),
            8 => row.user_status += 1,
            9 => row.display_name.push(b'x'),
            _ => unreachable!(),
        }
    }

    #[test]
    fn admission_compares_all_ten_fields_before_no_change_without_collation_or_status_policy() {
        let old = row();
        assert!(matches!(
            admit(None, &old, &replacement()),
            Admission::Complete(UserCredentialWriteOutcome::NotFound)
        ));
        assert!(matches!(
            admit(Some(&old), &old, &replacement()),
            Admission::Write
        ));
        let same = UserCredentialFields {
            user_pass: old.user_pass.clone(),
            user_activation_key: old.user_activation_key.clone(),
        };
        assert!(matches!(
            admit(Some(&old), &old, &same),
            Admission::Complete(UserCredentialWriteOutcome::NoChange)
        ));
        for field in 0..10 {
            let mut current = old.clone();
            mutate(&mut current, field);
            let same_as_current = UserCredentialFields {
                user_pass: current.user_pass.clone(),
                user_activation_key: current.user_activation_key.clone(),
            };
            assert!(
                matches!(
                    admit(Some(&current), &old, &same_as_current),
                    Admission::Complete(UserCredentialWriteOutcome::Conflict)
                ),
                "field {field}"
            );
        }
        for login in ["café ", "Cafe ", "Café"] {
            let mut current = old.clone();
            current.user_login = login.as_bytes().to_vec();
            assert!(matches!(
                admit(Some(&current), &old, &same),
                Admission::Complete(UserCredentialWriteOutcome::Conflict)
            ));
        }
    }

    #[test]
    fn readback_requires_exact_pair_and_all_eight_untouched_fields_including_zero_date_and_unsigned_id()
     {
        let old = row();
        let pair = replacement();
        let mut after = old.clone();
        after.user_pass = pair.user_pass.clone();
        after.user_activation_key = pair.user_activation_key.clone();
        assert!(stored_matches(&old, &after, &pair));
        for field in 0..10 {
            let mut corrupted = after.clone();
            mutate(&mut corrupted, field);
            assert!(!stored_matches(&old, &corrupted, &pair), "field {field}");
        }
        let mut max_before = old.clone();
        max_before.id = u64::MAX;
        let mut max_after = after;
        max_after.id = u64::MAX;
        assert!(stored_matches(&max_before, &max_after, &pair));
    }

    #[test]
    fn outcomes_require_rollback_or_commit_ack_and_diagnostics_never_retain_sensitive_values() {
        for outcome in [
            UserCredentialWriteOutcome::NoChange,
            UserCredentialWriteOutcome::NotFound,
            UserCredentialWriteOutcome::Conflict,
        ] {
            assert_eq!(after_rollback(Ok(outcome), Ok(())), Ok(outcome));
            assert_eq!(
                after_rollback(Ok(outcome), Err(sqlx::Error::PoolClosed)),
                Err(UserCredentialWriteError::Database)
            );
        }
        assert_eq!(
            after_rollback(Err(UserCredentialWriteError::StorageMismatch), Ok(())),
            Err(UserCredentialWriteError::StorageMismatch)
        );
        assert_eq!(
            after_rollback(
                Err(UserCredentialWriteError::StorageMismatch),
                Err(sqlx::Error::PoolClosed)
            ),
            Err(UserCredentialWriteError::Database)
        );
        assert_eq!(
            after_commit(Ok(())),
            Ok(UserCredentialWriteOutcome::Updated)
        );
        assert_eq!(
            after_commit(Err(sqlx::Error::PoolClosed)),
            Err(UserCredentialWriteError::CommitOutcomeUnknown)
        );
        let pair = replacement();
        assert_eq!(format!("{pair:?}"), "UserCredentialFields { .. }");
        for error in [
            UserCredentialWriteError::StorageMismatch,
            UserCredentialWriteError::Database,
            UserCredentialWriteError::CommitOutcomeUnknown,
        ] {
            assert!(!format!("{error:?} {error}").contains("SECRET"));
            assert!(std::error::Error::source(&error).is_none());
        }
    }
}
