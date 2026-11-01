//! Bounded C ABI for the separately licensed MIT oozextract decoder.
use std::{panic::catch_unwind, panic::AssertUnwindSafe, slice};

#[no_mangle]
pub extern "C" fn pbz_codec_version() -> u32 { 1 }

/// Caller-owned non-overlapping buffers must be valid for their lengths.
#[no_mangle]
pub unsafe extern "C" fn pbz_decode(src: *const u8, src_len: usize,
                                   dst: *mut u8, dst_len: usize) -> isize {
    const MAX: usize = 64 * 1024 * 1024;
    if src.is_null() || dst.is_null() || src_len == 0 || src_len > MAX + 65536 || dst_len > MAX {
        return -1;
    }
    match catch_unwind(AssertUnwindSafe(|| {
        let input = slice::from_raw_parts(src, src_len);
        let output = slice::from_raw_parts_mut(dst, dst_len);
        oozextract::Extractor::new().read_from_slice(input, output)
    })) {
        Ok(Ok(n)) if n == dst_len => n as isize,
        Ok(_) => -2,
        Err(_) => -3,
    }
}
