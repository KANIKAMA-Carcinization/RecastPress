//! Compile-only public API witness; this never constructs or connects a Conn.
#![forbid(unsafe_code)]

pub fn retained_error_code(connection: &mysql_async::Conn) -> Option<u16> {
    connection
        .last_error_packet()
        .map(|packet| packet.error_code())
}

/// The borrowed message keeps non-UTF-8 bytes without allocating a String.
pub fn retained_error_message(connection: &mysql_async::Conn) -> Option<&[u8]> {
    connection
        .last_error_packet()
        .map(|packet| packet.message_ref())
}
