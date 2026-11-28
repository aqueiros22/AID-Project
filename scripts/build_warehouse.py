"""Build or verify a persistent local SQLite dimensional warehouse."""
import argparse
import csv
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from scripts.paths import REFINED, SCHEMA, WAREHOUSE

TABLES = (
    'dim_year', 'dim_title_type', 'dim_genre_set', 'dim_audience', 'dim_genre',
    'bridge_genre_set_genre', 'fact_title_release', 'fact_catalog_year_snapshot',
    'fact_title_rating_snapshot',
)
INDEXES = (
    'CREATE INDEX release_type_year ON fact_title_release (title_type_key, year_key)',
    'CREATE INDEX release_genre_set ON fact_title_release (genre_set_key)',
    'CREATE INDEX release_series_end ON fact_title_release (series_end_status, year_key, end_year) '
    'WHERE series_end_status IS NOT NULL',
    'CREATE INDEX catalog_genre_set ON fact_catalog_year_snapshot (genre_set_key)',
    'CREATE INDEX genre_membership_reverse ON bridge_genre_set_genre (genre_key, genre_set_key)',
)


def open_warehouse(database, *, readonly=True):
    """Open an existing database; never silently create an empty query database."""
    database = Path(database).resolve()
    if not database.is_file():
        raise FileNotFoundError(f'{database} does not exist. Run python -m scripts.build_warehouse first.')
    mode = 'ro' if readonly else 'rw'
    conn = sqlite3.connect(database.as_uri() + f'?mode={mode}', uri=True)
    conn.execute('PRAGMA foreign_keys = ON')
    if conn.execute('PRAGMA user_version').fetchone()[0] != 5:
        conn.close()
        raise ValueError('Unsupported warehouse schema. Rebuild with python -m scripts.build_warehouse.')
    return conn


def load_table(conn, source, table):
    columns = conn.execute(f'PRAGMA table_info({table})').fetchall()
    expected = [col[1] for col in columns]
    with (source / f'{table}.csv').open(encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle)
        if next(reader, None) != expected:
            raise ValueError(f'{table}: CSV headers do not match the schema')

        def records():
            for line, values in enumerate(reader, 2):
                if len(values) != len(columns):
                    raise ValueError(f'{table}: invalid field count at CSV line {line}')
                try:
                    # An empty field is NULL in nullable (non-key) columns.
                    result = tuple(
                        None if value == '' and not col[3] and not col[5]
                        else int(value) if col[2] == 'INTEGER' else value
                        for col, value in zip(columns, values))
                except ValueError as error:
                    raise ValueError(f'{table}: invalid integer at CSV line {line}') from error
                if line % 1_000_000 == 0:
                    print(f'  {table}: {line - 1:,} rows read', flush=True)
                yield result

        placeholders = ', '.join('?' for _ in columns)
        conn.executemany(f'INSERT INTO {table} VALUES ({placeholders})', records())


def validate_warehouse(conn, source_report, *, minimum_release_facts=10001):
    """Reconcile stored rows and measures with the ETL's independent controls."""
    if source_report.get('snapshot_semantics_version') != 3:
        raise ValueError('Outdated snapshot semantics: regenerate CSVs with python -m scripts.refine_dataset')
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Foreign key enforcement is disabled')
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Foreign key validation failed')
    integrity = conn.execute('PRAGMA integrity_check').fetchall()
    if integrity != [('ok',)]:
        raise ValueError(f'SQLite integrity check failed: {integrity[:5]}')
    counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
              for table in TABLES}
    expected = {
        'fact_title_rating_snapshot': source_report['rating_facts'],
        'dim_year': source_report['year_members'],
        'bridge_genre_set_genre': source_report['genre_bridge_rows'],
        'fact_title_release': source_report['release_facts'],
        'fact_catalog_year_snapshot': source_report['catalog_snapshot_facts'],
        **{f'dim_{name}': count for name, count in source_report['dimension_members'].items()},
    }
    if counts != expected:
        raise ValueError(f'Warehouse row counts differ from ETL report: {counts} != {expected}')
    if counts['fact_title_release'] < minimum_release_facts:
        raise ValueError(f'Requirement: at least {minimum_release_facts:,} release facts')
    release_totals = conn.execute('''
        SELECT COALESCE(SUM(title_count), 0), COALESCE(SUM(runtime_minutes), 0),
               COALESCE(SUM(runtime_known_count), 0) FROM fact_title_release
    ''').fetchone()
    if release_totals != (source_report['release_facts'], source_report['total_runtime_minutes'],
                          source_report['known_runtime_facts']):
        raise ValueError('Release measures differ from ETL report')
    rating_votes = conn.execute('SELECT COALESCE(SUM(num_votes), 0) FROM fact_title_rating_snapshot').fetchone()[0]
    if rating_votes != source_report['rating_votes_total']:
        raise ValueError('Rating vote totals differ from ETL report')
    if conn.execute("""
        SELECT 1 FROM fact_title_rating_snapshot r
        LEFT JOIN fact_title_release f ON f.title_id = r.title_id
        WHERE f.title_id IS NULL OR r.year_key != f.year_key
           OR r.title_type_key != f.title_type_key OR r.genre_set_key != f.genre_set_key
           OR r.audience_key != f.audience_key LIMIT 1
    """).fetchone():
        raise ValueError('Rating dimensions differ from eligible release')
    cutoff = source_report['cutoff_year']
    # Series end metadata: the same reporting-year controls the old series snapshot
    # carried, recomputed from the release fact that the series queries now use.
    series_controls = conn.execute('''
        SELECT COALESCE(SUM(series_end_status = 'known'), 0),
               COALESCE(SUM(series_end_status = 'unknown'), 0),
               COALESCE(SUM(series_end_status = 'invalid'), 0),
               COALESCE(SUM(CASE WHEN series_end_status = 'known'
                                 THEN MIN(end_year, :cutoff) - year_key + 1 END), 0),
               COALESCE(SUM(CASE WHEN series_end_status = 'unknown'
                                 THEN :cutoff - year_key + 1 END), 0),
               COALESCE(SUM(series_end_status = 'known' AND end_year >= :cutoff), 0),
               COALESCE(SUM(series_end_status = 'unknown'), 0)
        FROM fact_title_release WHERE series_end_status IS NOT NULL
    ''', {'cutoff': cutoff}).fetchone()
    if series_controls != tuple(source_report[name] for name in (
            'series_with_known_end', 'series_with_unknown_end', 'series_excluded_invalid_end_year',
            'known_end_series_years', 'unknown_end_series_years',
            'known_end_series_at_cutoff', 'unknown_end_series_at_cutoff')):
        raise ValueError('Series end metadata differs from ETL report')
    if conn.execute('''
        SELECT 1 FROM fact_title_release f
        JOIN dim_title_type t USING (title_type_key)
        WHERE (f.series_end_status IS NOT NULL) != (t.title_type IN ('tvSeries', 'tvMiniSeries')) LIMIT 1
    ''').fetchone():
        raise ValueError('Series end status on a non-series type, or missing on a series')
    # Catalogue snapshot: flows equal releases per year and category, and each
    # stock equals the previous year's stock plus that year's flow.
    catalog_totals = conn.execute('''
        SELECT COALESCE(SUM(new_title_count), 0),
               COALESCE(SUM(CASE WHEN year_key = ? THEN cumulative_title_count END), 0)
        FROM fact_catalog_year_snapshot
    ''', (cutoff,)).fetchone()
    if catalog_totals != (source_report['release_facts'], source_report['release_facts']):
        raise ValueError('Catalogue snapshot totals differ from release facts')
    if conn.execute('''
        WITH rel AS (
            SELECT year_key, title_type_key, genre_set_key, audience_key, SUM(title_count) AS n
            FROM fact_title_release GROUP BY 1, 2, 3, 4
        ), snap AS (
            SELECT year_key, title_type_key, genre_set_key, audience_key, new_title_count AS n
            FROM fact_catalog_year_snapshot WHERE new_title_count > 0
        )
        SELECT 1 FROM (SELECT * FROM rel EXCEPT SELECT * FROM snap)
        UNION ALL
        SELECT 1 FROM (SELECT * FROM snap EXCEPT SELECT * FROM rel)
        LIMIT 1
    ''').fetchone():
        raise ValueError('Catalogue snapshot flows differ from release facts')
    if conn.execute('''
        SELECT 1 FROM (
            SELECT year_key, cumulative_title_count, new_title_count,
                   LAG(year_key) OVER w AS previous_year,
                   LAG(cumulative_title_count) OVER w AS previous_stock
            FROM fact_catalog_year_snapshot
            WINDOW w AS (PARTITION BY title_type_key, genre_set_key, audience_key ORDER BY year_key)
        )
        WHERE cumulative_title_count != COALESCE(previous_stock, 0) + new_title_count
           OR (previous_year IS NOT NULL AND previous_year != year_key - 1)
           OR (previous_year IS NULL AND new_title_count != cumulative_title_count)
        LIMIT 1
    ''').fetchone():
        raise ValueError('Catalogue snapshot stock is not the running sum of its flows')
    if conn.execute('''
        SELECT 1 FROM (
            SELECT MAX(year_key) AS last_year FROM fact_catalog_year_snapshot
            GROUP BY title_type_key, genre_set_key, audience_key
        ) WHERE last_year != ? LIMIT 1
    ''', (cutoff,)).fetchone():
        raise ValueError('Catalogue snapshot category does not continue through cutoff')
    genre_sets = dict(conn.execute('SELECT genre_set_key, genre_set FROM dim_genre_set'))
    expected_memberships = {(key, genre) for key, label in genre_sets.items() for genre in label.split(',')}
    actual_memberships = set(conn.execute('''
        SELECT b.genre_set_key, g.genre FROM bridge_genre_set_genre b
        JOIN dim_genre g USING (genre_key)
    '''))
    if actual_memberships != expected_memberships:
        raise ValueError('Genre bridge does not match genre combinations')
    first, last = conn.execute('SELECT MIN(year), MAX(year) FROM dim_year').fetchone()
    if (first, last) != (source_report['first_year'], source_report['cutoff_year']):
        raise ValueError('Year range differs from ETL report')
    return {
        'row_counts': counts,
        'foreign_keys': 'ok', 'integrity': 'ok', 'control_totals': 'ok',
        'genre_memberships': 'ok', 'series_end_metadata': 'ok', 'catalog_snapshot': 'ok',
        'cutoff_year': source_report['cutoff_year'],
        'snapshot_semantics_version': 3,
    }


def build_warehouse(source, database, *, minimum_release_facts=10001):
    source, database = Path(source).resolve(), Path(database).resolve()
    report = json.loads((source / 'validation_report.json').read_text(encoding='utf-8'))
    database.parent.mkdir(parents=True, exist_ok=True)
    # Build next to the destination for an atomic same-filesystem replacement.
    # A failed load leaves the last successful warehouse intact.
    fd, temporary = tempfile.mkstemp(prefix=database.name + '.', suffix='.tmp', dir=database.parent)
    os.close(fd)
    temporary = Path(temporary)
    conn = None
    try:
        conn = sqlite3.connect(temporary)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))
        with conn:
            for table in TABLES:
                print(f'Loading {table}...', flush=True)
                load_table(conn, source, table)
            for index in INDEXES:
                print(f'Building index {index.split()[2]}...', flush=True)
                conn.execute(index)
            print('Checking integrity and reconciling control totals...', flush=True)
            validation = validate_warehouse(conn, report, minimum_release_facts=minimum_release_facts)
            conn.execute('INSERT INTO warehouse_metadata VALUES (1, ?, ?, ?, ?)', (
                datetime.now(timezone.utc).isoformat(), str(source),
                json.dumps(report), json.dumps(validation),
            ))
        conn.execute('ANALYZE')
        conn.commit()
        conn.close()
        conn = None
        os.replace(temporary, database)
        print(f'Warehouse saved: {database}', flush=True)
        return validation
    finally:
        if conn is not None:
            conn.close()
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=REFINED)
    parser.add_argument('--database', type=Path, default=WAREHOUSE)
    parser.add_argument('--verify', action='store_true', help='Verify existing database without rebuilding or reading CSVs')
    args = parser.parse_args()
    if args.verify:
        connection = open_warehouse(args.database)
        try:
            stored_report = json.loads(connection.execute('SELECT source_report_json FROM warehouse_metadata WHERE singleton = 1').fetchone()[0])
            result = validate_warehouse(connection, stored_report)
        finally:
            connection.close()
    else:
        result = build_warehouse(args.source, args.database)
    print(json.dumps(result, indent=2))
