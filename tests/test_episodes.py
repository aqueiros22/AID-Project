import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from scripts.refine_dataset import build

HEADER = 'tconst\tparentTconst\tseasonNumber\tepisodeNumber\n'


def read(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'titles.tsv'
        self.source.write_text(
            'tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres\n'
            'tt1\ttvSeries\tShow\tShow\t0\t2020\t\\N\t45\tDrama\n'
            'tt2\ttvEpisode\tPilot\tPilot\t0\t2020\t\\N\t45\tDrama\n'
            'tt3\ttvEpisode\tSecond\tSecond\t0\t2020\t\\N\t45\tDrama\n'
            'tt4\ttvEpisode\tNo numbers\tNo numbers\t0\t2021\t\\N\t\\N\tDrama\n'
            'tt5\ttvEpisode\tNo year\tNo year\t0\t\\N\t\\N\t\\N\tDrama\n'
            'tt6\ttvEpisode\tOrphan\tOrphan\t0\t2021\t\\N\t\\N\tDrama\n'
            'tt7\ttvEpisode\tBad\tBad\t0\t2021\t\\N\t\\N\tDrama\n', encoding='utf-8')
        self.ratings = self.root / 'ratings.tsv'
        self.ratings.write_text('tconst\taverageRating\tnumVotes\ntt1\t8.0\t10000\n')
        self.episodes = self.root / 'episodes.tsv'
        self.episodes.write_text(
            HEADER
            + 'tt2\ttt1\t1\t1\n'
            + 'tt3\ttt1\t1\t2\n'
            + 'tt4\ttt1\t\\N\t\\N\n'          # unknown season and number
            + 'tt5\ttt1\t1\t3\n'              # episode excluded (no start year)
            + 'tt6\ttt99\t1\t1\n'             # parent absent from the extract
            + 'tt7\ttt1\tabc\t4\n'            # invalid season
            + 'tt98\ttt1\t1\t5\n', encoding='utf-8')  # episode absent from the extract
        self.output = self.root / 'refined'

    def run_build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return build(self.source, self.output, 2026, minimum_release_facts=1,
                         ratings_source=self.ratings, episode_source=self.episodes,
                         episode_download_date='2026-11-27')

    def test_episode_rows_and_counters(self):
        report = self.run_build()
        self.assertEqual(
            [(r['title_id'], r['parent_series_id'], r['season_number'], r['episode_number'])
             for r in read(self.output / 'title_episode.csv')],
            [('tt2', 'tt1', '1', '1'), ('tt3', 'tt1', '1', '2'),
             ('tt4', 'tt1', '', ''), ('tt7', 'tt1', '', '4')])
        self.assertEqual(report['episode_source_rows'], 7)
        self.assertEqual(report['episode_rows'], 4)
        self.assertEqual(report['episodes_without_eligible_title'], 2)
        self.assertEqual(report['episodes_without_eligible_parent'], 1)
        self.assertEqual(report['episodes_unknown_season'], 1)
        self.assertEqual(report['episodes_unknown_number'], 1)
        self.assertEqual(report['episodes_invalid_number'], 1)
        self.assertEqual(report['series_with_episodes'], 1)
        self.assertEqual(len(report['episode_provenance']['sha256']), 64)

    def test_every_row_references_two_release_facts(self):
        self.run_build()
        titles = {r['title_id'] for r in read(self.output / 'fact_title_release.csv')}
        for row in read(self.output / 'title_episode.csv'):
            self.assertIn(row['title_id'], titles)
            self.assertIn(row['parent_series_id'], titles)

    def test_episodes_are_optional(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = build(self.source, self.output, 2026, minimum_release_facts=1,
                           ratings_source=self.ratings)
        self.assertNotIn('episode_rows', report)
        self.assertFalse((self.output / 'title_episode.csv').exists())

    def test_invalid_inputs_are_rejected_and_keep_published_outputs(self):
        self.run_build()
        previous = {p.name: p.read_bytes() for p in self.output.iterdir()}
        for label, text in {
            'columns': 'tconst\tparentTconst\ntt2\ttt1\n',
            'duplicate': HEADER + 'tt2\ttt1\t1\t1\ntt2\ttt1\t1\t2\n',
            'malformed identifier': HEADER + 'x2\ttt1\t1\t1\n',
            'own parent': HEADER + 'tt2\ttt2\t1\t1\n',
            'short row': HEADER + 'tt2\ttt1\t1\n',
        }.items():
            with self.subTest(label):
                self.episodes.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.run_build()
                self.assertEqual(previous, {p.name: p.read_bytes() for p in self.output.iterdir()})


if __name__ == '__main__':
    unittest.main()
