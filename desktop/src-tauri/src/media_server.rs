//! Private streaming transport for WebKitGTK video. No directory is served:
//! only native-authorized files have unguessable, session-lifetime URLs.

use std::{
    collections::HashMap,
    net::TcpListener,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, HeaderValue, Method, StatusCode},
    response::{IntoResponse, Response},
    Router,
};
use tokio::sync::oneshot;
use tower_http::services::ServeFile;
use uuid::Uuid;

use crate::{error::AppResult, state::lock};

type Files = Arc<Mutex<HashMap<String, PathBuf>>>;

pub struct MediaServer {
    origin: String,
    files: Files,
    shutdown: Option<oneshot::Sender<()>>,
}

impl MediaServer {
    pub fn start() -> AppResult<Self> {
        let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))?;
        listener.set_nonblocking(true)?;
        let origin = format!("http://{}", listener.local_addr()?);
        let files = Files::default();
        let app = Router::new()
            .fallback(stream_file)
            .with_state(files.clone());
        let (shutdown, stopped) = oneshot::channel();
        tauri::async_runtime::spawn(async move {
            if let Ok(listener) = tokio::net::TcpListener::from_std(listener) {
                let _ = axum::serve(listener, app)
                    .with_graceful_shutdown(async {
                        let _ = stopped.await;
                    })
                    .await;
            }
        });
        Ok(Self {
            origin,
            files,
            shutdown: Some(shutdown),
        })
    }

    pub fn url_for(&self, path: &Path) -> AppResult<String> {
        let path = std::fs::canonicalize(path)?;
        if !path.is_file() {
            return Err(crate::error::AppError::Validation(
                "video file is unavailable".into(),
            ));
        }
        let mut files = lock(&self.files)?;
        // Reusing an entry also keeps repeated selection from growing the map.
        let key = files
            .iter()
            .find_map(|(key, value)| (value == &path).then(|| key.clone()))
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        files.insert(key.clone(), path);
        Ok(format!("{}/{key}", self.origin))
    }
}

impl Drop for MediaServer {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
    }
}

async fn stream_file(State(files): State<Files>, request: Request) -> Response {
    if request.method() != Method::GET && request.method() != Method::HEAD {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }
    let path = files.lock().ok().and_then(|files| {
        files
            .get(request.uri().path().trim_start_matches('/'))
            .cloned()
    });
    let Some(path) = path else {
        return StatusCode::NOT_FOUND.into_response();
    };
    // Reject a registered path subsequently replaced by a symlink to another file.
    if tokio::fs::canonicalize(&path).await.ok().as_ref() != Some(&path) {
        return StatusCode::NOT_FOUND.into_response();
    }
    // ServeFile implements HEAD, full responses, suffix/open/closed byte ranges,
    // and 416. Streaming uses bounded 64 KiB buffers even for multi-GB media.
    match ServeFile::new(path).try_call(request).await {
        Ok(response) => {
            let mut response = response.map(Body::new);
            response
                .headers_mut()
                .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
            response
        }
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn streams_ranges_and_head_without_exposing_other_files() {
        tauri::async_runtime::block_on(async {
            let directory = tempfile::tempdir().unwrap();
            let file = directory.path().join("film ü.mp4");
            std::fs::write(&file, b"0123456789").unwrap();
            let server = MediaServer::start().unwrap();
            let url = server.url_for(&file).unwrap();
            assert_eq!(url, server.url_for(&file).unwrap());
            assert!(url.starts_with("http://127.0.0.1:"));
            let client = reqwest::Client::new();
            let response = client.get(&url).send().await.unwrap();
            assert_eq!(response.status(), 200);
            assert_eq!(response.headers()["cache-control"], "no-store");
            assert_eq!(response.bytes().await.unwrap().as_ref(), b"0123456789");
            for (range, expected, content_range) in [
                ("bytes=3-5", "345", "bytes 3-5/10"),
                ("bytes=7-", "789", "bytes 7-9/10"),
                ("bytes=-2", "89", "bytes 8-9/10"),
            ] {
                let response = client
                    .get(&url)
                    .header("Range", range)
                    .send()
                    .await
                    .unwrap();
                assert_eq!(response.status(), 206);
                assert_eq!(response.headers()["content-range"], content_range);
                assert_eq!(response.text().await.unwrap(), expected);
            }
            let response = client.head(&url).send().await.unwrap();
            assert_eq!(response.headers()["content-length"], "10");
            assert!(response.bytes().await.unwrap().is_empty());
            assert_eq!(
                client
                    .get(&url)
                    .header("Range", "bytes=100-")
                    .send()
                    .await
                    .unwrap()
                    .status(),
                416
            );
            assert_eq!(client.post(&url).send().await.unwrap().status(), 405);
            assert_eq!(
                client
                    .get(format!("{}/film.mp4", server.origin))
                    .send()
                    .await
                    .unwrap()
                    .status(),
                404
            );
            std::fs::remove_file(file).unwrap();
            assert_eq!(client.get(&url).send().await.unwrap().status(), 404);
        });
    }

    #[test]
    #[cfg(unix)]
    fn reads_the_end_of_a_large_sparse_video_and_closes_its_listener() {
        tauri::async_runtime::block_on(async {
            use std::io::{Seek, Write};
            let directory = tempfile::tempdir().unwrap();
            let path = directory.path().join("large.mp4");
            let mut file = std::fs::File::create(&path).unwrap();
            file.set_len(5 * 1024 * 1024 * 1024).unwrap();
            file.seek(std::io::SeekFrom::End(-4)).unwrap();
            file.write_all(b"last").unwrap();
            drop(file);
            let server = MediaServer::start().unwrap();
            let url = server.url_for(&path).unwrap();
            let client = reqwest::Client::new();
            let response = client
                .get(&url)
                .header("Range", "bytes=-4")
                .send()
                .await
                .unwrap();
            assert_eq!(response.status(), 206);
            assert_eq!(response.text().await.unwrap(), "last");
            drop(server);
            for _ in 0..100 {
                if client.get(&url).send().await.is_err() {
                    return;
                }
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
            panic!("media listener remained open after disposal");
        });
    }
}
