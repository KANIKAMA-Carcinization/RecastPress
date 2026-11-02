#[cfg(test)]
mod rp_raw_error_tests {
    use super::*;

    fn connection() -> Conn {
        let mut conn = Conn::empty(Opts::default());
        conn.inner.handshake_complete = true;
        conn.inner.capabilities = CapabilityFlags::CLIENT_PROTOCOL_41;
        conn
    }

    fn error(conn: &mut Conn, code: u16, state: [u8; 5], message: &[u8]) -> crate::ServerError {
        let mut wire = vec![0xff];
        wire.extend_from_slice(&code.to_le_bytes());
        wire.push(b'#');
        wire.extend_from_slice(&state);
        wire.extend_from_slice(message);
        let packet = crate::buffer_pool().get_with(wire);
        match conn.handle_packet(&packet) {
            Err(Error::Server(error)) => error,
            _ => panic!("fixed ERR payload must follow the existing ServerError path"),
        }
    }

    #[test]
    fn rp_raw_error_retains_bytes_while_existing_string_is_lossy() {
        let mut conn = connection();
        assert!(conn.last_error_packet().is_none());
        let message = b"prefix\0\xff\xfe suffix";
        let legacy = error(&mut conn, 1064, *b"42000", message);
        let raw = conn.last_error_packet().unwrap();
        assert_eq!(raw.error_code(), 1064);
        assert_eq!(raw.sql_state_ref().unwrap().as_bytes(), *b"42000");
        assert_eq!(raw.message_ref(), message);
        assert_eq!(raw as *const _, conn.last_error_packet().unwrap() as *const _);
        assert_eq!(legacy.code, 1064);
        assert_eq!(legacy.state, "42000");
        assert_eq!(legacy.message, String::from_utf8_lossy(message));
        assert_ne!(legacy.message.as_bytes(), raw.message_ref());
        assert!(legacy.to_string().contains('\u{fffd}'));
        assert!(conn.last_ok_packet().is_none());
    }

    #[test]
    fn rp_raw_error_replaces_previous_error_fields() {
        let mut conn = connection();
        error(&mut conn, 1064, *b"42000", b"first\xff");
        error(&mut conn, 1146, *b"42S02", b"second\x80\0");
        let raw = conn.last_error_packet().unwrap();
        assert_eq!(raw.error_code(), 1146);
        assert_eq!(raw.sql_state_ref().unwrap().as_bytes(), *b"42S02");
        assert_eq!(raw.message_ref(), b"second\x80\0");
        assert!(conn.last_ok_packet().is_none());
    }

    #[test]
    fn rp_raw_error_clears_after_existing_ok_handler() {
        let mut conn = connection();
        error(&mut conn, 1064, *b"42000", b"before OK\xff");
        assert!(conn.last_error_packet().is_some());
        // Common OK: affected=0, insert=0, AUTOCOMMIT, warnings=0.
        let packet = crate::buffer_pool().get_with([0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00]);
        assert!(!conn.handle_packet(&packet).unwrap());
        assert!(conn.last_error_packet().is_none());
        assert!(conn.last_ok_packet().is_some());
        assert_eq!(conn.last_ok_packet().unwrap().status_flags(), StatusFlags::SERVER_STATUS_AUTOCOMMIT);
    }
}
