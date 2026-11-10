use std::{
    collections::HashMap,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

use rusqlite::{params, Connection, OptionalExtension};

use crate::{
    error::{AppError, AppResult},
    output_names::ReservedOutputs,
    results::{summary_from_row, MediaResults},
    types::{JobRecord, MediaItem},
};

/// A job that `admit_jobs` inserts. `ui_json` is host-only metadata.
#[derive(Clone, Debug)]
pub struct NewJob {
    pub id: String,
    pub media_id: String,
    pub request_json: String,
    pub ui_json: String,
}

#[derive(Clone, Debug)]
pub struct PendingJob {
    pub id: String,
    pub request_json: String,
}

pub struct Database {
    connection: Connection,
}

impl Database {
    pub fn open(path: &Path) -> AppResult<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(path)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS media (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                frame_count INTEGER NOT NULL DEFAULT 0,
                fps REAL NOT NULL DEFAULT 0,
                duration_seconds REAL NOT NULL DEFAULT 0,
                preview_data_url TEXT NOT NULL DEFAULT '',
                probe_status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS media_created_idx ON media(created_at);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                output_path TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs(status, created_at);
            ",
        )?;
        // Existing queues predate HDR metadata; preserve every media/job row.
        for column in ["hdr_format", "audio_warning"] {
            let present: bool = connection.query_row(
                "SELECT EXISTS(SELECT 1 FROM pragma_table_info('media') WHERE name=?1)",
                [column],
                |row| row.get(0),
            )?;
            if !present {
                connection.execute(
                    &format!("ALTER TABLE media ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"),
                    [],
                )?;
            }
        }
        let has_duration: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM pragma_table_info('jobs') WHERE name='elapsed_seconds')",
            [],
            |row| row.get(0),
        )?;
        if !has_duration {
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN elapsed_seconds REAL NOT NULL DEFAULT 0",
                [],
            )?;
        }
        let has_ui: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM pragma_table_info('jobs') WHERE name='ui_json')",
            [],
            |row| row.get(0),
        )?;
        if !has_ui {
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN ui_json TEXT NOT NULL DEFAULT ''",
                [],
            )?;
        }
        // Result history of one media, newest first (see list_media_results).
        connection.execute(
            "CREATE INDEX IF NOT EXISTS jobs_media_results_idx ON jobs(media_id, updated_at)",
            [],
        )?;
        let database = Self { connection };
        database.interrupt_stale_jobs()?;
        Ok(database)
    }

    pub fn close_for_update(&mut self) -> AppResult<()> {
        self.connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE)")?;
        let old = std::mem::replace(&mut self.connection, Connection::open_in_memory()?);
        old.close().map_err(|(_, error)| error)?;
        Ok(())
    }

    fn interrupt_stale_jobs(&self) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status = 'interrupted', error = 'The desktop host stopped before the job finished.', updated_at = ?1 WHERE status IN ('starting', 'running', 'cancelling')",
            [now_millis()],
        )?;
        Ok(())
    }

    pub fn list_media(&self) -> AppResult<Vec<MediaItem>> {
        let mut statement = self.connection.prepare(
            "SELECT id, path, name, kind, width, height, frame_count, fps, duration_seconds, preview_data_url, probe_status, error, selected, hdr_format, audio_warning FROM media ORDER BY created_at, rowid",
        )?;
        let rows = statement.query_map([], media_from_row)?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn get_media(&self, id: &str) -> AppResult<Option<MediaItem>> {
        Ok(self
            .connection
            .query_row(
                "SELECT id, path, name, kind, width, height, frame_count, fps, duration_seconds, preview_data_url, probe_status, error, selected, hdr_format, audio_warning FROM media WHERE id = ?1",
                [id],
                media_from_row,
            )
            .optional()?)
    }

    pub fn get_media_by_path(&self, path: &str) -> AppResult<Option<MediaItem>> {
        Ok(self
            .connection
            .query_row(
                "SELECT id, path, name, kind, width, height, frame_count, fps, duration_seconds, preview_data_url, probe_status, error, selected, hdr_format, audio_warning FROM media WHERE path = ?1",
                [path],
                media_from_row,
            )
            .optional()?)
    }

    pub fn pending_probe_paths(&self) -> AppResult<Vec<String>> {
        let mut statement = self
            .connection
            .prepare("SELECT path FROM media WHERE probe_status='pending' ORDER BY created_at")?;
        let rows = statement.query_map([], |row| row.get(0))?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn has_inflight_jobs(&self) -> AppResult<bool> {
        Ok(self.connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM jobs WHERE status IN ('queued','starting','running','cancelling'))",
            [],
            |row| row.get::<_, i64>(0),
        )? != 0)
    }

    pub fn clear_media(&self) -> AppResult<()> {
        self.connection.execute("DELETE FROM media", [])?;
        Ok(())
    }

    pub fn insert_media(&self, id: &str, path: &str, name: &str, kind: &str) -> AppResult<bool> {
        let selected = self.connection.query_row(
            "SELECT CASE WHEN EXISTS(SELECT 1 FROM media) THEN 0 ELSE 1 END",
            [],
            |row| row.get::<_, i64>(0),
        )?;
        let changed = self.connection.execute(
            "INSERT OR IGNORE INTO media (id, path, name, kind, selected, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![id, path, name, kind, selected, now_millis()],
        )?;
        Ok(changed == 1)
    }

    pub fn select_media(&self, id: &str) -> AppResult<()> {
        let transaction = self.connection.unchecked_transaction()?;
        transaction.execute("UPDATE media SET selected = 0", [])?;
        let changed = transaction.execute("UPDATE media SET selected = 1 WHERE id = ?1", [id])?;
        if changed == 0 {
            return Err(rusqlite::Error::QueryReturnedNoRows.into());
        }
        transaction.commit()?;
        Ok(())
    }

    pub fn remove_media(&self, id: &str) -> AppResult<()> {
        let was_selected = self
            .get_media(id)?
            .map(|media| media.selected)
            .unwrap_or(false);
        self.connection
            .execute("DELETE FROM media WHERE id = ?1", [id])?;
        if was_selected {
            self.connection.execute(
                "UPDATE media SET selected = 1 WHERE id = (SELECT id FROM media ORDER BY created_at, rowid LIMIT 1)",
                [],
            )?;
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn update_media_probe(
        &self,
        path: &str,
        kind: &str,
        width: u32,
        height: u32,
        frame_count: u64,
        fps: f64,
        duration_seconds: f64,
        preview_data_url: &str,
        hdr_format: &str,
        audio_warning: &str,
    ) -> AppResult<()> {
        self.connection.execute(
            "UPDATE media SET kind=?2, width=?3, height=?4, frame_count=?5, fps=?6, duration_seconds=?7, preview_data_url=?8, hdr_format=?9, audio_warning=?10, probe_status='ready', error='' WHERE path=?1",
            params![
                path,
                kind,
                width,
                height,
                i64::try_from(frame_count).unwrap_or(i64::MAX),
                fps,
                duration_seconds,
                preview_data_url,
                hdr_format,
                audio_warning
            ],
        )?;
        Ok(())
    }

    pub fn start_media_probe(&self, path: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE media SET probe_status='pending', error='' WHERE path=?1",
            [path],
        )?;
        Ok(())
    }

    pub fn fail_media_probe(&self, path: &str, error: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE media SET probe_status='failed', error=?2 WHERE path=?1",
            params![path, error],
        )?;
        Ok(())
    }

    pub fn insert_job(&self, id: &str, media_id: &str, request_json: &str) -> AppResult<()> {
        let now = now_millis();
        self.connection.execute(
            "INSERT INTO jobs (id, media_id, status, request_json, created_at, updated_at) VALUES (?1, ?2, 'queued', ?3, ?4, ?4)",
            params![id, media_id, request_json, now],
        )?;
        Ok(())
    }

    pub fn list_jobs(&self) -> AppResult<Vec<JobRecord>> {
        let mut statement = self.connection.prepare(
            "SELECT jobs.id, jobs.media_id, media.name, media.kind, jobs.status, jobs.progress, jobs.output_path, jobs.error, jobs.created_at, jobs.request_json, media.width, media.height, media.frame_count, media.hdr_format, jobs.elapsed_seconds FROM jobs JOIN media ON media.id = jobs.media_id
             WHERE jobs.status IN ('queued','starting','running','cancelling')
                OR jobs.rowid IN (SELECT rowid FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT 200)
                OR jobs.rowid IN (SELECT MAX(rowid) FROM jobs WHERE status='completed' GROUP BY media_id)
             ORDER BY jobs.created_at DESC, jobs.rowid DESC",
        )?;
        let rows = statement.query_map([], |row| {
            let (work_key, work_units) = crate::queue_timing::work_profile(
                &row.get::<_, String>(9)?,
                row.get(10)?,
                row.get(11)?,
                u64::try_from(row.get::<_, i64>(12)?).unwrap_or_default(),
                &row.get::<_, String>(13)?,
            );
            let (model_id, model_label) = crate::results::request_model(
                &serde_json::from_str::<serde_json::Value>(&row.get::<_, String>(9)?)
                    .unwrap_or_default(),
            );
            Ok(JobRecord {
                model_id,
                model_label,
                id: row.get(0)?,
                media_id: row.get(1)?,
                media_name: row.get(2)?,
                media_kind: row.get(3)?,
                status: row.get(4)?,
                progress: row.get(5)?,
                output_path: row.get(6)?,
                error: row.get(7)?,
                created_at: row.get(8)?,
                work_key,
                work_units,
                elapsed_seconds: row.get(14)?,
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    /// Queue jobs in one transaction, with the check that none of their media
    /// already has unfinished work and the output-name reservation inside it.
    /// `build` receives every output path that unfinished jobs reserve and
    /// returns the jobs to insert; nothing is written if it fails.
    pub fn admit_jobs<F>(&self, media_ids: &[&str], build: F) -> AppResult<()>
    where
        F: FnOnce(&mut ReservedOutputs) -> AppResult<Vec<NewJob>>,
    {
        let transaction = self.connection.unchecked_transaction()?;
        for media_id in media_ids {
            let busy: bool = transaction.query_row(
                "SELECT EXISTS(SELECT 1 FROM jobs WHERE media_id=?1 AND status IN ('queued','starting','running','cancelling'))",
                [media_id],
                |row| row.get(0),
            )?;
            if busy {
                return Err(AppError::Validation(
                    "one or more selected items are already running or queued".into(),
                ));
            }
        }
        let mut reserved: ReservedOutputs = self.unfinished_output_paths()?.into_iter().collect();
        let jobs = build(&mut reserved)?;
        let now = now_millis();
        for job in &jobs {
            transaction.execute(
                "INSERT INTO jobs (id, media_id, status, request_json, ui_json, created_at, updated_at) VALUES (?1, ?2, 'queued', ?3, ?4, ?5, ?5)",
                params![job.id, job.media_id, job.request_json, job.ui_json, now],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }

    /// The finished attempts of one media, newest first, independently of the
    /// bounded job snapshot. Ties in completion time break by insertion order.
    pub fn list_media_results(
        &self,
        media_id: &str,
        limit: u32,
        before: Option<&str>,
    ) -> AppResult<MediaResults> {
        let limit = limit.clamp(1, 50);
        let total_count: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM jobs WHERE media_id=?1 AND status IN ('completed','failed','cancelled','interrupted')",
            [media_id],
            |row| row.get(0),
        )?;
        let mut statement = self.connection.prepare(&format!(
            "SELECT {} FROM jobs WHERE media_id=?1 AND status IN ('completed','failed','cancelled','interrupted')
               AND (?2 IS NULL OR (updated_at, rowid) < (SELECT updated_at, rowid FROM jobs WHERE id=?2 AND media_id=?1))
             ORDER BY updated_at DESC, rowid DESC LIMIT ?3",
            crate::results::SUMMARY_COLUMNS
        ))?;
        let mut results = statement
            .query_map(params![media_id, before, limit + 1], summary_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        let next_before = if results.len() > limit as usize {
            results.pop();
            results.last().map(|result| result.job_id.clone())
        } else {
            None
        };
        // Whether a file exists is checked now, over the whole history, so the
        // chooser and the default do not depend on which page was loaded.
        let mut completed = self.connection.prepare(
            "SELECT id, output_path FROM jobs WHERE media_id=?1 AND status='completed' ORDER BY updated_at DESC, rowid DESC",
        )?;
        let completed = completed
            .query_map([media_id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        let available: Vec<&String> = completed
            .iter()
            .filter(|(_, path)| !path.is_empty() && Path::new(path).is_file())
            .map(|(id, _)| id)
            .collect();
        Ok(MediaResults {
            media_id: media_id.to_owned(),
            results,
            total_count: u64::try_from(total_count).unwrap_or_default(),
            completed_count: completed.len() as u64,
            available_count: available.len() as u64,
            default_result_id: available.first().map(|id| (*id).clone()),
            next_before,
        })
    }

    /// All image histories in media order, without the per-image page limit.
    pub fn list_all_media_results(&self) -> AppResult<Vec<MediaResults>> {
        let mut media = self
            .connection
            .prepare("SELECT id FROM media WHERE kind='image' ORDER BY created_at, rowid")?;
        let ids = media
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        let indices: HashMap<String, usize> = ids
            .iter()
            .enumerate()
            .map(|(index, id)| (id.clone(), index))
            .collect();
        let mut groups: Vec<MediaResults> = ids
            .into_iter()
            .map(|media_id| MediaResults {
                media_id,
                results: Vec::new(),
                total_count: 0,
                completed_count: 0,
                available_count: 0,
                default_result_id: None,
                next_before: None,
            })
            .collect();
        let mut statement = self.connection.prepare(&format!(
            "SELECT {}, media_id FROM jobs WHERE status IN ('completed','failed','cancelled','interrupted') ORDER BY updated_at DESC, rowid DESC",
            crate::results::SUMMARY_COLUMNS
        ))?;
        let rows = statement.query_map([], |row| {
            Ok((row.get::<_, String>(8)?, summary_from_row(row)?))
        })?;
        for row in rows {
            let (id, result) = row?;
            if let Some(index) = indices.get(&id) {
                let group = &mut groups[*index];
                group.total_count += 1;
                if result.status == "completed" {
                    group.completed_count += 1;
                }
                if result.available {
                    group.available_count += 1;
                    if group.default_result_id.is_none() {
                        group.default_result_id = Some(result.job_id.clone());
                    }
                }
                group.results.push(result);
            }
        }
        Ok(groups)
    }

    /// The saved file of one completed result of this media, for a preview.
    pub fn media_result_output(&self, media_id: &str, job_id: &str) -> AppResult<String> {
        let path: Option<String> = self
            .connection
            .query_row(
                "SELECT output_path FROM jobs WHERE id=?1 AND media_id=?2 AND status='completed' AND output_path != ''",
                params![job_id, media_id],
                |row| row.get(0),
            )
            .optional()?;
        let path = path.ok_or_else(|| {
            AppError::Validation("choose a completed result of this image".into())
        })?;
        // A result that was replaced by a link would put its target on screen.
        let replaced = std::fs::symlink_metadata(&path)
            .map_or(true, |metadata| metadata.file_type().is_symlink());
        if replaced || !Path::new(&path).is_file() {
            return Err(AppError::Validation(
                "this result's file is no longer available".into(),
            ));
        }
        Ok(path)
    }

    /// Cancel the queued jobs of one media and mark its active job as
    /// cancelling. Returns that active job, which the caller signals.
    pub fn cancel_jobs_for_media(&self, media_id: &str) -> AppResult<Option<String>> {
        let transaction = self.connection.unchecked_transaction()?;
        let now = now_millis();
        transaction.execute(
            "UPDATE jobs SET status='cancelled', error='Cancelled before starting.', updated_at=?2 WHERE media_id=?1 AND status='queued'",
            params![media_id, now],
        )?;
        let active: Option<String> = transaction
            .query_row(
                "SELECT id FROM jobs WHERE media_id=?1 AND status IN ('starting','running','cancelling') ORDER BY rowid LIMIT 1",
                [media_id],
                |row| row.get(0),
            )
            .optional()?;
        transaction.execute(
            "UPDATE jobs SET status='cancelling', updated_at=?2 WHERE media_id=?1 AND status IN ('starting','running')",
            params![media_id, now],
        )?;
        transaction.commit()?;
        Ok(active)
    }

    /// A measured run time for a request that is not queued yet: the latest
    /// completed job with the same work profile, scaled to this media.
    pub fn estimate_request_seconds(
        &self,
        request: &str,
        media: &MediaItem,
    ) -> AppResult<Option<f64>> {
        let (key, units) = crate::queue_timing::work_profile(
            request,
            media.width,
            media.height,
            media.frame_count,
            &media.hdr_format,
        );
        if key.is_empty() || units <= 0.0 {
            return Ok(None);
        }
        let mut statement = self.connection.prepare(
            "SELECT jobs.request_json, media.width, media.height, media.frame_count, media.hdr_format, jobs.elapsed_seconds
               FROM jobs JOIN media ON media.id = jobs.media_id
              WHERE jobs.status='completed' AND jobs.elapsed_seconds > 0
              ORDER BY jobs.updated_at DESC, jobs.rowid DESC",
        )?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let (candidate, measured_units) = crate::queue_timing::work_profile(
                &row.get::<_, String>(0)?,
                row.get(1)?,
                row.get(2)?,
                u64::try_from(row.get::<_, i64>(3)?).unwrap_or_default(),
                &row.get::<_, String>(4)?,
            );
            if candidate == key && measured_units > 0.0 {
                let seconds: f64 = row.get(5)?;
                return Ok(Some(seconds / measured_units * units));
            }
        }
        Ok(None)
    }

    pub fn latest_completed_output_for_media(&self, media_id: &str) -> AppResult<Option<String>> {
        Ok(self
            .connection
            .query_row(
                "SELECT output_path FROM jobs WHERE media_id=?1 AND status='completed' AND output_path != '' ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                [media_id],
                |row| row.get(0),
            )
            .optional()?)
    }

    pub fn is_completed_output(&self, path: &str) -> AppResult<bool> {
        Ok(self
            .connection
            .query_row(
                "SELECT 1 FROM jobs WHERE status='completed' AND output_path != '' AND output_path=?1 LIMIT 1",
                [path],
                |_| Ok(()),
            )
            .optional()?
            .is_some())
    }

    pub fn unfinished_requests(&self) -> AppResult<Vec<serde_json::Value>> {
        let mut statement = self.connection.prepare(
            "SELECT request_json FROM jobs WHERE status IN ('queued','starting','running','cancelling')",
        )?;
        let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
        let mut requests = Vec::new();
        for row in rows {
            requests.push(serde_json::from_str(&row?)?);
        }
        Ok(requests)
    }

    pub fn unfinished_output_paths(&self) -> AppResult<Vec<std::path::PathBuf>> {
        Ok(self
            .unfinished_requests()?
            .iter()
            .filter_map(request_output)
            .map(std::path::PathBuf::from)
            .collect())
    }

    /// The output file the saved request of a job names, which is the only file the
    /// engine may report as that job's result. None for a job that was never queued.
    pub fn requested_output(&self, id: &str) -> AppResult<Option<String>> {
        let request: Option<String> = self
            .connection
            .query_row("SELECT request_json FROM jobs WHERE id=?1", [id], |row| {
                row.get(0)
            })
            .optional()?;
        Ok(request
            .and_then(|request| serde_json::from_str::<serde_json::Value>(&request).ok())
            .and_then(|request| request_output(&request).map(str::to_owned)))
    }

    pub fn unfinished_job_needs_model(&self, path: &Path) -> AppResult<bool> {
        let path = path.to_string_lossy();
        Ok(self.unfinished_requests()?.iter().any(|request| {
            let data = &request["data"];
            data["model_path"].as_str() == Some(path.as_ref())
                || data["face_model_path"].as_str() == Some(path.as_ref())
                || data["stages"].as_array().is_some_and(|stages| {
                    stages
                        .iter()
                        .any(|stage| stage["model_path"].as_str() == Some(path.as_ref()))
                })
        }))
    }

    pub fn next_queued_job(&self) -> AppResult<Option<PendingJob>> {
        Ok(self
            .connection
            .query_row(
                "SELECT id, request_json FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1",
                [],
                |row| Ok(PendingJob { id: row.get(0)?, request_json: row.get(1)? }),
            )
            .optional()?)
    }

    /// The first queued jobs in queue order, for a photo batch.
    pub fn queued_job_window(&self, limit: usize) -> AppResult<Vec<PendingJob>> {
        let mut statement = self.connection.prepare(
            "SELECT id, request_json FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT ?1",
        )?;
        let rows = statement.query_map([i64::try_from(limit).unwrap_or(i64::MAX)], |row| {
            Ok(PendingJob {
                id: row.get(0)?,
                request_json: row.get(1)?,
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    /// Queue a started batch photo again, marked to run on its own (`run_alone`);
    /// a photo that never started or already ended is left as it is.
    pub fn requeue_alone(&self, id: &str) -> AppResult<()> {
        let Some(request) = self
            .connection
            .query_row("SELECT request_json FROM jobs WHERE id=?1", [id], |row| {
                row.get::<_, String>(0)
            })
            .optional()?
        else {
            return Ok(());
        };
        let mut request: serde_json::Value = serde_json::from_str(&request)?;
        request["data"]["run_alone"] = true.into();
        self.connection.execute(
            "UPDATE jobs SET status='queued', progress=0, error='', request_json=?2, updated_at=?3 WHERE id=?1 AND status IN ('starting','running')",
            params![id, request.to_string(), now_millis()],
        )?;
        Ok(())
    }

    /// A job that started is now stopping; one that never started stays as it is.
    pub fn mark_cancelling(&self, id: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status='cancelling', updated_at=?2 WHERE id=?1 AND status IN ('starting','running')",
            params![id, now_millis()],
        )?;
        Ok(())
    }

    pub fn set_job_status(&self, id: &str, status: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status=?2, updated_at=?3 WHERE id=?1",
            params![id, status, now_millis()],
        )?;
        Ok(())
    }

    pub fn update_job_progress(&self, id: &str, progress: f64) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status='running', progress=?2, updated_at=?3 WHERE id=?1",
            params![id, progress.clamp(0.0, 100.0), now_millis()],
        )?;
        Ok(())
    }

    pub fn finish_job(&self, id: &str, status: &str, output: &str, error: &str) -> AppResult<()> {
        let progress = if status == "completed" { 100.0 } else { 0.0 };
        self.connection.execute(
            "UPDATE jobs SET status=?2, progress=?3, output_path=?4, error=?5, updated_at=?6 WHERE id=?1",
            params![id, status, progress, output, error, now_millis()],
        )?;
        Ok(())
    }

    pub fn record_job_duration(&self, id: &str, seconds: f64) -> AppResult<()> {
        if seconds.is_finite() && seconds > 0.0 {
            self.connection.execute(
                "UPDATE jobs SET elapsed_seconds=?2 WHERE id=?1 AND status='completed'",
                params![id, seconds],
            )?;
        }
        Ok(())
    }

    pub fn cancel_queued_jobs(&self) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status='cancelled', error='Cancelled before starting.', updated_at=?1 WHERE status='queued'",
            [now_millis()],
        )?;
        Ok(())
    }

    pub fn interrupt_active_job(&self, id: &str, reason: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status='interrupted', error=?2, updated_at=?3 WHERE id=?1 AND status IN ('starting','running','cancelling')",
            params![id, reason, now_millis()],
        )?;
        Ok(())
    }
}

/// Where a saved job request writes its result: an image or edit output, or a video.
fn request_output(request: &serde_json::Value) -> Option<&str> {
    request["data"]["output_path"]
        .as_str()
        .or_else(|| request["data"]["output_video_path"].as_str())
        .filter(|path| !path.is_empty())
}

fn media_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<MediaItem> {
    Ok(MediaItem {
        id: row.get(0)?,
        path: row.get(1)?,
        name: row.get(2)?,
        kind: row.get(3)?,
        width: row.get(4)?,
        height: row.get(5)?,
        frame_count: u64::try_from(row.get::<_, i64>(6)?).unwrap_or_default(),
        fps: row.get(7)?,
        duration_seconds: row.get(8)?,
        preview_data_url: row.get(9)?,
        probe_status: row.get(10)?,
        error: row.get(11)?,
        selected: row.get::<_, i64>(12)? != 0,
        hdr_format: row.get(13)?,
        audio_warning: row.get(14)?,
    })
}

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(id: &str, media: &str, request: &str, ui: &str) -> NewJob {
        NewJob {
            id: id.into(),
            media_id: media.into(),
            request_json: request.into(),
            ui_json: ui.into(),
        }
    }

    #[test]
    fn result_outputs_are_authorized_for_their_own_media_only() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        let path = directory.path().join("output.png");
        std::fs::write(&path, "preview").unwrap();
        let path = path.to_str().unwrap();
        for media in ["m", "other"] {
            db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
                .unwrap();
        }
        db.insert_job("done", "m", r#"{"type":"job_request"}"#)
            .unwrap();
        db.finish_job("done", "completed", path, "").unwrap();
        db.insert_job(
            "edit",
            "m",
            r#"{"type":"edit_job_request","data":{"model_id":"qwen"}}"#,
        )
        .unwrap();
        db.finish_job("edit", "completed", path, "").unwrap();
        db.insert_job("failed", "m", "{}").unwrap();
        db.finish_job("failed", "failed", "", "broken").unwrap();
        db.insert_job("queued", "m", "{}").unwrap();
        assert_eq!(db.media_result_output("m", "done").unwrap(), path);
        // An Edit result can be shown on its own; the UI decides about a split.
        assert_eq!(db.media_result_output("m", "edit").unwrap(), path);
        for (media, job) in [
            ("other", "done"),
            ("m", "failed"),
            ("m", "queued"),
            ("m", "nope"),
        ] {
            assert!(db.media_result_output(media, job).is_err(), "{media}/{job}");
        }
        std::fs::remove_file(path).unwrap();
        assert!(db.media_result_output("m", "done").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn a_result_replaced_by_a_link_is_no_longer_available() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        let output = directory.path().join("output.png");
        let private = directory.path().join("private.png");
        std::fs::write(&output, "result").unwrap();
        std::fs::write(&private, "private").unwrap();
        db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
        db.insert_job("done", "m", "{}").unwrap();
        db.finish_job("done", "completed", output.to_str().unwrap(), "")
            .unwrap();
        assert!(db.media_result_output("m", "done").is_ok());
        std::fs::remove_file(&output).unwrap();
        std::os::unix::fs::symlink(&private, &output).unwrap();
        assert!(db.media_result_output("m", "done").is_err());
    }

    #[test]
    fn all_media_results_include_unavailable_and_older_outputs() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        for media in ["m", "other"] {
            db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
                .unwrap();
        }
        let path = directory.path().join("kept.png");
        std::fs::write(&path, "pixels").unwrap();
        for index in 0..55 {
            let id = format!("job-{index}");
            db.insert_job(&id, "m", "{}").unwrap();
            db.finish_job(
                &id,
                "completed",
                if index == 0 {
                    path.to_str().unwrap()
                } else {
                    "/missing.png"
                },
                "",
            )
            .unwrap();
        }
        let groups = db.list_all_media_results().unwrap();
        let group = groups.iter().find(|g| g.media_id == "m").unwrap();
        assert_eq!(group.results.len(), 55);
        assert_eq!(group.available_count, 1);
        assert_eq!(group.default_result_id.as_deref(), Some("job-0"));
        assert!(groups
            .iter()
            .any(|g| g.media_id == "other" && g.results.is_empty()));
        std::fs::remove_file(path).unwrap();
        assert_eq!(
            db.list_all_media_results()
                .unwrap()
                .iter()
                .find(|g| g.media_id == "m")
                .unwrap()
                .available_count,
            0
        );
    }

    #[test]
    fn history_is_paginated_independently_of_the_bounded_snapshot() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/tmp/history.png", "history.png", "image")
            .unwrap();
        let kept = directory.path().join("kept.png");
        std::fs::write(&kept, "pixels").unwrap();
        for index in 0..250 {
            let id = format!("result-{index:03}");
            db.insert_job(&id, "m", r#"{"type":"job_request","data":{"output_scale":2,"model_path":"/models/swinir.pth","stages":[{"kind":"upscale","model_id":"swinir","model_path":"/models/swinir.pth"}]}}"#).unwrap();
            let output = if index == 3 {
                kept.to_str().unwrap()
            } else {
                "/tmp/no-such-output.png"
            };
            db.finish_job(&id, "completed", output, "Blur fix skipped")
                .unwrap();
        }
        // Every job finished in the same millisecond: insertion order decides,
        // not the job id (result-249 sorts after result-25 as text).
        db.connection
            .execute("UPDATE jobs SET updated_at=100", [])
            .unwrap();
        let first = db.list_media_results("m", 50, None).unwrap();
        assert_eq!(first.total_count, 250);
        assert_eq!(first.completed_count, 250);
        assert_eq!(first.available_count, 1);
        assert_eq!(first.results.len(), 50);
        assert_eq!(first.results[0].job_id, "result-249");
        assert_eq!(first.results[0].notes, "Blur fix skipped");
        assert_eq!(first.results[0].kind, "upscale");
        assert_eq!(first.results[0].scale, 2);
        assert!(first.results[0].ui.is_none());
        assert!(!first.results[0].available);
        // The default is the newest result whose file exists, beyond this page.
        assert_eq!(first.default_result_id.as_deref(), Some("result-003"));
        let second = db
            .list_media_results("m", 50, first.next_before.as_deref())
            .unwrap();
        assert_eq!(second.results[0].job_id, "result-199");
        let mut before = second.next_before;
        let mut pages = 2;
        while let Some(cursor) = before {
            let page = db.list_media_results("m", 50, Some(&cursor)).unwrap();
            before = page.next_before;
            pages += 1;
        }
        assert_eq!(pages, 5);
        assert_eq!(db.list_jobs().unwrap().len(), 200);
    }

    #[test]
    fn history_lists_failed_attempts_with_their_reason_but_never_as_default() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/tmp/a.png", "a.png", "image")
            .unwrap();
        let output = directory.path().join("a_upscaled_4x.png");
        std::fs::write(&output, "pixels").unwrap();
        db.insert_job("done", "m", "{}").unwrap();
        db.finish_job("done", "completed", output.to_str().unwrap(), "")
            .unwrap();
        db.insert_job("broken", "m", "{}").unwrap();
        db.finish_job("broken", "failed", "", "Out of memory")
            .unwrap();
        db.insert_job("running", "m", "{}").unwrap();
        db.set_job_status("running", "running").unwrap();
        let history = db.list_media_results("m", 50, None).unwrap();
        assert_eq!(
            history
                .results
                .iter()
                .map(|r| (r.job_id.as_str(), r.status.as_str(), r.notes.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("broken", "failed", "Out of memory"),
                ("done", "completed", "")
            ]
        );
        assert_eq!(history.default_result_id.as_deref(), Some("done"));
        assert_eq!((history.total_count, history.completed_count), (2, 1));
    }

    #[test]
    fn admission_keeps_ui_metadata_out_of_worker_requests_and_rolls_back() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/tmp/a.png", "a.png", "image")
            .unwrap();
        let request = r#"{"type":"job_request","data":{"model_path":"swinir.pth","output_path":"/out/a.png"}}"#;
        let ui =
            r#"{"quality":"custom","content":"photo","recipe_name":"My recipe","fixes":["blur"]}"#;
        // A failing insert (a duplicate id) leaves nothing behind.
        assert!(db
            .admit_jobs(&["m"], |_| Ok(vec![
                job("one", "m", request, ui),
                job("one", "m", request, ui)
            ]))
            .is_err());
        assert!(db.list_jobs().unwrap().is_empty());
        // A failing build leaves nothing behind either.
        assert!(db
            .admit_jobs(&["m"], |_| Err(AppError::Validation("no".into())))
            .is_err());
        db.admit_jobs(&["m"], |reserved| {
            assert!(reserved.is_empty());
            Ok(vec![job("one", "m", request, ui)])
        })
        .unwrap();
        assert_eq!(db.next_queued_job().unwrap().unwrap().request_json, request);
        assert_eq!(db.list_jobs().unwrap()[0].model_label, "swinir");
        // The in-flight check is part of the same admission.
        assert!(db
            .admit_jobs(&["m"], |_| Ok(vec![job("two", "m", request, ui)]))
            .is_err());
        db.insert_media("n", "/tmp/n.png", "n.png", "image")
            .unwrap();
        db.admit_jobs(&["n"], |reserved| {
            assert!(reserved.contains(Path::new("/out/a.png")));
            Ok(vec![])
        })
        .unwrap();
        db.finish_job("one", "failed", "", "broken").unwrap();
        let row = db
            .list_media_results("m", 50, None)
            .unwrap()
            .results
            .remove(0);
        assert_eq!(row.ui.unwrap().recipe_name, "My recipe");
        assert_eq!(row.status, "failed");
        assert_eq!(row.notes, "broken");
    }

    #[test]
    fn ui_metadata_leaves_the_timing_profile_alone() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        for media in ["a", "b"] {
            db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
                .unwrap();
            db.update_media_probe(
                &format!("/tmp/{media}.png"),
                "image",
                512,
                512,
                1,
                0.0,
                0.0,
                "",
                "",
                "",
            )
            .unwrap();
        }
        let request = |media: &str| {
            format!(
                r#"{{"type":"job_request","data":{{"job_id":"{media}","image_path":"/tmp/{media}.png","output_path":"/out/{media}.png","model_path":"m.pth","device":"cpu","tile_size":256}}}}"#
            )
        };
        db.admit_jobs(&["a"], |_| {
            Ok(vec![job("a", "a", &request("a"), r#"{"quality":"best"}"#)])
        })
        .unwrap();
        db.admit_jobs(&["b"], |_| {
            Ok(vec![job(
                "b",
                "b",
                &request("b"),
                r#"{"quality":"quick","recipe_name":"Mine"}"#,
            )])
        })
        .unwrap();
        let jobs = db.list_jobs().unwrap();
        assert!(!jobs[0].work_key.is_empty());
        assert_eq!(jobs[0].work_key, jobs[1].work_key);
        assert_eq!(jobs[0].work_units, jobs[1].work_units);
    }

    #[test]
    fn trials_estimate_only_measured_matching_requests() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/tmp/a.png", "a.png", "image")
            .unwrap();
        db.update_media_probe("/tmp/a.png", "image", 512, 512, 1, 0.0, 0.0, "", "", "")
            .unwrap();
        let request = r#"{"type":"job_request","data":{"model_path":"model.pth","device":"cpu","tile_size":256}}"#;
        let media = db.get_media("m").unwrap().unwrap();
        assert!(db
            .estimate_request_seconds(request, &media)
            .unwrap()
            .is_none());
        db.insert_job("j", "m", request).unwrap();
        db.finish_job("j", "completed", "out.png", "").unwrap();
        db.record_job_duration("j", 12.0).unwrap();
        assert_eq!(
            db.estimate_request_seconds(request, &media).unwrap(),
            Some(12.0)
        );
        assert!(db
            .estimate_request_seconds(&request.replace("cpu", "mps"), &media)
            .unwrap()
            .is_none());
    }

    #[test]
    fn cancel_media_preserves_other_media_and_completed_outputs() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        for media in ["m", "other"] {
            db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
                .unwrap();
        }
        for (id, media) in [
            ("done", "m"),
            ("active", "m"),
            ("pending", "m"),
            ("unrelated", "other"),
        ] {
            db.insert_job(id, media, "{}").unwrap();
        }
        db.finish_job("done", "completed", "/tmp/done.png", "")
            .unwrap();
        db.set_job_status("active", "running").unwrap();
        assert_eq!(
            db.cancel_jobs_for_media("m").unwrap().as_deref(),
            Some("active")
        );
        let jobs = db.list_jobs().unwrap();
        for (id, status) in [
            ("done", "completed"),
            ("active", "cancelling"),
            ("pending", "cancelled"),
            ("unrelated", "queued"),
        ] {
            assert_eq!(jobs.iter().find(|job| job.id == id).unwrap().status, status);
        }
        assert_eq!(db.next_queued_job().unwrap().unwrap().id, "unrelated");
        // A media without an active job has nothing to signal; asking twice
        // still names the job that is cancelling.
        assert_eq!(db.cancel_jobs_for_media("other").unwrap(), None);
        assert!(db.next_queued_job().unwrap().is_none());
        assert_eq!(
            db.cancel_jobs_for_media("m").unwrap().as_deref(),
            Some("active")
        );
    }

    #[test]
    fn queued_multi_fix_snapshot_protects_each_stage_after_reopening() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("queue.db");
        let mut request = serde_json::json!({"data":{"stages":[
            {"model_path":"/models/blur"}, {"model_path":"/models/jpeg"}, {"model_path":"/models/noise"}, {"model_path":"/models/main"}, {"model_path":"/models/face"}
        ]}});
        {
            let db = Database::open(&path).unwrap();
            db.insert_media("m", "/tmp/a.png", "a.png", "image")
                .unwrap();
            db.insert_job("j", "m", &request.to_string()).unwrap();
        }
        request["data"]["stages"][0]["model_path"] = serde_json::json!("/models/changed");
        let db = Database::open(&path).unwrap();
        for model in ["blur", "jpeg", "noise", "main", "face"] {
            assert!(db
                .unfinished_job_needs_model(Path::new(&format!("/models/{model}")))
                .unwrap());
        }
        assert!(!db
            .unfinished_job_needs_model(Path::new("/models/changed"))
            .unwrap());
        assert_eq!(
            db.unfinished_requests().unwrap()[0]["data"]["stages"][0]["model_path"],
            "/models/blur"
        );
    }

    #[test]
    fn the_queue_window_is_in_order_and_cancelling_spares_photos_that_never_started() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/tmp/a.png", "a.png", "image")
            .unwrap();
        for id in ["a", "b", "c", "d"] {
            db.insert_job(id, "m", "{}").unwrap();
        }
        db.set_job_status("a", "running").unwrap();
        let window: Vec<String> = db
            .queued_job_window(2)
            .unwrap()
            .into_iter()
            .map(|job| job.id)
            .collect();
        assert_eq!(window, ["b", "c"]);
        db.cancel_queued_jobs().unwrap();
        for id in ["a", "b"] {
            db.mark_cancelling(id).unwrap();
        }
        let status = |id: &str| {
            db.list_jobs()
                .unwrap()
                .into_iter()
                .find(|job| job.id == id)
                .unwrap()
                .status
        };
        assert_eq!(status("a"), "cancelling");
        assert_eq!(status("b"), "cancelled");
    }

    #[test]
    fn unfinished_jobs_protect_all_model_paths() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.db")).unwrap();
        db.insert_media("m", "/tmp/a.png", "a.png", "image")
            .unwrap();
        db.insert_job("j", "m", r#"{"data":{"model_path":"/models/main","face_model_path":"/models/face","stages":[{"model_path":"/models/stage"}]}}"#).unwrap();
        for path in ["/models/main", "/models/face", "/models/stage"] {
            assert!(
                db.unfinished_job_needs_model(Path::new(path)).unwrap(),
                "unfinished job must protect {path}"
            );
        }
        assert!(!db
            .unfinished_job_needs_model(Path::new("/models/other"))
            .unwrap());
        db.set_job_status("j", "completed").unwrap();
        assert!(!db
            .unfinished_job_needs_model(Path::new("/models/main"))
            .unwrap());
    }

    #[test]
    fn hdr_migration_preserves_old_queue_and_retry_clears_failure() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("queue.sqlite3");
        let db = Database::open(&path).unwrap();
        db.insert_media("m1", "/tmp/portrait.mov", "portrait.mov", "video")
            .unwrap();
        db.insert_job("j1", "m1", r#"{"type":"video_job_request"}"#)
            .unwrap();
        // Recreate the pre-HDR schema with a real queued job and selected source.
        db.connection
            .execute("ALTER TABLE media DROP COLUMN hdr_format", [])
            .unwrap();
        db.connection
            .execute("ALTER TABLE media DROP COLUMN audio_warning", [])
            .unwrap();
        drop(db);
        let migrated = Database::open(&path).unwrap();
        assert_eq!(migrated.next_queued_job().unwrap().unwrap().id, "j1");
        let old = migrated.get_media("m1").unwrap().unwrap();
        assert!(old.selected);
        assert_eq!(old.hdr_format, "");
        migrated
            .fail_media_probe(&old.path, "Previous import failed")
            .unwrap();
        migrated.start_media_probe(&old.path).unwrap();
        let pending = migrated.get_media("m1").unwrap().unwrap();
        assert_eq!(pending.probe_status, "pending");
        assert!(pending.error.is_empty());
        migrated
            .update_media_probe(
                &old.path,
                "video",
                2160,
                3840,
                14047,
                59.94,
                234.33,
                "data:image/jpeg;base64,test",
                "HLG",
                "Standard audio will be kept.",
            )
            .unwrap();
        drop(migrated);
        let reopened = Database::open(&path).unwrap();
        let media = reopened.list_media().unwrap().remove(0);
        assert_eq!(media.hdr_format, "HLG");
        assert_eq!(media.audio_warning, "Standard audio will be kept.");
        assert_eq!(media.probe_status, "ready");
        assert_eq!((media.width, media.height), (2160, 3840));
        assert_eq!(
            reopened
                .get_media_by_path(&media.path)
                .unwrap()
                .unwrap()
                .hdr_format,
            "HLG"
        );
    }

    #[test]
    fn queue_is_fifo_and_interrupted_jobs_are_recoverable() {
        let directory = tempfile::tempdir().unwrap();
        let db = Database::open(&directory.path().join("queue.sqlite3")).unwrap();
        db.insert_media("m1", "/tmp/a.png", "a.png", "image")
            .unwrap();
        db.insert_media("m2", "/tmp/b.png", "b.png", "image")
            .unwrap();
        db.insert_job("j1", "m1", r#"{"type":"job_request"}"#)
            .unwrap();
        db.insert_job("j2", "m2", r#"{"type":"job_request"}"#)
            .unwrap();
        assert_eq!(db.next_queued_job().unwrap().unwrap().id, "j1");
        db.set_job_status("j1", "running").unwrap();
        assert_eq!(db.next_queued_job().unwrap().unwrap().id, "j2");
        drop(db);

        let reopened = Database::open(&directory.path().join("queue.sqlite3")).unwrap();
        let jobs = reopened.list_jobs().unwrap();
        assert_eq!(
            jobs.iter().find(|job| job.id == "j1").unwrap().status,
            "interrupted"
        );
        assert_eq!(reopened.next_queued_job().unwrap().unwrap().id, "j2");
    }

    #[test]
    fn queue_timings_migrate_and_large_batches_keep_all_pending_and_owned_results() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("queue.sqlite3");
        let db = Database::open(&path).unwrap();
        db.insert_media("old", "/tmp/old.png", "old.png", "image")
            .unwrap();
        db.insert_media("batch", "/tmp/batch.png", "batch.png", "image")
            .unwrap();
        db.insert_job("finished", "old", r#"{"type":"job_request","data":{}}"#)
            .unwrap();
        db.finish_job("finished", "completed", "/tmp/result.png", "")
            .unwrap();
        for index in 0..250 {
            db.insert_job(
                &format!("pending-{index}"),
                "batch",
                r#"{"type":"job_request","data":{}}"#,
            )
            .unwrap();
        }
        db.connection
            .execute("ALTER TABLE jobs DROP COLUMN elapsed_seconds", [])
            .unwrap();
        drop(db);
        let db = Database::open(&path).unwrap();
        let jobs = db.list_jobs().unwrap();
        assert_eq!(
            jobs.iter().filter(|job| job.status == "queued").count(),
            250
        );
        assert!(jobs
            .iter()
            .any(|job| job.id == "finished" && job.output_path == "/tmp/result.png"));
        assert!(db.is_completed_output("/tmp/result.png").unwrap());
        assert!(!db.is_completed_output("/tmp/batch.png").unwrap());
        assert!(!db.is_completed_output("").unwrap());
        assert_eq!(db.next_queued_job().unwrap().unwrap().id, "pending-0");
        db.record_job_duration("finished", 24.5).unwrap();
        db.record_job_duration("finished", f64::NAN).unwrap();
        db.record_job_duration("pending-0", 3.0).unwrap();
        drop(db);
        let jobs = Database::open(&path).unwrap().list_jobs().unwrap();
        assert_eq!(
            jobs.iter()
                .find(|job| job.id == "finished")
                .unwrap()
                .elapsed_seconds,
            24.5
        );
        assert_eq!(
            jobs.iter()
                .find(|job| job.id == "pending-0")
                .unwrap()
                .elapsed_seconds,
            0.0
        );
    }
}
