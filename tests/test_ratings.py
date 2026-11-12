import contextlib
import csv
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.refine_dataset import build
from scripts.build_warehouse import build_warehouse, open_warehouse, validate_warehouse
from scripts.rating_queries import RATING_QUERIES


class RatingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'titles.tsv'
        self.source.write_text(
            'tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres\n'
            'tt1\tmovie\tOne\tOne\t0\t2020\t\\N\t90\tComedy,Drama\n'
            'tt2\tmovie\tTwo\tTwo\t0\t2021\t\\N\t95\tDrama\n'
            'tt3\tmovie\tThree\tThree\t0\t\\N\t\\N\t95\tDrama\n', encoding='utf-8')
        self.ratings = self.root / 'ratings.tsv'
        self.ratings.write_text('tconst\taverageRating\tnumVotes\ntt1\t8.0\t10000\ntt3\t6.0\t10\ntt99\t9.0\t5\n')
        self.output = self.root / 'refined'

    def run_build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return build(self.source, self.output, 2026, minimum_release_facts=1,
                         ratings_source=self.ratings)

    def test_join_coverage_and_nonduplicating_genres(self):
        report = self.run_build()
        self.assertEqual(report['rating_facts'], 1)
        self.assertEqual(report['ratings_without_eligible_title'], 2)
        self.assertEqual(report['ratings_provenance']['download_date'], '2026-11-12')
        database = self.root / 'warehouse.db'
        with contextlib.redirect_stdout(io.StringIO()):
            build_warehouse(self.output, database, minimum_release_facts=1)
        conn = open_warehouse(database, readonly=False)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'dim_rating_extract'").fetchone())
        columns = conn.execute('PRAGMA table_info(fact_title_rating_snapshot)').fetchall()
        self.assertEqual([c[1] for c in columns if c[5]], ['title_id'])
        self.assertNotIn('rating_extract_key', [c[1] for c in columns])
        stored = json.loads(conn.execute('SELECT source_report_json FROM warehouse_metadata').fetchone()[0])
        self.assertEqual(stored['ratings_provenance'], report['ratings_provenance'])
        self.assertEqual(len(stored['ratings_provenance']['sha256']), 64)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO fact_title_rating_snapshot SELECT * FROM fact_title_rating_snapshot')
        rows = conn.execute(list(RATING_QUERIES.values())[0]).fetchall()
        self.assertEqual(rows, [('movie', 2, 1, 50.0, 8.0, 10000)])
        genres = conn.execute(list(RATING_QUERIES.values())[2]).fetchall()
        self.assertEqual({r[0]: r[3] for r in genres}, {'Drama': 50.0, 'Comedy': 100.0})
        for sql in RATING_QUERIES.values():
            conn.execute(sql).fetchall()
        validate_warehouse(conn, report, minimum_release_facts=1)
        for sql in (
            'UPDATE fact_title_rating_snapshot SET average_rating = 11',
            'UPDATE fact_title_rating_snapshot SET num_votes = 0',
            'UPDATE fact_title_rating_snapshot SET year_key = 9999',
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)
        conn.execute('UPDATE fact_title_rating_snapshot SET year_key = 2021')
        with self.assertRaisesRegex(ValueError, 'dimensions differ'):
            validate_warehouse(conn, report, minimum_release_facts=1)
        conn.rollback()

    def test_invalid_ratings_preserve_published_outputs(self):
        self.run_build()
        previous = {p.name: p.read_bytes() for p in self.output.iterdir()}
        for value in ('11.0\t10', 'nan\t10', '8.0\t0'):
            self.ratings.write_text('tconst\taverageRating\tnumVotes\ntt1\t' + value + '\n')
            with self.assertRaises(ValueError):
                self.run_build()
            self.assertEqual(previous, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_duplicate_rating_rejected(self):
        with self.ratings.open('a') as f:
            f.write('tt1\t9.0\t20\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.run_build()

    def test_refresh_replaces_ratings_and_retires_extract_dimension(self):
        first = self.run_build()
        legacy = self.output / 'dim_rating_extract.csv'
        legacy.write_text('rating_extract_key,download_date,source_sha256\n1,2026-11-12,old\n')
        self.ratings.write_text('tconst\taverageRating\tnumVotes\ntt2\t7.5\t20\n')
        second = self.run_build()
        self.assertFalse(legacy.exists())
        self.assertNotIn('rating_extract_members', second)
        self.assertNotEqual(first['ratings_provenance']['sha256'], second['ratings_provenance']['sha256'])
        with (self.output / 'fact_title_rating_snapshot.csv').open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r['title_id'] for r in rows], ['tt2'])
        self.assertNotIn('rating_extract_key', rows[0])
        database = self.root / 'warehouse.db'
        with contextlib.redirect_stdout(io.StringIO()):
            build_warehouse(self.output, database, minimum_release_facts=1)
        conn = open_warehouse(database)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('SELECT title_id, average_rating, num_votes FROM fact_title_rating_snapshot').fetchall(),
                         [('tt2', 7.5, 20)])

    def test_failed_legacy_retirement_rolls_back_publication(self):
        self.run_build()
        legacy = self.output / 'dim_rating_extract.csv'
        legacy.write_text('old dimension\n')
        previous = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.ratings.write_text('tconst\taverageRating\tnumVotes\ntt2\t7.5\t20\n')
        replace = os.replace

        def fail_retirement(source, destination):
            if Path(source) == legacy:
                raise OSError('Simulated retirement failure')
            return replace(source, destination)

        with patch('scripts.refine_dataset.os.replace', side_effect=fail_retirement):
            with self.assertRaisesRegex(OSError, 'retirement failure'):
                self.run_build()
        self.assertEqual(previous, {p.name: p.read_bytes() for p in self.output.iterdir()})
