use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use rusqlite::{params, Connection, OptionalExtension};

use crate::{
    error::{AppError, AppResult},
    output_names::ReservedOutputs,
    results::{summary_from_row, MediaResults},
    types::{JobRecord, MediaItem},
};

const MEDIA_COLUMNS: &str = "id, path, name, kind, width, height, frame_count, fps, duration_seconds, preview_data_url, probe_status, error, selected, hdr_format, audio_warning";

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

/// A scratch folder an earlier run made for a job and recorded with it, and the
/// output that job was asked to write.
#[derive(Clone, Debug)]
pub struct ScratchRecord {
    pub id: String,
    pub output: PathBuf,
    pub scratch: PathBuf,
}

pub struct Database {
    connection: Connection,
    /// The scratch folders of the jobs that were unfinished when the host last stopped.
    interrupted_scratch: Vec<ScratchRecord>,
}

/// The final states of a job.
const ENDED_STATES: [&str; 4] = ["completed", "failed", "cancelled", "interrupted"];

/// How many set-aside copies of damaged queue files are kept; older ones are removed.
const KEPT_DAMAGED_FILES: usize = 3;

impl Database {
    /// Open the queue. A file SQLite cannot use (not a database, corrupt, a table
    /// that does not fit) is moved aside next to itself and a new queue is made, so
    /// a damaged history never keeps the app from starting. The path of the moved
    /// file comes back with it. Any other failure (a locked file, a read-only
    /// folder) is returned as it is and nothing is moved.
    pub fn open_or_set_aside(path: &Path) -> AppResult<(Self, Option<PathBuf>)> {
        let opened = Self::open(path).and_then(|database| {
            database.check_integrity()?;
            Ok(database)
        });
        match opened {
            Ok(database) => Ok((database, None)),
            Err(error) if is_damage(&error) => {
                let aside = set_aside(path)?;
                Ok((Self::open(path)?, Some(aside)))
            }
            Err(error) => Err(error),
        }
    }

    fn check_integrity(&self) -> AppResult<()> {
        let verdict: String = self
            .connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))?;
        if verdict == "ok" {
            Ok(())
        } else {
            Err(AppError::Database(rusqlite::Error::SqliteFailure(
                rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_CORRUPT),
                Some(verdict),
            )))
        }
    }

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
        let has_hidden: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM pragma_table_info('jobs') WHERE name='hidden')",
            [],
            |row| row.get(0),
        )?;
        if !has_hidden {
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
                [],
            )?;
        }
        let has_scratch: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM pragma_table_info('jobs') WHERE name='scratch_path')",
            [],
            |row| row.get(0),
        )?;
        if !has_scratch {
            connection.execute(
                "ALTER TABLE jobs ADD COLUMN scratch_path TEXT NOT NULL DEFAULT ''",
                [],
            )?;
        }
        // Result history of one media, newest first (see list_media_results).
        connection.execute(
            "CREATE INDEX IF NOT EXISTS jobs_media_results_idx ON jobs(media_id, updated_at)",
            [],
        )?;
        let mut database = Self {
            connection,
            interrupted_scratch: Vec::new(),
        };
        database.interrupted_scratch = database.interrupt_stale_jobs()?;
        Ok(database)
    }

    pub fn close_for_update(&mut self) -> AppResult<()> {
        self.connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE)")?;
        let old = std::mem::replace(&mut self.connection, Connection::open_in_memory()?);
        old.close().map_err(|(_, error)| error)?;
        Ok(())
    }

    /// Mark the jobs a stopped host left running as interrupted. The scratch folders
    /// recorded for the jobs that are not finished come back: those the stopped host
    /// made and no engine of this run can be using.
    fn interrupt_stale_jobs(&self) -> AppResult<Vec<ScratchRecord>> {
        let mut statement = self.connection.prepare(
            "SELECT id, request_json, scratch_path FROM jobs WHERE scratch_path != ''
             AND status IN ('queued', 'starting', 'running', 'cancelling', 'interrupted')",
        )?;
        let records = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })?
            .filter_map(Result::ok)
            .filter_map(|(id, request, scratch)| {
                let request = serde_json::from_str::<serde_json::Value>(&request).ok()?;
                Some(ScratchRecord {
                    id,
                    output: PathBuf::from(request_output(&request)?),
                    scratch: PathBuf::from(scratch),
                })
            })
            .collect();
        drop(statement);
        self.connection.execute(
            "UPDATE jobs SET status = 'interrupted', error = 'The desktop host stopped before the job finished.', updated_at = ?1 WHERE status IN ('starting', 'running', 'cancelling')",
            [now_millis()],
        )?;
        Ok(records)
    }

    /// The scratch folders of the jobs the last stop left unfinished, once.
    pub fn take_interrupted_scratch(&mut self) -> Vec<ScratchRecord> {
        std::mem::take(&mut self.interrupted_scratch)
    }

    /// Remember the scratch folder made for a job, so a later run removes that folder
    /// and no other.
    pub fn record_scratch(&self, id: &str, scratch: &Path) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET scratch_path=?2 WHERE id=?1",
            params![id, scratch.to_string_lossy()],
        )?;
        Ok(())
    }

    /// The recorded folder is gone or removed; it needs no more attention.
    pub fn forget_scratch(&self, id: &str) -> AppResult<()> {
        self.connection
            .execute("UPDATE jobs SET scratch_path='' WHERE id=?1", [id])?;
        Ok(())
    }

    pub fn list_media(&self) -> AppResult<Vec<MediaItem>> {
        let mut statement = self.connection.prepare(&format!(
            "SELECT {MEDIA_COLUMNS} FROM media ORDER BY created_at, rowid"
        ))?;
        let rows = statement.query_map([], media_from_row)?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn get_media(&self, id: &str) -> AppResult<Option<MediaItem>> {
        Ok(self
            .connection
            .query_row(
                &format!("SELECT {MEDIA_COLUMNS} FROM media WHERE id = ?1"),
                [id],
                media_from_row,
            )
            .optional()?)
    }

    pub fn get_media_by_path(&self, path: &str) -> AppResult<Option<MediaItem>> {
        Ok(self
            .connection
            .query_row(
                &format!("SELECT {MEDIA_COLUMNS} FROM media WHERE path = ?1"),
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

    /// Remove a picture while a queue may run. In one transaction: a picture whose job is
    /// starting, running or cancelling, or that is part of the batch the engine was sent
    /// (`engine_job_ids`), is refused; any other job it has goes with it (its results on disk
    /// stay). If it was selected, the picture being worked on becomes the selection.
    pub fn remove_media_guarded(&self, id: &str, engine_job_ids: &[String]) -> AppResult<()> {
        let transaction = self.connection.unchecked_transaction()?;
        let mut statement = transaction.prepare("SELECT id, status FROM jobs WHERE media_id=?1")?;
        let jobs = statement
            .query_map([id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        drop(statement);
        let working = jobs.iter().any(|(job, status)| {
            matches!(status.as_str(), "starting" | "running" | "cancelling")
                || engine_job_ids.contains(job)
        });
        if working {
            return Err(AppError::Validation(CANNOT_REMOVE_RUNNING.into()));
        }
        let was_selected = transaction
            .query_row("SELECT selected FROM media WHERE id=?1", [id], |row| {
                row.get::<_, bool>(0)
            })
            .optional()?
            .unwrap_or(false);
        transaction.execute("DELETE FROM media WHERE id = ?1", [id])?;
        if was_selected {
            transaction.execute(
                "UPDATE media SET selected = 1 WHERE id = COALESCE(
                   (SELECT media_id FROM jobs WHERE status IN ('starting','running','cancelling') ORDER BY rowid LIMIT 1),
                   (SELECT id FROM media ORDER BY created_at, rowid LIMIT 1))",
                [],
            )?;
        }
        transaction.commit()?;
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
                seconds_per_unit: 0.0,
            })
        })?;
        let mut jobs = rows.collect::<Result<Vec<_>, _>>()?;
        crate::queue_timing::attach_typical_speeds(&mut jobs);
        Ok(jobs)
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
            "SELECT COUNT(*) FROM jobs WHERE media_id=?1 AND hidden=0 AND status IN ('completed','failed','cancelled','interrupted')",
            [media_id],
            |row| row.get(0),
        )?;
        let mut statement = self.connection.prepare(&format!(
            "SELECT {} FROM jobs WHERE media_id=?1 AND hidden=0 AND status IN ('completed','failed','cancelled','interrupted')
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
            "SELECT id, output_path FROM jobs WHERE media_id=?1 AND hidden=0 AND status='completed' ORDER BY updated_at DESC, rowid DESC",
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

    /// All media histories in media order, without the per-media page limit.
    pub fn list_all_media_results(&self) -> AppResult<Vec<MediaResults>> {
        let mut media = self
            .connection
            .prepare("SELECT id FROM media ORDER BY created_at, rowid")?;
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
            "SELECT {}, media_id FROM jobs WHERE hidden=0 AND status IN ('completed','failed','cancelled','interrupted') ORDER BY updated_at DESC, rowid DESC",
            crate::results::SUMMARY_COLUMNS
        ))?;
        let rows = statement.query_map([], |row| {
            Ok((row.get::<_, String>(10)?, summary_from_row(row)?))
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

    /// Take every result out of the Outputs list for good. The job rows stay (the speed
    /// history reads them) and no file is touched. Refused while any picture is being
    /// worked on, like removing it from the list.
    pub fn hide_all_results(&self, engine_job_ids: &[String]) -> AppResult<()> {
        let transaction = self.connection.unchecked_transaction()?;
        let mut statement = transaction.prepare("SELECT id, status FROM jobs")?;
        let working = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<Result<Vec<_>, _>>()?
            .iter()
            .any(|(job, status)| {
                matches!(status.as_str(), "starting" | "running" | "cancelling")
                    || engine_job_ids.contains(job)
            });
        drop(statement);
        if working {
            return Err(AppError::Validation(CANNOT_REMOVE_RUNNING.into()));
        }
        transaction.execute(
            "UPDATE jobs SET hidden=1 WHERE hidden=0 AND status IN ('completed','failed','cancelled','interrupted')",
            [],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// The saved file of one completed result of this media, for a preview.
    pub fn media_result_output(&self, media_id: &str, job_id: &str) -> AppResult<String> {
        let path: Option<String> = self
            .connection
            .query_row(
                "SELECT output_path FROM jobs WHERE id=?1 AND media_id=?2 AND hidden=0 AND status='completed' AND output_path != ''",
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

    /// A run time for a request that is not queued yet: the typical speed of recent
    /// completed jobs with the same work profile, scaled to this media. None until two
    /// such jobs were measured.
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
        let mut runs = Vec::new();
        while let Some(row) = rows.next()? {
            let (candidate, measured_units) = crate::queue_timing::work_profile(
                &row.get::<_, String>(0)?,
                row.get(1)?,
                row.get(2)?,
                u64::try_from(row.get::<_, i64>(3)?).unwrap_or_default(),
                &row.get::<_, String>(4)?,
            );
            if candidate == key && measured_units > 0.0 {
                runs.push((measured_units, row.get::<_, f64>(5)?));
            }
        }
        Ok(crate::queue_timing::typical_seconds_per_unit(&runs).map(|speed| speed * units))
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
            // A request that is not JSON can never be sent to the engine, so it names no
            // model and no output; one bad row must not block the rest of the queue.
            if let Ok(request) = serde_json::from_str(&row?) {
                requests.push(request);
            }
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

    /// Whether the job has reached a final state, after which nothing may change it.
    pub fn job_has_ended(&self, id: &str) -> AppResult<bool> {
        let status: Option<String> = self
            .connection
            .query_row("SELECT status FROM jobs WHERE id=?1", [id], |row| {
                row.get(0)
            })
            .optional()?;
        Ok(status.is_some_and(|status| ENDED_STATES.contains(&status.as_str())))
    }

    /// A job that ended stays as it ended, and one that is being cancelled stays so
    /// when a late start message arrives.
    pub fn set_job_status(&self, id: &str, status: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status=?2, updated_at=?3 WHERE id=?1
             AND status NOT IN ('completed','failed','cancelled','interrupted')
             AND NOT (status='cancelling' AND ?2 IN ('starting','running'))",
            params![id, status, now_millis()],
        )?;
        Ok(())
    }

    pub fn update_job_progress(&self, id: &str, progress: f64) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status=CASE WHEN status='cancelling' THEN status ELSE 'running' END,
             progress=?2, updated_at=?3 WHERE id=?1
             AND status NOT IN ('completed','failed','cancelled','interrupted')",
            params![id, progress.clamp(0.0, 100.0), now_millis()],
        )?;
        Ok(())
    }

    pub fn finish_job(&self, id: &str, status: &str, output: &str, error: &str) -> AppResult<()> {
        let progress = if status == "completed" { 100.0 } else { 0.0 };
        self.connection.execute(
            "UPDATE jobs SET status=?2, progress=?3, output_path=?4, error=?5, updated_at=?6 WHERE id=?1
             AND status NOT IN ('completed','failed','cancelled','interrupted')",
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
        self.stop_queued_jobs("Cancelled before starting.")
    }

    /// End every queued job as cancelled with `reason`; finished ones are not touched.
    pub fn stop_queued_jobs(&self, reason: &str) -> AppResult<()> {
        self.connection.execute(
            "UPDATE jobs SET status='cancelled', error=?2, updated_at=?1 WHERE status='queued'",
            params![now_millis(), reason],
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

/// A stored image side as a `u32`; a value outside its range (a damaged row) is
/// clamped, so one row cannot make the whole media list fail.
fn side(stored: i64) -> u32 {
    u32::try_from(stored.max(0)).unwrap_or(u32::MAX)
}

fn media_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<MediaItem> {
    Ok(MediaItem {
        id: row.get(0)?,
        path: row.get(1)?,
        name: row.get(2)?,
        kind: row.get(3)?,
        width: side(row.get(4)?),
        height: side(row.get(5)?),
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

/// A failure that says the file's content is unusable, as opposed to the file being
/// busy, read-only or out of reach.
fn is_damage(error: &AppError) -> bool {
    let AppError::Database(
        rusqlite::Error::SqliteFailure(failure, _)
        | rusqlite::Error::SqlInputError { error: failure, .. },
    ) = error
    else {
        return false;
    };
    matches!(
        failure.code,
        rusqlite::ErrorCode::NotADatabase
            | rusqlite::ErrorCode::DatabaseCorrupt
            | rusqlite::ErrorCode::Unknown
    )
}

/// Rename the queue and its journal files to `<name>.damaged-<time>.<extension>`
/// beside it, and drop the oldest such copies beyond `KEPT_DAMAGED_FILES`.
fn set_aside(path: &Path) -> AppResult<PathBuf> {
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .ok_or_else(|| AppError::Config("the queue file has no name".into()))?;
    let (stem, extension) = match name.rsplit_once('.') {
        Some((stem, extension)) => (stem.to_owned(), format!(".{extension}")),
        None => (name.clone(), String::new()),
    };
    let moved_name = |millis: i64| format!("{stem}.damaged-{millis}{extension}");
    let directory = path.parent().unwrap_or(Path::new("."));
    let mut millis = now_millis();
    while directory.join(moved_name(millis)).exists() {
        millis += 1;
    }
    let moved = directory.join(moved_name(millis));
    std::fs::rename(path, &moved)?;
    for suffix in ["-wal", "-shm"] {
        let journal = directory.join(format!("{name}{suffix}"));
        if journal.exists() {
            std::fs::rename(
                journal,
                directory.join(format!("{}{suffix}", moved_name(millis))),
            )?;
        }
    }
    let mut copies: Vec<(i64, PathBuf)> = std::fs::read_dir(directory)?
        .filter_map(|entry| {
            let file = entry.ok()?.file_name().to_string_lossy().into_owned();
            let time = file
                .strip_prefix(&format!("{stem}.damaged-"))?
                .strip_suffix(extension.as_str())?
                .parse::<i64>()
                .ok()?;
            Some((time, directory.join(file)))
        })
        .collect();
    copies.sort();
    let surplus = copies.len().saturating_sub(KEPT_DAMAGED_FILES);
    for (_, old) in copies.into_iter().take(surplus) {
        let _ = std::fs::remove_file(&old);
        for suffix in ["-wal", "-shm"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", old.display()));
        }
    }
    Ok(moved)
}

/// What the host and the window say when a running picture is asked to be removed.
pub const CANNOT_REMOVE_RUNNING: &str =
    "Cannot remove while it is running. Cancel the run instead.";

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

#[cfg(test)]
mod tests;
