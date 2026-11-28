-- SQLite 3.37+; strict physical types. Dates are ISO-8601 TEXT.
PRAGMA foreign_keys = ON;
PRAGMA user_version = 5;

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
    -- Series end metadata. NULL status for non-series; end_year only for a valid known end.
    -- end_year is a plain attribute, not an FK: known ends may lie after the cutoff year.
    series_end_status TEXT CHECK (series_end_status IN ('known', 'unknown', 'invalid')),
    end_year INTEGER CHECK (end_year BETWEEN 1 AND 9999),
    CHECK ((runtime_known_count = 0 AND runtime_minutes = 0)
        OR (runtime_known_count = 1 AND runtime_minutes > 0)),
    CHECK ((series_end_status = 'known') = (end_year IS NOT NULL)),
    CHECK (end_year IS NULL OR end_year >= year_key)
) STRICT;

CREATE TRIGGER release_series_end_insert
BEFORE INSERT ON fact_title_release
WHEN (NEW.series_end_status IS NOT NULL) != COALESCE((
        SELECT title_type IN ('tvSeries', 'tvMiniSeries')
        FROM dim_title_type WHERE title_type_key = NEW.title_type_key), 0)
BEGIN
    SELECT RAISE(ABORT, 'Series end status is set only for tvSeries and tvMiniSeries');
END;

CREATE TRIGGER release_series_end_update
BEFORE UPDATE OF title_type_key, series_end_status ON fact_title_release
WHEN (NEW.series_end_status IS NOT NULL) != COALESCE((
        SELECT title_type IN ('tvSeries', 'tvMiniSeries')
        FROM dim_title_type WHERE title_type_key = NEW.title_type_key), 0)
BEGIN
    SELECT RAISE(ABORT, 'Series end status is set only for tvSeries and tvMiniSeries');
END;

-- Periodic snapshot of the catalogue at each reference year-end (all title types).
-- new_title_count is a flow (additive); cumulative_title_count is a stock: additive
-- across disjoint categories within one year, never across years.
CREATE TABLE fact_catalog_year_snapshot (
    year_key INTEGER NOT NULL REFERENCES dim_year (year_key),
    title_type_key INTEGER NOT NULL REFERENCES dim_title_type (title_type_key),
    genre_set_key INTEGER NOT NULL REFERENCES dim_genre_set (genre_set_key),
    audience_key INTEGER NOT NULL REFERENCES dim_audience (audience_key),
    new_title_count INTEGER NOT NULL CHECK (new_title_count >= 0),
    cumulative_title_count INTEGER NOT NULL CHECK (cumulative_title_count > 0),
    PRIMARY KEY (year_key, title_type_key, genre_set_key, audience_key),
    CHECK (cumulative_title_count >= new_title_count)
) STRICT, WITHOUT ROWID;

-- Operational metadata, not an additional dimensional fact table.
CREATE TABLE warehouse_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    built_at_utc TEXT NOT NULL,
    source_directory TEXT NOT NULL,
    source_report_json TEXT NOT NULL,
    validation_json TEXT NOT NULL
) STRICT;

-- Current ratings only: download date and source hash are in warehouse_metadata.
CREATE TABLE fact_title_rating_snapshot (
    title_id TEXT PRIMARY KEY NOT NULL CHECK (length(title_id) > 0),
    year_key INTEGER NOT NULL REFERENCES dim_year,
    title_type_key INTEGER NOT NULL REFERENCES dim_title_type,
    genre_set_key INTEGER NOT NULL REFERENCES dim_genre_set,
    audience_key INTEGER NOT NULL REFERENCES dim_audience,
    average_rating REAL NOT NULL CHECK (average_rating BETWEEN 1 AND 10),
    num_votes INTEGER NOT NULL CHECK (num_votes > 0),
    rated_title_count INTEGER NOT NULL CHECK (rated_title_count = 1)
) STRICT, WITHOUT ROWID;
