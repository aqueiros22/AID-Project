import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from scripts.refine_dataset import build

PRINCIPALS_HEADER = 'tconst\tordering\tnconst\tcategory\tjob\tcharacters\n'
NAMES_HEADER = 'nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles\n'


def read(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


class CastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'titles.tsv'
        self.source.write_text(
            'tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres\n'
            'tt1\tmovie\tOne\tOne\t0\t2020\t\\N\t90\tDrama\n'
            'tt2\tmovie\tTwo\tTwo\t0\t2021\t\\N\t95\tDrama\n'
            'tt3\tmovie\tThree\tThree\t0\t\\N\t\\N\t95\tDrama\n', encoding='utf-8')
        self.ratings = self.root / 'ratings.tsv'
        self.ratings.write_text('tconst\taverageRating\tnumVotes\ntt1\t8.0\t10000\n')
        self.crew = self.root / 'crew.tsv'
        self.crew.write_text('tconst\tdirectors\twriters\ntt1\tnm1\t\\N\ntt2\t\\N\t\\N\n', encoding='utf-8')
        self.names = self.root / 'names.tsv'
        self.names.write_text(
            NAMES_HEADER
            + 'nm1\tAda Lovelace\t1815\t1852\tdirector\ttt1\n'
            + 'nm5\tEve Five\t1980\t\\N\tactress\ttt1\n'
            + 'nm6\tFay Six\t\\N\t\\N\tactor\ttt1\n'
            + 'nm7\tGus Seven\t1970\t\\N\tactor\ttt1\n', encoding='utf-8')
        self.principals = self.root / 'principals.tsv'
        self.principals.write_text(
            PRINCIPALS_HEADER
            + 'tt1\t1\tnm5\tactress\t\\N\t["Alice"]\n'
            + 'tt1\t2\tnm6\tactor\t\\N\t\\N\n'
            + 'tt1\t3\tnm1\tdirector\t\\N\t\\N\n'     # other category
            + 'tt1\t4\tnm5\tactress\t\\N\t\\N\n'      # same person again
            + 'tt1\t5\tnm7\tactor\t\\N\t\\N\n'
            + 'tt1\t6\tnm8\tactor\t\\N\t\\N\n'        # beyond the billing limit
            + 'tt2\t1\tnm6\tactor\t\\N\t\\N\n'
            + 'tt2\t2\tnm9\tactor\t\\N\t\\N\n'        # no name record
            + 'tt3\t1\tnm5\tactor\t\\N\t\\N\n'        # excluded title
            + 'tt99\t1\tnm5\tactor\t\\N\t\\N\n',      # title absent from the extract
            encoding='utf-8')
        self.output = self.root / 'refined'

    def run_build(self, principals=True):
        with contextlib.redirect_stdout(io.StringIO()):
            return build(self.source, self.output, 2026, minimum_release_facts=1,
                         ratings_source=self.ratings, crew_source=self.crew,
                         names_source=self.names, crew_download_date='2026-11-27',
                         principals_source=self.principals if principals else None,
                         principals_download_date='2026-11-28')

    def test_cast_rows_and_counters(self):
        report = self.run_build()
        self.assertEqual(
            [(r['title_id'], r['person_key'], r['role'], r['billing_order'])
             for r in read(self.output / 'bridge_title_person.csv')],
            [('tt1', '1', 'director', ''), ('tt1', '5', 'actor', '1'), ('tt1', '6', 'actor', '2'),
             ('tt1', '7', 'actor', '5'), ('tt2', '6', 'actor', '1')])
        self.assertEqual([r['person_key'] for r in read(self.output / 'dim_person.csv')], ['1', '5', '6', '7'])
        self.assertEqual(report['principals_source_rows'], 10)
        self.assertEqual(report['principals_rows_other_categories'], 1)
        self.assertEqual(report['principals_rows_beyond_billing_limit'], 1)
        self.assertEqual(report['principals_rows_without_eligible_title'], 2)
        self.assertEqual(report['principals_duplicate_credits_ignored'], 1)
        self.assertEqual(report['credits_without_person_record'], 1)
        self.assertEqual(report['bridge_actor_rows'], 4)
        self.assertEqual(report['titles_with_actor'], 2)
        self.assertEqual(report['cast_billing_limit'], 5)
        self.assertEqual(report['bridge_title_person_rows'], 5)
        self.assertEqual(report['principals_provenance']['download_date'], '2026-11-28')
        self.assertEqual(report['crew_provenance']['download_date'], '2026-11-27')

    def test_cast_is_optional(self):
        report = self.run_build(principals=False)
        self.assertNotIn('bridge_actor_rows', report)
        self.assertEqual([r['role'] for r in read(self.output / 'bridge_title_person.csv')], ['director'])

    def test_invalid_principals_are_rejected_and_keep_published_outputs(self):
        self.run_build()
        previous = {p.name: p.read_bytes() for p in self.output.iterdir()}
        for label, text in {
            'columns': 'tconst\tordering\ntt1\t1\n',
            'not grouped': PRINCIPALS_HEADER + 'tt1\t1\tnm5\tactor\t\\N\t\\N\ntt2\t1\tnm6\tactor\t\\N\t\\N\n'
                                               'tt1\t2\tnm7\tactor\t\\N\t\\N\n',
            'invalid ordering': PRINCIPALS_HEADER + 'tt1\tx\tnm5\tactor\t\\N\t\\N\n',
            'malformed identifier': PRINCIPALS_HEADER + 'tt1\t1\tabc\tactor\t\\N\t\\N\n',
            'short row': PRINCIPALS_HEADER + 'tt1\t1\tnm5\tactor\n',
        }.items():
            with self.subTest(label):
                self.principals.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.run_build()
                self.assertEqual(previous, {p.name: p.read_bytes() for p in self.output.iterdir()})


if __name__ == '__main__':
    unittest.main()
