"""Boundary and reconciliation checks for annual series snapshots."""
import contextlib
import csv
import io
import hashlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.refine_dataset import build
from scripts.genre_queries import find_titles_by_genres, release_count_by_genres, series_counts_by_genres


class SeriesSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'data.tsv'
        self.output = self.root / 'refined'
        # Includes a known interval, a one-year miniseries, two missing ends,
        # an end beyond cutoff, invalid ends, non-series and ineligible starts.
        self.titles = [
            ('tt1', 'tvSeries', '2018', '2020', 'Drama'),
            ('tt2', 'tvMiniSeries', '2019', '2019', 'Comedy'),
            ('tt3', 'tvSeries', '2019', '\\N', 'Drama'),
            ('tt4', 'tvMiniSeries', '2020', '', 'Comedy'),
            ('tt5', 'tvSeries', '2020', '2028', 'Drama'),
            ('tt6', 'tvSeries', '2019', '2018', 'Drama'),
            ('tt7', 'tvSeries', '2019', 'invalid', 'Drama'),
            ('tt8', 'tvSeries', '2019', '0', 'Drama'),
            ('tt9', 'movie', '2017', '\\N', 'Drama'),
            ('tt10', 'tvEpisode', '2019', '\\N', 'Drama'),
            ('tt11', 'tvSeries', '\\N', '\\N', 'Drama'),
            ('tt12', 'tvSeries', '2022', '\\N', 'Drama'),
            ('tt13', 'tvMiniSeries', '2018', '2018', 'Comedy'),
        ]
        with self.source.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerow(['tconst', 'titleType', 'primaryTitle', 'originalTitle',
                             'isAdult', 'startYear', 'endYear', 'runtimeMinutes', 'genres'])
            for title_id, title_type, start, end, genres in self.titles:
                writer.writerow([title_id, title_type, title_id, title_id, '0', start, end, '30', genres])

    def run_build(self, cutoff=2021, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return build(self.source, self.output, cutoff, **kwargs)

    def rows(self, table):
        with (self.output / f'{table}.csv').open(encoding='utf-8', newline='') as handle:
            return list(csv.DictReader(handle))

    def test_snapshot_matches_direct_interval_membership(self):
        self.output.mkdir()
        legacy = self.output / 'fact_year_end_catalog.csv'
        legacy.write_text('obsolete generated fact\n', encoding='utf-8')
        report = self.run_build(minimum_release_facts=1)
        self.assertFalse(legacy.exists())
        releases = self.rows('fact_title_release')
        self.assertEqual(len(releases), 11)
        self.assertEqual(report['series_excluded_invalid_end_year'], 3)
        self.assertEqual(report['series_with_known_end'], 4)
        self.assertEqual(report['series_with_unknown_end'], 2)
        self.assertEqual(report['known_end_series_at_cutoff'], 1)
        self.assertEqual(report['unknown_end_series_at_cutoff'], 2)

        dimensions = {}
        for name in ('title_type', 'genre_set', 'audience', 'year'):
            dimensions[name] = {row[f'{name}_key']: row[name] for row in self.rows(f'dim_{name}')}
        actual = {}
        for row in self.rows('fact_series_year_snapshot'):
            for name in dimensions:
                self.assertIn(row[f'{name}_key'], dimensions[name])
            key = (int(row['year_key']), dimensions['title_type'][row['title_type_key']],
                   dimensions['genre_set'][row['genre_set_key']])
            self.assertNotIn(key, actual)
            counts = (int(row['known_interval_series_count']), int(row['started_series_unknown_end_count']))
            self.assertGreater(sum(counts), 0)
            actual[key] = counts

        # Direct enumeration: missing ends remain None, not invented cutoff ends.
        intervals = [
            ('tvSeries', 'Drama', 2018, 2020, False),
            ('tvMiniSeries', 'Comedy', 2019, 2019, False),
            ('tvSeries', 'Drama', 2019, None, True),
            ('tvMiniSeries', 'Comedy', 2020, None, True),
            ('tvSeries', 'Drama', 2020, 2028, False),
            ('tvMiniSeries', 'Comedy', 2018, 2018, False),
        ]
        expected = {}
        for year in range(2017, 2022):
            for title_type, genre in {('tvSeries', 'Drama'), ('tvMiniSeries', 'Comedy')}:
                known = sum(t == title_type and g == genre and end is not None and start <= year <= end
                            for t, g, start, end, unknown in intervals)
                unknown = sum(t == title_type and g == genre and start <= year and unknown
                              for t, g, start, end, unknown in intervals)
                if known + unknown:
                    expected[(year, title_type, genre)] = (known, unknown)
        self.assertEqual(actual, expected)
        self.assertEqual(report['snapshot_semantics_version'], 2)
        self.assertIn('status indeterminate', report['snapshot_rule'])
        self.assertIn('do not infer continued airing', report['missing_end_policy'])

    def test_no_series_produces_empty_snapshot(self):
        report = self.run_build(cutoff=2017, minimum_release_facts=1)
        self.assertEqual(report['release_facts'], 1)
        self.assertEqual(report['snapshot_facts'], 0)
        self.assertEqual(self.rows('fact_series_year_snapshot'), [])

    def test_default_fact_threshold_is_enforced(self):
        with self.assertRaisesRegex(ValueError, '10,001'):
            self.run_build()

    def test_no_eligible_titles(self):
        with self.assertRaisesRegex(ValueError, 'No eligible titles'):
            self.run_build(cutoff=2016, minimum_release_facts=1)

    def test_invalid_cutoff(self):
        for cutoff in (0, 10000):
            with self.subTest(cutoff=cutoff), self.assertRaisesRegex(ValueError, 'Cutoff year'):
                self.run_build(cutoff=cutoff)

    def test_quality_counters_and_source_fingerprint(self):
        with self.source.open('a', encoding='utf-8', newline='') as handle:
            csv.writer(handle, delimiter='\t').writerows([
                ['tt14', 'movie', 'Missing', 'Missing', '?', '2019', '\\N', '', ''],
                ['tt15', 'movie', 'Invalid', 'Invalid', '0', '2019', '\\N', '-5', ' Comedy,Comedy , '],
                ['tt16', 'movie', 'Bad year', 'Bad year', '0', 'invalid', '\\N', '90', 'Drama'],
            ])
        report = self.run_build(minimum_release_facts=1)
        self.assertEqual(report['excluded_missing_start_year'], 1)
        self.assertEqual(report['excluded_invalid_start_year'], 1)
        self.assertEqual(report['excluded_outside_cutoff_year'], 1)
        self.assertEqual(report['missing_runtime_facts'], 1)
        self.assertEqual(report['invalid_runtime_facts'], 1)
        self.assertEqual(report['unknown_genre_facts'], 1)
        self.assertEqual(report['unknown_audience_facts'], 1)
        self.assertEqual(report['source_provenance']['sha256'],
                         hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertIn('Comedy', {row['genre_set'] for row in self.rows('dim_genre_set')})

    def test_failed_transformation_preserves_previous_outputs(self):
        self.run_build(minimum_release_facts=1)
        previous = {path.name: path.read_bytes() for path in self.output.iterdir()}
        with self.assertRaises(ValueError):
            self.run_build()
        self.assertEqual(previous, {path.name: path.read_bytes() for path in self.output.iterdir()})

    def test_malformed_source_is_rejected_before_publication(self):
        self.source.write_text('tconst\tstartYear\ntt1\t2020\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'required columns'):
            self.run_build(minimum_release_facts=1)
        self.assertFalse(self.output.exists())

    def test_publication_error_restores_previous_outputs(self):
        self.run_build(minimum_release_facts=1)
        (self.output / 'unrelated.txt').write_text('Keep me', encoding='utf-8')
        previous = {path.name: path.read_bytes() for path in self.output.iterdir()}
        replace = os.replace

        def fail_publication(source, destination):
            if Path(source).parent.name == 'next' and Path(source).name == 'dim_audience.csv':
                raise OSError('Simulated publication failure')
            return replace(source, destination)

        with patch('scripts.refine_dataset.os.replace', side_effect=fail_publication):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                self.run_build(minimum_release_facts=1)
        self.assertEqual(previous, {path.name: path.read_bytes() for path in self.output.iterdir()})

    def test_individual_genres_preserve_combinations_and_fact_counts(self):
        with self.source.open('a', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerows([
                ['tt14', 'tvSeries', 'Hybrid', 'Hybrid', '0', '2018', '2020', '30', 'Romance,Comedy,Comedy'],
                ['tt15', 'movie', 'Comedy only', 'Comedy only', '0', '2019', '\\N', '90', 'Comedy'],
                ['tt16', 'movie', 'No genre', 'No genre', '0', '2019', '\\N', '90', '\\N'],
            ])
        report = self.run_build(minimum_release_facts=1)
        sets = {r['genre_set_key']: r['genre_set'] for r in self.rows('dim_genre_set')}
        genres = {r['genre_key']: r['genre'] for r in self.rows('dim_genre')}
        memberships = self.rows('bridge_genre_set_genre')
        pairs = {(r['genre_set_key'], r['genre_key']) for r in memberships}
        self.assertEqual(len(memberships), len(pairs))
        self.assertEqual(len(memberships), report['genre_bridge_rows'])
        for set_key, genre_key in pairs:
            self.assertIn(set_key, sets)
            self.assertIn(genre_key, genres)
        hybrid_key = next(key for key, value in sets.items() if value == 'Comedy,Romance')
        self.assertEqual({genres[g] for s, g in pairs if s == hybrid_key}, {'Comedy', 'Romance'})
        self.assertIn('Unknown', genres.values())

        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        for path in self.output.glob('*.csv'):
            with path.open(encoding='utf-8', newline='') as handle:
                reader = csv.reader(handle)
                headers = next(reader)
                columns = ', '.join(f'"{name}" NUMERIC' for name in headers)
                conn.execute(f'CREATE TABLE {path.stem} ({columns})')
                placeholders = ', '.join('?' for _ in headers)
                conn.executemany(f'INSERT INTO {path.stem} VALUES ({placeholders})', reader)
        self.assertEqual(release_count_by_genres(conn, ['Comedy']), 5)
        self.assertEqual(release_count_by_genres(conn, ['Comedy', 'Romance']), 5)
        self.assertEqual(release_count_by_genres(conn, ['comedy', 'Comedy']), 5)
        self.assertEqual(release_count_by_genres(conn, ['Romance']), 1)
        self.assertEqual(release_count_by_genres(conn, ['Unknown']), 1)
        self.assertEqual(release_count_by_genres(conn, ["Comedy') OR 1=1 --"]), 0)
        matches = find_titles_by_genres(conn, ['Comedy', 'Romance'])
        self.assertEqual(len(matches), len({r[0] for r in matches}))
        self.assertIn(('tt14', 'Hybrid', 2018, 'Comedy,Romance'), matches)
        self.assertEqual(series_counts_by_genres(conn, ['Comedy', 'Romance'], 2019), (2, 0))
        self.assertEqual(series_counts_by_genres(conn, ['Comedy', 'Romance'], 2020), (1, 1))
        self.assertEqual(series_counts_by_genres(conn, ['Romance'], 2021), (0, 0))
        with self.assertRaisesRegex(ValueError, 'at least one genre'):
            release_count_by_genres(conn, [' '])


if __name__ == '__main__':
    unittest.main()
