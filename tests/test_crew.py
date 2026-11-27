import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from scripts.refine_dataset import build

CREW_HEADER = 'tconst\tdirectors\twriters\n'
NAMES_HEADER = 'nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles\n'


def read(path):
    with Path(path).open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


class CrewTests(unittest.TestCase):
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
        self.crew.write_text(
            CREW_HEADER
            + 'tt1\tnm1,nm2,nm1\tnm2,nm3\n'     # nm1 repeated, nm2 is director and writer
            + 'tt2\t\\N\tnm4\n'                  # no directors; nm4 has no name record
            + 'tt3\tnm1\t\\N\n'                  # excluded title (no start year)
            + 'tt9\tnm1\tnm1\n', encoding='utf-8')  # title absent from the extract
        self.names = self.root / 'names.tsv'
        self.names.write_text(
            NAMES_HEADER
            + 'nm1\tAda, "The First" Lovelace\t1815\t1852\tdirector\ttt1\n'
            + 'nm2\tBob Two\t\\N\t\\N\twriter\ttt1\n'
            + 'nm3\tCy Three\tabcd\t\\N\twriter\ttt1\n'
            + 'nm5\tNot Needed\t1900\t\\N\tactor\ttt1\n', encoding='utf-8')
        self.output = self.root / 'refined'

    def run_build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return build(self.source, self.output, 2026, minimum_release_facts=1,
                         ratings_source=self.ratings,
                         crew_source=self.crew, names_source=self.names,
                         crew_download_date='2026-11-27')

    def test_people_and_roles(self):
        report = self.run_build()
        self.assertEqual(read(self.output / 'dim_person.csv'), [
            {'person_key': '1', 'nconst': 'nm1', 'person_name': 'Ada, "The First" Lovelace', 'birth_year': '1815'},
            {'person_key': '2', 'nconst': 'nm2', 'person_name': 'Bob Two', 'birth_year': ''},
            {'person_key': '3', 'nconst': 'nm3', 'person_name': 'Cy Three', 'birth_year': ''},
        ])
        self.assertEqual(
            [(r['title_id'], r['person_key'], r['role']) for r in read(self.output / 'bridge_title_person.csv')],
            [('tt1', '1', 'director'), ('tt1', '2', 'director'),
             ('tt1', '2', 'writer'), ('tt1', '3', 'writer')])
        self.assertEqual(report['crew_source_rows'], 4)
        self.assertEqual(report['crew_rows_without_eligible_title'], 2)
        self.assertEqual(report['duplicate_credits_ignored'], 1)
        self.assertEqual(report['credits_without_person_record'], 1)
        self.assertEqual(report['person_members'], 3)
        self.assertEqual(report['person_birth_year_unknown'], 1)
        self.assertEqual(report['person_birth_year_invalid'], 1)
        self.assertEqual(report['bridge_title_person_rows'], 4)
        self.assertEqual((report['bridge_director_rows'], report['bridge_writer_rows']), (2, 2))
        self.assertEqual((report['titles_with_director'], report['titles_with_writer']), (1, 1))
        self.assertEqual(len(report['crew_provenance']['sha256']), 64)
        self.assertEqual(report['names_provenance']['download_date'], '2026-11-27')

    def test_every_bridge_row_references_a_release_and_a_person(self):
        self.run_build()
        titles = {r['title_id'] for r in read(self.output / 'fact_title_release.csv')}
        people = {r['person_key'] for r in read(self.output / 'dim_person.csv')}
        for row in read(self.output / 'bridge_title_person.csv'):
            self.assertIn(row['title_id'], titles)
            self.assertIn(row['person_key'], people)

    def test_no_temporary_file_is_published(self):
        self.run_build()
        self.assertEqual(list(self.output.glob('*.pending')), [])

    def test_crew_is_optional(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = build(self.source, self.output, 2026, minimum_release_facts=1,
                           ratings_source=self.ratings)
        self.assertNotIn('person_members', report)
        self.assertFalse((self.output / 'dim_person.csv').exists())

    def test_invalid_inputs_are_rejected_and_keep_published_outputs(self):
        self.run_build()
        previous = {p.name: p.read_bytes() for p in self.output.iterdir()}
        bad_crews = {
            'columns': 'tconst\tdirectors\nnm1\tnm1\n',
            'duplicate title': CREW_HEADER + 'tt1\tnm1\t\\N\ntt1\tnm2\t\\N\n',
            'malformed title': CREW_HEADER + 'x1\tnm1\t\\N\n',
            'malformed person': CREW_HEADER + 'tt1\tabc\t\\N\n',
            'short row': CREW_HEADER + 'tt1\tnm1\n',
        }
        for label, text in bad_crews.items():
            with self.subTest(label):
                self.crew.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.run_build()
                self.assertEqual(previous, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_invalid_names_are_rejected(self):
        for label, text in {
            'duplicate person': NAMES_HEADER + 'nm1\tAda\t1815\t\\N\tx\tx\nnm1\tAda\t1815\t\\N\tx\tx\n',
            'missing name': NAMES_HEADER + 'nm1\t\\N\t1815\t\\N\tx\tx\n',
            'columns': 'nconst\tprimaryName\nnm1\tAda\n',
        }.items():
            with self.subTest(label):
                self.names.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.run_build()

    def test_download_date_must_be_iso(self):
        with self.assertRaises(ValueError):
            with contextlib.redirect_stdout(io.StringIO()):
                build(self.source, self.output, 2026, minimum_release_facts=1,
                      ratings_source=self.ratings, crew_source=self.crew,
                      names_source=self.names, crew_download_date='27/11/2026')


if __name__ == '__main__':
    unittest.main()
