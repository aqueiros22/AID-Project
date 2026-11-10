-- SQLite 3.37+; strict physical types. Dates are ISO-8601 TEXT.
PRAGMA foreign_keys = ON;
PRAGMA user_version = 2;

CREATE TABLE dim_year (
    year_key INTEGER PRIMARY KEY CHECK (year_key BETWEEN 1 AND 9999),
    year INTEGER NOT NULL UNIQUE CHECK (year = year_key),
    decade INTEGER NOT NULL CHECK (decade = (year / 10) * 10),
    century INTEGER NOT NULL CHECK (century = (year - 1) / 100 + 1),
    snapshot_date TEXT NOT NULL CHECK (snapshot_date = printf('%04d-12-31', year))
) STRICT;

CREATE TABLE dim_title_type (
    title_type_key INTEGER PRIMARY KEY CHECK (title_type_key > 0),
    title_type TEXT NOT NULL UNIQUE CHECK (length(title_type) > 0)
) STRICT;

CREATE TABLE dim_genre_set (
    genre_set_key INTEGER PRIMARY KEY CHECK (genre_set_key > 0),
    genre_set TEXT NOT NULL UNIQUE CHECK (length(genre_set) > 0)
) STRICT;

CREATE TABLE dim_audience (
    audience_key INTEGER PRIMARY KEY CHECK (audience_key > 0),
    audience TEXT NOT NULL UNIQUE
        CHECK (audience IN ('Not adult-flagged', 'Adult-flagged', 'Unknown'))
) STRICT;

CREATE TABLE dim_genre (
    genre_key INTEGER PRIMARY KEY CHECK (genre_key > 0),
    genre TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(genre) > 0)
) STRICT;

CREATE TABLE bridge_genre_set_genre (
    genre_set_key INTEGER NOT NULL REFERENCES dim_genre_set (genre_set_key),
    genre_key INTEGER NOT NULL REFERENCES dim_genre (genre_key),
    PRIMARY KEY (genre_set_key, genre_key)
) STRICT, WITHOUT ROWID;

CREATE TABLE fact_title_release (
    title_id TEXT PRIMARY KEY NOT NULL CHECK (length(title_id) > 0),
    primary_title TEXT NOT NULL,
    year_key INTEGER NOT NULL REFERENCES dim_year (year_key),
    title_type_key INTEGER NOT NULL REFERENCES dim_title_type (title_type_key),
    genre_set_key INTEGER NOT NULL REFERENCES dim_genre_set (genre_set_key),
    audience_key INTEGER NOT NULL REFERENCES dim_audience (audience_key),
    title_count INTEGER NOT NULL CHECK (title_count = 1),
    runtime_minutes INTEGER NOT NULL CHECK (runtime_minutes >= 0),
    runtime_known_count INTEGER NOT NULL CHECK (runtime_known_count IN (0, 1)),
    CHECK ((runtime_known_count = 0 AND runtime_minutes = 0)
        OR (runtime_known_count = 1 AND runtime_minutes > 0))
) STRICT;

CREATE TABLE fact_series_year_snapshot (
    year_key INTEGER NOT NULL REFERENCES dim_year (year_key),
    title_type_key INTEGER NOT NULL REFERENCES dim_title_type (title_type_key),
    genre_set_key INTEGER NOT NULL REFERENCES dim_genre_set (genre_set_key),
    audience_key INTEGER NOT NULL REFERENCES dim_audience (audience_key),
    -- Known interval covers the year; does not prove episodes aired that year.
    known_interval_series_count INTEGER NOT NULL CHECK (known_interval_series_count >= 0),
    -- Started by the year with missing end; actual airing status is indeterminate.
    started_series_unknown_end_count INTEGER NOT NULL CHECK (started_series_unknown_end_count >= 0),
    PRIMARY KEY (year_key, title_type_key, genre_set_key, audience_key),
    CHECK (known_interval_series_count + started_series_unknown_end_count > 0)
) STRICT, WITHOUT ROWID;

CREATE TRIGGER snapshot_series_type_insert
BEFORE INSERT ON fact_series_year_snapshot
WHEN (SELECT title_type FROM dim_title_type WHERE title_type_key = NEW.title_type_key)
     NOT IN ('tvSeries', 'tvMiniSeries')
BEGIN
    SELECT RAISE(ABORT, 'Snapshot supports only tvSeries and tvMiniSeries');
END;

CREATE TRIGGER snapshot_series_type_update
BEFORE UPDATE OF title_type_key ON fact_series_year_snapshot
WHEN (SELECT title_type FROM dim_title_type WHERE title_type_key = NEW.title_type_key)
     NOT IN ('tvSeries', 'tvMiniSeries')
BEGIN
    SELECT RAISE(ABORT, 'Snapshot supports only tvSeries and tvMiniSeries');
END;

-- Operational metadata, not an additional dimensional fact table.
CREATE TABLE warehouse_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    built_at_utc TEXT NOT NULL,
    source_directory TEXT NOT NULL,
    source_report_json TEXT NOT NULL,
    validation_json TEXT NOT NULL
) STRICT;
