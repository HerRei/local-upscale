//! Offline verification using the same Minisign implementation as the app.
use base64::Engine;
use std::{env, error::Error, ffi::OsString, fs, io::Read, process};

fn verify(args: &[OsString]) -> Result<(), Box<dyn Error>> {
    if args.len() != 3 {
        return Err("usage: localsr-update-verifier PUBLIC_KEY ARTIFACT SIGNATURE".into());
    }
    let decode = |path: &std::ffi::OsStr| -> Result<String, Box<dyn Error>> {
        let encoded = fs::read_to_string(path)?;
        Ok(String::from_utf8(
            base64::engine::general_purpose::STANDARD.decode(encoded.trim())?,
        )?)
    };
    let key = minisign_verify::PublicKey::decode(&decode(&args[0])?)?;
    let signature = minisign_verify::Signature::decode(&decode(&args[2])?)?;
    let mut verifier = key.verify_stream(&signature)?;
    let mut file = fs::File::open(&args[1])?;
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        verifier.update(&buffer[..count]);
    }
    verifier.finalize()?;
    println!("Signature verified");
    Ok(())
}

fn main() {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if let Err(error) = verify(&args) {
        eprintln!("Update signature rejected: {error}");
        process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ring::signature::{Ed25519KeyPair, KeyPair};
    use std::{
        path::PathBuf,
        sync::atomic::{AtomicUsize, Ordering},
    };

    const PAYLOAD: &[u8] = b"LocalSR verifier test payload\n";
    // BLAKE2b-512 of PAYLOAD; minisign's streaming format signs this digest.
    const DIGEST: [u8; 64] = [
        216, 56, 219, 143, 175, 95, 206, 36, 115, 218, 200, 228, 124, 224, 171, 241, 166, 15, 238,
        125, 3, 27, 37, 228, 13, 25, 162, 21, 78, 34, 77, 140, 189, 160, 133, 237, 12, 235, 10,
        143, 23, 218, 91, 237, 189, 153, 147, 157, 116, 29, 241, 79, 195, 3, 118, 16, 184, 100,
        149, 89, 242, 129, 50, 209,
    ];
    const ID: &[u8; 8] = b"test-key";
    const COMMENT: &str = "timestamp:0\tfile:payload";
    static NEXT_DIR: AtomicUsize = AtomicUsize::new(0);

    struct Files(PathBuf);
    impl Files {
        fn new() -> Self {
            let path = env::temp_dir().join(format!(
                "localsr-verifier-{}-{}",
                process::id(),
                NEXT_DIR.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn write(&self, name: &str, data: impl AsRef<[u8]>) -> OsString {
            let path = self.0.join(name);
            fs::write(&path, data).unwrap();
            path.into_os_string()
        }
    }
    impl Drop for Files {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn key(seed: u8) -> Ed25519KeyPair {
        Ed25519KeyPair::from_seed_unchecked(&[seed; 32]).unwrap()
    }
    fn encoded_key(key: &Ed25519KeyPair) -> String {
        let raw = [b"Ed".as_slice(), ID, key.public_key().as_ref()].concat();
        let text = format!(
            "untrusted comment: minisign public key\n{}\n",
            base64::engine::general_purpose::STANDARD.encode(raw)
        );
        base64::engine::general_purpose::STANDARD.encode(text)
    }
    fn encoded_signature(key: &Ed25519KeyPair) -> String {
        let signature = key.sign(&DIGEST);
        let raw = [b"ED".as_slice(), ID, signature.as_ref()].concat();
        let global = key.sign(&[signature.as_ref(), COMMENT.as_bytes()].concat());
        let text = format!(
            "untrusted comment: signature from tauri secret key\n{}\ntrusted comment: {}\n{}\n",
            base64::engine::general_purpose::STANDARD.encode(raw),
            COMMENT,
            base64::engine::general_purpose::STANDARD.encode(global.as_ref())
        );
        base64::engine::general_purpose::STANDARD.encode(text)
    }
    fn args(files: &Files, public_key: &str, payload: &[u8], signature: &str) -> Vec<OsString> {
        vec![
            files.write("key", public_key),
            files.write("payload", payload),
            files.write("signature", signature),
        ]
    }
    #[test]
    fn valid_signature_verifies() {
        let files = Files::new();
        let key = key(1);
        assert!(verify(&args(
            &files,
            &encoded_key(&key),
            PAYLOAD,
            &encoded_signature(&key)
        ))
        .is_ok());
    }
    #[test]
    fn tampered_payload_fails() {
        let files = Files::new();
        let key = key(1);
        let error = verify(&args(
            &files,
            &encoded_key(&key),
            b"tampered",
            &encoded_signature(&key),
        ))
        .unwrap_err();
        assert!(matches!(
            error.downcast_ref(),
            Some(minisign_verify::Error::InvalidSignature)
        ));
    }
    #[test]
    fn signature_from_another_key_fails() {
        let files = Files::new();
        let error = verify(&args(
            &files,
            &encoded_key(&key(1)),
            PAYLOAD,
            &encoded_signature(&key(2)),
        ))
        .unwrap_err();
        assert!(matches!(
            error.downcast_ref(),
            Some(minisign_verify::Error::InvalidSignature)
        ));
    }
    #[test]
    fn malformed_input_is_rejected() {
        let files = Files::new();
        let key = key(1);
        let valid_key = encoded_key(&key);
        let valid_signature = encoded_signature(&key);
        assert!(verify(&[]).is_err());
        for bad in [
            "not base64!".to_string(),
            base64::engine::general_purpose::STANDARD.encode("not minisign"),
        ] {
            assert!(verify(&args(&files, &bad, PAYLOAD, &valid_signature)).is_err());
            assert!(verify(&args(&files, &valid_key, PAYLOAD, &bad)).is_err());
        }
    }
}
