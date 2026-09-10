use std::{
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

use rusqlite::{params, Connection, OptionalExtension};

use crate::{
    error::AppResult,
    types::{JobRecord, MediaItem},
};

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
        let database = Self { connection };
        database.interrupt_stale_jobs()?;
        Ok(database)
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
            "SELECT jobs.id, jobs.media_id, media.name, media.kind, jobs.status, jobs.progress, jobs.output_path, jobs.error, jobs.created_at FROM jobs JOIN media ON media.id = jobs.media_id ORDER BY jobs.created_at DESC LIMIT 200",
        )?;
        let rows = statement.query_map([], |row| {
            Ok(JobRecord {
                id: row.get(0)?,
                media_id: row.get(1)?,
                media_name: row.get(2)?,
                media_kind: row.get(3)?,
                status: row.get(4)?,
                progress: row.get(5)?,
                output_path: row.get(6)?,
                error: row.get(7)?,
                created_at: row.get(8)?,
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
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
}
