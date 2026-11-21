//! Newline-framed messages from a child's pipe, read without letting one line grow
//! without bound: a child that never ends a line must not be able to fill the
//! host's memory.
use std::io::{self, BufRead};

/// How one read ended.
#[derive(Debug, PartialEq, Eq)]
pub enum Frame {
    /// A whole line is in the buffer, without its line ending. A last line the
    /// pipe ended without a newline counts too.
    Line,
    /// The line is longer than the limit. The buffer holds its first `limit` bytes
    /// and the rest is still unread; `skip_line` drops it.
    TooLong,
    /// The pipe ended and nothing was left to read.
    End,
}

/// Read the next line into `line` (cleared first), keeping at most `limit` bytes of
/// it. A line of exactly `limit` bytes is whole; the line ending does not count.
pub fn read_frame<R: BufRead + ?Sized>(
    reader: &mut R,
    limit: usize,
    line: &mut Vec<u8>,
) -> io::Result<Frame> {
    line.clear();
    loop {
        let chunk = match reader.fill_buf() {
            Ok(chunk) => chunk,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        if chunk.is_empty() {
            return Ok(if line.is_empty() {
                Frame::End
            } else {
                strip_carriage_return(line);
                Frame::Line
            });
        }
        let newline = chunk.iter().position(|&byte| byte == b'\n');
        let take = newline.unwrap_or(chunk.len());
        if line.len() + take > limit {
            let room = limit - line.len();
            line.extend_from_slice(&chunk[..room]);
            reader.consume(room);
            return Ok(Frame::TooLong);
        }
        line.extend_from_slice(&chunk[..take]);
        reader.consume(take + usize::from(newline.is_some()));
        if newline.is_some() {
            strip_carriage_return(line);
            return Ok(Frame::Line);
        }
    }
}

/// Drop the rest of the current line, holding none of it.
pub fn skip_line<R: BufRead + ?Sized>(reader: &mut R) -> io::Result<()> {
    loop {
        let chunk = match reader.fill_buf() {
            Ok(chunk) => chunk,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        if chunk.is_empty() {
            return Ok(());
        }
        match chunk.iter().position(|&byte| byte == b'\n') {
            Some(newline) => {
                reader.consume(newline + 1);
                return Ok(());
            }
            None => {
                let length = chunk.len();
                reader.consume(length);
            }
        }
    }
}

fn strip_carriage_return(line: &mut Vec<u8>) {
    if line.last() == Some(&b'\r') {
        line.pop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    use std::io::{BufReader, Cursor, Read};

    fn frames(bytes: &[u8], limit: usize, capacity: usize) -> Vec<(Frame, Vec<u8>)> {
        let mut reader = BufReader::with_capacity(capacity, Cursor::new(bytes.to_vec()));
        let mut line = Vec::new();
        let mut found = Vec::new();
        loop {
            match read_frame(&mut reader, limit, &mut line).unwrap() {
                Frame::End => return found,
                Frame::TooLong => {
                    found.push((Frame::TooLong, line.clone()));
                    skip_line(&mut reader).unwrap();
                }
                Frame::Line => found.push((Frame::Line, line.clone())),
            }
        }
    }

    #[test]
    fn normal_traffic_is_split_into_its_lines() {
        let found = frames(b"one\r\n\ntwo\nlast", 16, 4);
        let lines: Vec<&[u8]> = found.iter().map(|(_, line)| line.as_slice()).collect();
        assert!(found.iter().all(|(frame, _)| *frame == Frame::Line));
        assert_eq!(lines, [&b"one"[..], b"", b"two", b"last"]);
        assert!(frames(b"", 16, 4).is_empty());
    }

    #[test]
    fn a_line_that_never_ends_stops_at_the_limit() {
        // An endless source of bytes without a newline: the read returns, holding
        // no more than the limit.
        let mut reader = BufReader::with_capacity(8192, std::io::repeat(b'x'));
        let mut line = Vec::new();
        let limit = 1 << 20;
        assert_eq!(
            read_frame(&mut reader, limit, &mut line).unwrap(),
            Frame::TooLong
        );
        assert_eq!(line.len(), limit);
    }

    #[test]
    fn an_oversized_line_is_dropped_and_the_traffic_around_it_survives() {
        let mut bytes = b"before\n".to_vec();
        bytes.extend(std::iter::repeat_n(b'z', 100_000));
        bytes.extend_from_slice(b"\nafter\n");
        let found = frames(&bytes, 1000, 4096);
        assert_eq!(found.len(), 3);
        assert_eq!(found[0], (Frame::Line, b"before".to_vec()));
        assert_eq!(found[1].0, Frame::TooLong);
        assert_eq!(found[1].1.len(), 1000);
        assert_eq!(found[2], (Frame::Line, b"after".to_vec()));
    }

    #[test]
    fn a_line_of_exactly_the_limit_is_whole() {
        assert_eq!(
            frames(b"abcd\nabcde\n", 4, 3)[0],
            (Frame::Line, b"abcd".to_vec())
        );
        assert_eq!(frames(b"abcd\nabcde\n", 4, 3)[1].0, Frame::TooLong);
        assert_eq!(frames(b"abcd", 4, 3)[0], (Frame::Line, b"abcd".to_vec()));
    }

    #[test]
    fn the_engine_limit_admits_the_largest_message_and_a_huge_valid_one_is_refused() {
        use crate::{types::WorkerEnvelope, worker::MAX_ENGINE_LINE_BYTES};
        // The largest message the engine sends: a 2048 x 2048 Compare crop, PNG
        // coded at the worst, in base64.
        let largest = 2048 * 2048 * 4;
        let message = |payload: usize| {
            let body = "A".repeat(payload);
            format!(r#"{{"type":"region_ready","data":{{"png_base64":"{body}"}}}}"#).into_bytes()
        };
        let mut traffic = b"{\"type\":\"log\",\"data\":{}}\n".to_vec();
        traffic.extend(message(largest));
        traffic.extend_from_slice(b"\n{\"type\":\"log\",\"data\":{}}\n");
        let found = frames(&traffic, MAX_ENGINE_LINE_BYTES, 64 * 1024);
        assert_eq!(found.len(), 3);
        assert!(found.iter().all(|(frame, _)| *frame == Frame::Line));
        assert_eq!(
            serde_json::from_slice::<WorkerEnvelope>(&found[1].1)
                .unwrap()
                .message_type,
            "region_ready"
        );
        // The same kind of message, valid JSON but over the limit.
        let huge = message(MAX_ENGINE_LINE_BYTES);
        let found = frames(&huge, MAX_ENGINE_LINE_BYTES, 64 * 1024);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].0, Frame::TooLong);
        assert_eq!(found[0].1.len(), MAX_ENGINE_LINE_BYTES);
    }

    #[test]
    fn a_read_error_is_reported_and_an_interruption_is_retried() {
        struct Flaky(u8);
        impl Read for Flaky {
            fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
                self.0 += 1;
                match self.0 {
                    1 => Err(io::ErrorKind::Interrupted.into()),
                    2 => {
                        buffer[..3].copy_from_slice(b"ok\n");
                        Ok(3)
                    }
                    _ => Err(io::Error::other("broken pipe")),
                }
            }
        }
        let mut reader = BufReader::new(Flaky(0));
        let mut line = Vec::new();
        assert_eq!(read_frame(&mut reader, 8, &mut line).unwrap(), Frame::Line);
        assert_eq!(line, b"ok");
        assert!(read_frame(&mut reader, 8, &mut line).is_err());
    }

    proptest! {
        // Whatever the chunking of the pipe and the line lengths, the frames are the
        // lines of the stream, each cut at the limit.
        #[test]
        fn frames_match_the_lines_of_the_stream(
            bytes in proptest::collection::vec(prop_oneof![Just(b'a'), Just(b'\n'), Just(b'\r'), Just(b'{')], 0..200),
            limit in 1usize..12,
            capacity in 1usize..9,
        ) {
            let mut expected = Vec::new();
            let mut pieces: Vec<&[u8]> = bytes.split(|&byte| byte == b'\n').collect();
            if pieces.last().is_some_and(|piece| piece.is_empty()) {
                pieces.pop();
            }
            for piece in pieces {
                if piece.len() > limit {
                    expected.push((Frame::TooLong, piece[..limit].to_vec()));
                } else {
                    let mut line = piece.to_vec();
                    strip_carriage_return(&mut line);
                    expected.push((Frame::Line, line));
                }
            }
            prop_assert_eq!(frames(&bytes, limit, capacity), expected);
        }
    }
}
