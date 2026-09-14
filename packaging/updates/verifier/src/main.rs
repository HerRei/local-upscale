//! Offline verification using the same Minisign implementation as the app.
use base64::Engine;
use std::{env, error::Error, fs, io::Read, process};

fn verify() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("usage: localsr-update-verifier PUBLIC_KEY ARTIFACT SIGNATURE".into());
    }
    let decode = |path: &std::ffi::OsStr| -> Result<String, Box<dyn Error>> {
        let encoded = fs::read_to_string(path)?;
        Ok(String::from_utf8(base64::engine::general_purpose::STANDARD.decode(encoded.trim())?)?)
    };
    let key = minisign_verify::PublicKey::decode(&decode(&args[0])?)?;
    let signature = minisign_verify::Signature::decode(&decode(&args[2])?)?;
    let mut verifier = key.verify_stream(&signature)?;
    let mut file = fs::File::open(&args[1])?;
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 { break; }
        verifier.update(&buffer[..count]);
    }
    verifier.finalize()?;
    println!("Signature verified");
    Ok(())
}

fn main() {
    if let Err(error) = verify() {
        eprintln!("Update signature rejected: {error}");
        process::exit(1);
    }
}
