"""Extract directors, writers and top-billed cast into a person dimension and a bridge.

Inputs are IMDb's ``title.crew.tsv`` and ``name.basics.tsv`` and, optionally,
``title.principals.tsv`` for the cast. Outputs, written next to the release facts, are:

* ``dim_person.csv``: one row per person credited on at least one eligible title.
* ``bridge_title_person.csv``: one row per title, person and role (director, writer or
  actor). ``billing_order`` is only filled for actors (1 is the first billed).

Only credits whose title is an eligible release fact are kept, so every bridge row can
reference ``fact_title_release``. The person key is the numeric part of the IMDb
identifier (``nm0000001`` becomes 1), so it does not depend on file reading order.
Actors and actresses share the role ``actor``; only the first ``CAST_LIMIT`` billed
people of each title are kept to bound the size of the bridge.
"""
import csv
import hashlib
import re
from datetime import date
from pathlib import Path

CREW_COLUMNS = ['tconst', 'directors', 'writers']
NAME_COLUMNS = ['nconst', 'primaryName', 'birthYear', 'deathYear',
                'primaryProfession', 'knownForTitles']
PRINCIPAL_COLUMNS = ['tconst', 'ordering', 'nconst', 'category', 'job', 'characters']
ROLES = ('director', 'writer', 'actor')
CAST_CATEGORIES = frozenset({'actor', 'actress'})
CAST_LIMIT = 5
TITLE_ID = re.compile(r'tt[0-9]+')
PERSON_ID = re.compile(r'nm[0-9]+')
UNKNOWN = ('\\N', '')


class _Bitmap:
    """Compact set of non-negative integers (a few MB for IMDb's identifier range)."""
    __slots__ = ('data', 'count')

    def __init__(self):
        self.data = bytearray()
        self.count = 0

    def add(self, number):
        """Add a number; return False when it was already present."""
        index = number >> 3
        if index >= len(self.data):
            self.data.extend(bytes(max(index + 1 - len(self.data), len(self.data) // 4, 1024)))
        bit = 1 << (number & 7)
        if self.data[index] & bit:
            return False
        self.data[index] |= bit
        self.count += 1
        return True

    def __contains__(self, number):
        index = number >> 3
        return index < len(self.data) and bool(self.data[index] & (1 << (number & 7)))


def _read_tsv(path, columns, label):
    """Yield data rows of a TSV, checking exact columns and record width."""
    with Path(path).open(encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle, delimiter='\t', quoting=csv.QUOTE_NONE)
        if next(reader, None) != columns:
            raise ValueError(f'Invalid {label} columns')
        for row in reader:
            if len(row) != len(columns):
                raise ValueError(f'Malformed {label} row at line {reader.line_num}')
            yield row


def _provenance(path, download_date, url):
    path = Path(path)
    with path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    return dict(source_file=str(path.resolve()), sha256=digest,
                size_bytes=path.stat().st_size, download_date=download_date, url=url)


def add_crew(output, crew_source, names_source, download_date, *,
             principals_source=None, principals_download_date=None):
    """Write dim_person.csv and bridge_title_person.csv into ``output``.

    ``output`` must already contain ``fact_title_release.csv``. Returns report entries.
    """
    date.fromisoformat(download_date)
    principals_download_date = principals_download_date or download_date
    date.fromisoformat(principals_download_date)
    output = Path(output)

    # Eligible titles are the ones already kept as release facts.
    eligible = _Bitmap()
    with (output / 'fact_title_release.csv').open(encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            eligible.add(int(row[0][2:]))

    counts = dict(crew_source_rows=0, crew_rows_without_eligible_title=0,
                  duplicate_credits_ignored=0, credits_without_person_record=0,
                  person_source_rows=0)
    cast_counts = dict(principals_source_rows=0, principals_rows_other_categories=0,
                       principals_rows_beyond_billing_limit=0,
                       principals_rows_without_eligible_title=0,
                       principals_duplicate_credits_ignored=0)
    pending = output / 'bridge_title_person.pending'
    seen_titles, needed = _Bitmap(), _Bitmap()
    try:
        # Pass 1: credits of eligible titles, written grouped by title.
        with pending.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            for title, directors, writers in _read_tsv(crew_source, CREW_COLUMNS, 'crew'):
                if not TITLE_ID.fullmatch(title):
                    raise ValueError('Malformed crew title identifier: ' + title)
                number = int(title[2:])
                if not seen_titles.add(number):
                    raise ValueError('Duplicate crew title: ' + title)
                counts['crew_source_rows'] += 1
                if number not in eligible:
                    counts['crew_rows_without_eligible_title'] += 1
                    continue
                credited = set()
                for role, value in zip(ROLES, (directors, writers)):
                    if value in UNKNOWN:
                        continue
                    for person in value.split(','):
                        if not PERSON_ID.fullmatch(person):
                            raise ValueError('Malformed person identifier: ' + person)
                        key = int(person[2:])
                        if (key, role) in credited:
                            counts['duplicate_credits_ignored'] += 1
                            continue
                        credited.add((key, role))
                        needed.add(key)
                        writer.writerow([title, key, role, ''])

            if principals_source is not None:
                # The file must list each title in one contiguous block; this lets
                # duplicate credits be detected without keeping every credit in memory.
                seen_principal_titles, current, credited = _Bitmap(), None, set()
                for title, ordering, person, category, *_ in _read_tsv(
                        principals_source, PRINCIPAL_COLUMNS, 'principals'):
                    if not TITLE_ID.fullmatch(title) or not PERSON_ID.fullmatch(person):
                        raise ValueError(f'Malformed principals identifier: {title} / {person}')
                    if not (ordering.isascii() and ordering.isdigit()):
                        raise ValueError(f'Invalid principals ordering for {title}: {ordering}')
                    if title != current:
                        if not seen_principal_titles.add(int(title[2:])):
                            raise ValueError('Principals are not grouped by title: ' + title)
                        current, credited = title, set()
                    cast_counts['principals_source_rows'] += 1
                    if category not in CAST_CATEGORIES:
                        cast_counts['principals_rows_other_categories'] += 1
                        continue
                    if int(ordering) > CAST_LIMIT:
                        cast_counts['principals_rows_beyond_billing_limit'] += 1
                        continue
                    if int(title[2:]) not in eligible:
                        cast_counts['principals_rows_without_eligible_title'] += 1
                        continue
                    key = int(person[2:])
                    if key in credited:
                        cast_counts['principals_duplicate_credits_ignored'] += 1
                        continue
                    credited.add(key)
                    needed.add(key)
                    writer.writerow([title, key, 'actor', int(ordering)])

        # Pass 2: names and birth years of the people who are needed.
        present, birth_unknown, birth_invalid = _Bitmap(), 0, 0
        seen_people = _Bitmap()
        with (output / 'dim_person.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['person_key', 'nconst', 'person_name', 'birth_year'])
            for person, name, birth, *_ in _read_tsv(names_source, NAME_COLUMNS, 'names'):
                if not PERSON_ID.fullmatch(person):
                    raise ValueError('Malformed person identifier: ' + person)
                key = int(person[2:])
                if not seen_people.add(key):
                    raise ValueError('Duplicate person: ' + person)
                counts['person_source_rows'] += 1
                if key not in needed:
                    continue
                if not name or name in UNKNOWN:
                    raise ValueError('Person without name: ' + person)
                if birth in UNKNOWN:
                    birth, birth_unknown = '', birth_unknown + 1
                elif not (birth.isascii() and birth.isdigit() and 1 <= int(birth) <= 9999):
                    birth, birth_invalid = '', birth_invalid + 1
                present.add(key)
                writer.writerow([key, person, name, birth])

        # Pass 3: keep only credits whose person has a name record.
        bridge_rows = {role: 0 for role in ROLES}
        titles_with_role, last_title = {role: 0 for role in ROLES}, {}
        with pending.open(encoding='utf-8', newline='') as source, \
                (output / 'bridge_title_person.csv').open('w', encoding='utf-8', newline='') as target:
            writer = csv.writer(target)
            writer.writerow(['title_id', 'person_key', 'role', 'billing_order'])
            for title, key, role, billing in csv.reader(source):
                if int(key) not in present:
                    counts['credits_without_person_record'] += 1
                    continue
                writer.writerow([title, key, role, billing])
                bridge_rows[role] += 1
                if last_title.get(role) != title:
                    last_title[role] = title
                    titles_with_role[role] += 1
    finally:
        pending.unlink(missing_ok=True)

    report = dict(
        **counts,
        person_members=present.count,
        person_birth_year_unknown=birth_unknown,
        person_birth_year_invalid=birth_invalid,
        bridge_title_person_rows=sum(bridge_rows.values()),
        bridge_director_rows=bridge_rows['director'],
        bridge_writer_rows=bridge_rows['writer'],
        titles_with_director=titles_with_role['director'],
        titles_with_writer=titles_with_role['writer'],
        crew_provenance=_provenance(crew_source, download_date,
                                    'https://datasets.imdbws.com/title.crew.tsv.gz'),
        names_provenance=_provenance(names_source, download_date,
                                     'https://datasets.imdbws.com/name.basics.tsv.gz'),
    )
    if principals_source is not None:
        report.update(
            **cast_counts,
            cast_billing_limit=CAST_LIMIT,
            bridge_actor_rows=bridge_rows['actor'],
            titles_with_actor=titles_with_role['actor'],
            principals_provenance=_provenance(
                principals_source, principals_download_date,
                'https://datasets.imdbws.com/title.principals.tsv.gz'))
    return report
