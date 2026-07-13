use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::error::{Result, ReviewError};
use crate::git::current_branch;
use crate::state::ReviewState;

static FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ArchivePayload {
    path: String,
    branch: String,
    review_message: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArchivedReview {
    pub archive_path: PathBuf,
    pub repository_path: String,
    pub branch: String,
    pub review_message: String,
}

impl ArchivedReview {
    #[must_use]
    pub fn timestamp_label(&self) -> String {
        let stem = self
            .archive_path
            .file_stem()
            .and_then(|name| name.to_str())
            .unwrap_or("unknown");
        let mut components = stem.split('-');
        let date = components.next().unwrap_or("unknown");
        let time = components.next().unwrap_or_default();
        if date.len() == 8
            && time.len() == 6
            && date.bytes().all(|byte| byte.is_ascii_digit())
            && time.bytes().all(|byte| byte.is_ascii_digit())
        {
            format!("{date}-{time}")
        } else {
            date.to_owned()
        }
    }
}

pub fn archive_review(state: &ReviewState, review_message: &str) -> Result<PathBuf> {
    let directory = review_archive_dir()?;
    fs::create_dir_all(&directory)
        .map_err(|error| ReviewError::io("could not create review archive directory", error))?;
    let payload = ArchivePayload {
        path: state.repository_root.to_string_lossy().into_owned(),
        branch: current_branch(&state.repository_root)?,
        review_message: review_message.to_owned(),
    };
    let encoded = serde_json::to_vec_pretty(&payload).map_err(|error| {
        ReviewError::Message(format!("could not encode review archive: {error}"))
    })?;
    for _ in 0..100 {
        let path = directory.join(archive_filename(SystemTime::now()));
        let temporary = directory.join(format!(
            ".{}.tmp",
            path.file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("review")
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
        {
            Ok(mut file) => {
                let write_result = file
                    .write_all(&encoded)
                    .and_then(|()| file.write_all(b"\n"))
                    .and_then(|()| file.sync_all());
                drop(file);
                if let Err(error) = write_result {
                    let _ = fs::remove_file(&temporary);
                    return Err(ReviewError::io("could not write review archive", error));
                }
                match fs::hard_link(&temporary, &path) {
                    Ok(()) => {
                        let _ = fs::remove_file(&temporary);
                        return Ok(path);
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                        let _ = fs::remove_file(&temporary);
                    }
                    Err(error) => {
                        let _ = fs::remove_file(&temporary);
                        return Err(ReviewError::io("could not publish review archive", error));
                    }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(ReviewError::io("could not create review archive", error)),
        }
    }
    Err(ReviewError::Message(
        "could not allocate a unique review archive filename".to_owned(),
    ))
}

pub fn review_archive_dir() -> Result<PathBuf> {
    if let Some(data_home) = env::var_os("XDG_DATA_HOME").filter(|value| !value.is_empty()) {
        return Ok(PathBuf::from(data_home).join("review/reviews"));
    }
    let home = env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            ReviewError::Message("HOME is not set; cannot locate review archive".into())
        })?;
    Ok(PathBuf::from(home).join(".local/share/review/reviews"))
}

pub fn list_archived_reviews(limit: usize) -> Result<Vec<ArchivedReview>> {
    let directory = review_archive_dir()?;
    let entries = match fs::read_dir(&directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(ReviewError::io("could not read review archives", error)),
    };
    let mut paths = entries
        .filter_map(std::result::Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "json")
        })
        .collect::<Vec<_>>();
    paths.sort_by(|left, right| right.file_name().cmp(&left.file_name()));
    let mut reviews = Vec::with_capacity(limit.min(paths.len()));
    for path in paths {
        if reviews.len() == limit {
            break;
        }
        let Ok(bytes) = fs::read(&path) else {
            continue;
        };
        let Ok(payload) = serde_json::from_slice::<ArchivePayload>(&bytes) else {
            continue;
        };
        reviews.push(ArchivedReview {
            archive_path: path,
            repository_path: payload.path,
            branch: payload.branch,
            review_message: payload.review_message,
        });
    }
    Ok(reviews)
}

fn archive_filename(now: SystemTime) -> String {
    let duration = now.duration_since(UNIX_EPOCH).unwrap_or_default();
    let count = FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!(
        "{}-{:09}-{}-{count}.json",
        utc_second_stamp(now),
        duration.subsec_nanos(),
        std::process::id()
    )
}

#[must_use]
pub fn timestamped_review_path(directory: &Path, now: SystemTime) -> PathBuf {
    let stamp = utc_minute_stamp(now);
    let initial = directory.join(format!("review-{stamp}.md"));
    if !initial.exists() {
        return initial;
    }
    for counter in 2.. {
        let candidate = directory.join(format!("review-{stamp}-{counter}.md"));
        if !candidate.exists() {
            return candidate;
        }
    }
    unreachable!()
}

pub fn save_review_file(
    markdown_message: &str,
    directory: &Path,
    now: SystemTime,
) -> Result<PathBuf> {
    let stamp = utc_minute_stamp(now);
    for counter in 1..=10_000 {
        let path = if counter == 1 {
            directory.join(format!("review-{stamp}.md"))
        } else {
            directory.join(format!("review-{stamp}-{counter}.md"))
        };
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut file) => {
                let result = file
                    .write_all(markdown_message.as_bytes())
                    .and_then(|()| file.sync_all());
                drop(file);
                if let Err(error) = result {
                    let _ = fs::remove_file(&path);
                    return Err(ReviewError::io("could not save review file", error));
                }
                return Ok(path);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(ReviewError::io("could not save review file", error)),
        }
    }
    Err(ReviewError::Message(
        "could not allocate a unique review filename".to_owned(),
    ))
}

fn utc_minute_stamp(now: SystemTime) -> String {
    let seconds = now.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let (year, month, day, hour, minute, _) = utc_components(seconds);
    format!("{year:04}{month:02}{day:02}-{hour:02}{minute:02}")
}

fn utc_second_stamp(now: SystemTime) -> String {
    let seconds = now.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let (year, month, day, hour, minute, second) = utc_components(seconds);
    format!("{year:04}{month:02}{day:02}-{hour:02}{minute:02}{second:02}")
}

fn utc_components(seconds: u64) -> (i64, i64, i64, u64, u64, u64) {
    let days = i64::try_from(seconds / 86_400).unwrap_or(i64::MAX);
    let seconds_in_day = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_in_day / 3_600;
    let minute = (seconds_in_day % 3_600) / 60;
    let second = seconds_in_day % 60;
    (year, month, day, hour, minute, second)
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let days = days_since_epoch + 719_468;
    let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
    let day_of_era = days - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_stamp_matches_epoch_and_known_date() {
        assert_eq!(utc_minute_stamp(UNIX_EPOCH), "19700101-0000");
        assert_eq!(utc_second_stamp(UNIX_EPOCH), "19700101-000000");
        assert_eq!(
            utc_minute_stamp(UNIX_EPOCH + std::time::Duration::from_secs(1_704_067_200)),
            "20240101-0000"
        );
    }

    #[test]
    fn archive_names_are_unique() {
        let now = UNIX_EPOCH + std::time::Duration::from_secs(10);
        assert_ne!(archive_filename(now), archive_filename(now));
        assert!(archive_filename(now).starts_with("19700101-000010-"));
    }

    #[test]
    fn archive_timestamp_labels_are_human_readable() {
        let review = ArchivedReview {
            archive_path: PathBuf::from("20260713-135927-000000001-1-0.json"),
            repository_path: String::new(),
            branch: String::new(),
            review_message: String::new(),
        };
        assert_eq!(review.timestamp_label(), "20260713-135927");
    }

    #[test]
    fn save_review_file_allocates_without_overwriting() {
        let directory = std::env::temp_dir().join(format!(
            "review-save-test-{}-{}",
            std::process::id(),
            FILE_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory).unwrap();
        let now = UNIX_EPOCH + std::time::Duration::from_secs(1_704_067_200);

        let first = save_review_file("first", &directory, now).unwrap();
        let second = save_review_file("second", &directory, now).unwrap();

        assert_eq!(first.file_name().unwrap(), "review-20240101-0000.md");
        assert_eq!(second.file_name().unwrap(), "review-20240101-0000-2.md");
        assert_eq!(fs::read_to_string(first).unwrap(), "first");
        assert_eq!(fs::read_to_string(second).unwrap(), "second");
        fs::remove_dir_all(directory).unwrap();
    }
}
