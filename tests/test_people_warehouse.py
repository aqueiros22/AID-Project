import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_warehouse import build_warehouse, open_warehouse, validate_warehouse
from scripts.refine_dataset import build

NAMES = ('nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles\n'
         'nm1\tAda Lovelace\t1815\t\\N\tdirector\ttt1\n'
         'nm2\tBob Two\t\\N\t\\N\twriter\ttt1\n'
         'nm5\tEve Five\t1980\t\\N\tactress\ttt1\n')
PRINCIPALS = ('tconst\tordering\tnconst\tcategory\tjob\tcharacters\n'
              'tt1\t1\tnm5\tactress\t\\N\t\\N\n')
EPISODES = ('tconst\tparentTconst\tseasonNumber\tepisodeNumber\n'
            'tt2\ttt1\t1\t1\ntt3\ttt1\t\\N\t\\N\n')


class PeopleWarehouseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'titles.tsv').write_text(
            'tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres\n'
            'tt1\ttvSeries\tShow\tShow\t0\t2020\t\\N\t45\tDrama\n'
            'tt2\ttvEpisode\tPilot\tPilot\t0\t2020\t\\N\t45\tDrama\n'
            'tt3\ttvEpisode\tSecond\tSecond\t0\t2020\t\\N\t45\tDrama\n', encoding='utf-8')
        (self.root / 'ratings.tsv').write_text('tconst\taverageRating\tnumVotes\ntt1\t8.0\t10000\n')
        (self.root / 'crew.tsv').write_text('tconst\tdirectors\twriters\ntt1\tnm1\tnm1,nm2\n')
        (self.root / 'names.tsv').write_text(NAMES, encoding='utf-8')
        (self.root / 'principals.tsv').write_text(PRINCIPALS, encoding='utf-8')
        (self.root / 'episodes.tsv').write_text(EPISODES, encoding='utf-8')
        self.refined, self.database = self.root / 'refined', self.root / 'wh.db'

    def run_all(self, people=True, episodes=True):
        kwargs = {}
        if people:
            kwargs.update(crew_source=self.root / 'crew.tsv', names_source=self.root / 'names.tsv',
                          principals_source=self.root / 'principals.tsv')
        if episodes:
            kwargs.update(episode_source=self.root / 'episodes.tsv')
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root / 'titles.tsv', self.refined, 2026, minimum_release_facts=1,
                  ratings_source=self.root / 'ratings.tsv', **kwargs)
            build_warehouse(self.refined, self.database, minimum_release_facts=1)

    def conn(self, readonly=True):
        conn = open_warehouse(self.database, readonly=readonly)
        self.addCleanup(conn.close)
        return conn

    def test_tables_are_loaded_with_nulls(self):
        self.run_all()
        conn = self.conn()
        self.assertEqual(conn.execute('SELECT person_name, birth_year FROM dim_person ORDER BY person_key').fetchall(),
                         [('Ada Lovelace', 1815), ('Bob Two', None), ('Eve Five', 1980)])
        self.assertEqual(conn.execute('SELECT role, COUNT(*) FROM bridge_title_person GROUP BY role ORDER BY role').fetchall(),
                         [('actor', 1), ('director', 1), ('writer', 2)])
        self.assertEqual(conn.execute('SELECT title_id, season_number, episode_number FROM title_episode ORDER BY title_id').fetchall(),
                         [('tt2', 1, 1), ('tt3', None, None)])
        report = json.loads(conn.execute('SELECT source_report_json FROM warehouse_metadata').fetchone()[0])
        counts = validate_warehouse(conn, report, minimum_release_facts=1)['row_counts']
        self.assertEqual((counts['dim_person'], counts['bridge_title_person'], counts['title_episode']), (3, 4, 2))

    def test_constraints_are_enforced(self):
        self.run_all()
        conn = self.conn(readonly=False)
        bad = [
            "INSERT INTO bridge_title_person VALUES ('tt1', 999, 'director', NULL)",   # unknown person
            "INSERT INTO bridge_title_person VALUES ('tt9', 1, 'director', NULL)",     # unknown title
            "INSERT INTO bridge_title_person VALUES ('tt1', 1, 'producer', NULL)",     # invalid role
            "INSERT INTO bridge_title_person VALUES ('tt1', 2, 'actor', NULL)",        # actor needs order
            "INSERT INTO bridge_title_person VALUES ('tt1', 2, 'director', 1)",        # order only for actors
            "INSERT INTO dim_person VALUES (7, 'nm8', 'Wrong key', 1900)",             # key/nconst mismatch
            "INSERT INTO title_episode VALUES ('tt1', 'tt1', 1, 1)",                   # own parent
            "INSERT INTO title_episode VALUES ('tt1', 'tt9', 1, 1)",                   # unknown parent
        ]
        for statement in bad:
            with self.subTest(statement), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(statement)

    def test_tampering_is_detected(self):
        self.run_all()
        conn = self.conn(readonly=False)
        report = json.loads(conn.execute('SELECT source_report_json FROM warehouse_metadata').fetchone()[0])
        conn.execute("DELETE FROM bridge_title_person WHERE role = 'writer' AND person_key = 2")
        with self.assertRaises(ValueError):
            validate_warehouse(conn, report, minimum_release_facts=1)

    def test_sources_are_optional(self):
        self.run_all(people=False, episodes=False)
        conn = self.conn()
        for table in ('dim_person', 'bridge_title_person', 'title_episode'):
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        report = json.loads(conn.execute('SELECT source_report_json FROM warehouse_metadata').fetchone()[0])
        validate_warehouse(conn, report, minimum_release_facts=1)


if __name__ == '__main__':
    unittest.main()
