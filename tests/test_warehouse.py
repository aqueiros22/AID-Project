"""Persistent storage, constraints and failure recovery for the local warehouse."""
import contextlib
import csv
import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_warehouse import build_warehouse, open_warehouse, validate_warehouse
from scripts.genre_queries import release_count_by_genres, series_counts_by_genres
from scripts.refine_dataset import build
from scripts.paths import PROJECT_ROOT


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / 'data.tsv'
        self.refined = root / 'refined'
        self.database = root / 'warehouse.db'
        with source.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerow(['tconst', 'titleType', 'primaryTitle', 'originalTitle',
                             'isAdult', 'startYear', 'endYear', 'runtimeMinutes', 'genres'])
            writer.writerows([
                ['tt1', 'tvSeries', 'Hybrid series', 'Hybrid series', '0', '2018', '2020', '30', 'Comedy,Romance'],
                ['tt2', 'tvMiniSeries', 'Open series', 'Open series', '0', '2019', '\\N', '\\N', 'Comedy'],
                ['tt3', 'movie', 'Movie', 'Movie', '1', '2019', '\\N', '90', 'Drama'],
            ])
        with contextlib.redirect_stdout(io.StringIO()):
            self.report = build(source, self.refined, 2021, minimum_release_facts=1)
            build_warehouse(self.refined, self.database, minimum_release_facts=1)

    def connect(self, readonly=True):
        conn = open_warehouse(self.database, readonly=readonly)
        self.addCleanup(conn.close)
        return conn

    def test_persistence_and_genre_queries_after_csvs_are_removed(self):
        for path in self.refined.glob('*.csv'):
            path.unlink()
        conn = self.connect()
        self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
        self.assertEqual(release_count_by_genres(conn, ['Comedy', 'Romance']), 2)
        self.assertEqual(series_counts_by_genres(conn, ['Comedy'], 2019), (1, 1))
        self.assertEqual(series_counts_by_genres(conn, ['Comedy'], 2021), (0, 1))
        stored_report = json.loads(conn.execute('SELECT source_report_json FROM warehouse_metadata').fetchone()[0])
        validation = validate_warehouse(conn, stored_report, minimum_release_facts=1)
        self.assertEqual(validation['row_counts']['fact_title_release'], 3)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
            conn.execute('DELETE FROM fact_title_release')

    def test_release_primary_foreign_keys_not_null_checks_and_types(self):
        conn = self.connect(readonly=False)
        row = list(conn.execute("SELECT * FROM fact_title_release WHERE title_id = 'tt1'").fetchone())
        invalid = [row.copy()]
        # Year FK, NOT NULL title, measures, and series end consistency (status vs end year).
        for index, value in ((2, 9999), (1, None), (6, 0), (7, -1), (8, 0), (7, 1.5),
                             (9, 'unknown'), (9, 'ended'), (10, 2017), (10, None)):
            changed = row.copy()
            changed[0] = 'new-title'
            changed[index] = value
            invalid.append(changed)
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                conn.execute('INSERT INTO fact_title_release VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', values)
        movie = list(conn.execute("SELECT * FROM fact_title_release WHERE title_id = 'tt3'").fetchone())
        self.assertEqual(movie[9:], [None, None])
        movie[0], movie[9] = 'new-movie', 'unknown'
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'only for tvSeries'):
            conn.execute('INSERT INTO fact_title_release VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', movie)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'only for tvSeries'):
            conn.execute("UPDATE fact_title_release SET series_end_status = NULL, end_year = NULL WHERE title_id = 'tt1'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM dim_year WHERE year_key = 2018')
        conn.rollback()

    def test_catalog_snapshot_and_bridge_composite_keys_and_checks(self):
        conn = self.connect(readonly=False)
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'fact_series_year_snapshot'").fetchone())
        row = list(conn.execute('SELECT * FROM fact_catalog_year_snapshot LIMIT 1').fetchone())
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO fact_catalog_year_snapshot VALUES (?, ?, ?, ?, ?, ?)', row)
        for change in ('year_key = 9999', 'new_title_count = -1', 'cumulative_title_count = 0',
                       'new_title_count = cumulative_title_count + 1'):
            with self.subTest(change=change), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f'UPDATE fact_catalog_year_snapshot SET {change}')
        # Stock at cutoff equals the release population; flows sum to it as well.
        self.assertEqual(conn.execute('''
            SELECT SUM(new_title_count), SUM(CASE WHEN year_key = 2021 THEN cumulative_title_count END)
            FROM fact_catalog_year_snapshot''').fetchone(), (3, 3))
        pair = conn.execute('SELECT * FROM bridge_genre_set_genre LIMIT 1').fetchone()
        for values in (pair, (pair[0], 9999)):
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('INSERT INTO bridge_genre_set_genre VALUES (?, ?)', values)
        conn.rollback()

    def test_failed_rebuild_keeps_previous_database(self):
        previous = hashlib.sha256(self.database.read_bytes()).hexdigest()
        path = self.refined / 'fact_title_release.csv'
        with path.open(encoding='utf-8', newline='') as handle:
            records = list(csv.reader(handle))
        with path.open('a', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerow(records[1])
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(sqlite3.IntegrityError):
            build_warehouse(self.refined, self.database, minimum_release_facts=1)
        self.assertEqual(previous, hashlib.sha256(self.database.read_bytes()).hexdigest())
        self.assertEqual(list(self.database.parent.glob('warehouse.db.*.tmp*')), [])
        self.assertEqual(self.connect().execute('SELECT COUNT(*) FROM fact_title_release').fetchone()[0], 3)

    def test_inconsistent_controls_prevent_publication(self):
        previous = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.report['total_runtime_minutes'] += 1
        (self.refined / 'validation_report.json').write_text(json.dumps(self.report), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'Release measures'):
            build_warehouse(self.refined, self.database, minimum_release_facts=1)
        self.assertEqual(previous, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_inconsistent_series_or_catalog_controls_are_rejected(self):
        conn = self.connect()
        for key, message in (('unknown_end_series_years', 'Series end metadata'),
                             ('known_end_series_at_cutoff', 'Series end metadata'),
                             ('catalog_snapshot_facts', 'row counts')):
            report = dict(self.report, **{key: self.report[key] + 1})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                validate_warehouse(conn, report, minimum_release_facts=1)

    def test_missing_database_is_not_silently_created(self):
        missing = self.database.with_name('missing.db')
        with self.assertRaisesRegex(FileNotFoundError, 'build_warehouse'):
            open_warehouse(missing)
        self.assertFalse(missing.exists())

    def test_old_schema_and_snapshot_semantics_are_rejected(self):
        old_report = self.report.copy()
        old_report['snapshot_semantics_version'] = 2
        with self.assertRaisesRegex(ValueError, 'Outdated snapshot semantics'):
            validate_warehouse(self.connect(), old_report, minimum_release_facts=1)
        conn = sqlite3.connect(self.database)
        conn.execute('PRAGMA user_version = 1')
        conn.close()
        with self.assertRaisesRegex(ValueError, 'Unsupported warehouse schema'):
            open_warehouse(self.database)

    def test_cli_reports_indeterminate_status_without_airing_total(self):
        result = subprocess.run([
            sys.executable, '-m', 'scripts.run_queries', '--database', str(self.database),
            '--genre', 'Comedy', '--snapshot-year', '2021',
        ], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True)
        self.assertIn('Known series intervals covering 2021: 0', result.stdout)
        self.assertIn('Series started by 2021 with unknown end: 1 (airing status indeterminate)', result.stdout)
        self.assertNotIn('Series considered airing', result.stdout)
        self.assertNotIn('assumed airing', result.stdout)


if __name__ == '__main__':
    unittest.main()
