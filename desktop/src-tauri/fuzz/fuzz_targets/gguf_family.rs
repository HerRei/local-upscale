#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = localsr_next_lib::fuzz_detect_gguf_edit_family(data);
});
