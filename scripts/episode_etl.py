"""Extract the series, season and episode numbers of eligible episodes.

The input is IMDb's ``title.episode.tsv``. The output, ``title_episode.csv``, has one row
per episode that is an eligible release fact whose parent series is also an eligible
release fact, so both identifiers reference ``fact_title_release``.
"""
import csv
import re
from datetime import date
from pathlib import Path

from scripts.crew_etl import UNKNOWN, _Bitmap, _provenance, _read_tsv

EPISODE_COLUMNS = ['tconst', 'parentTconst', 'seasonNumber', 'episodeNumber']
TITLE_ID = re.compile(r'tt[0-9]+')
MAX_NUMBER = 100_000


def _number(value):
    """Return (text for the CSV, status) for a season or episode number."""
    if value in UNKNOWN:
        return '', 'unknown'
    if value.isascii() and value.isdigit() and int(value) <= MAX_NUMBER:
        return str(int(value)), 'ok'
    return '', 'invalid'


def add_episodes(output, episode_source, download_date):
    """Write title_episode.csv into ``output`` and return report entries.

    ``output`` must already contain ``fact_title_release.csv``.
    """
    date.fromisoformat(download_date)
    output = Path(output)
    eligible = _Bitmap()
    with (output / 'fact_title_release.csv').open(encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            eligible.add(int(row[0][2:]))

    counts = dict(episode_source_rows=0, episodes_without_eligible_title=0,
                  episodes_without_eligible_parent=0, episodes_unknown_season=0,
                  episodes_unknown_number=0, episodes_invalid_number=0)
    seen, parents = _Bitmap(), _Bitmap()
    written = 0
    with (output / 'title_episode.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['title_id', 'parent_series_id', 'season_number', 'episode_number'])
        for title, parent, season, number in _read_tsv(episode_source, EPISODE_COLUMNS, 'episode'):
            if not TITLE_ID.fullmatch(title) or not TITLE_ID.fullmatch(parent):
                raise ValueError(f'Malformed episode identifier: {title} / {parent}')
            if title == parent:
                raise ValueError('Episode is its own parent: ' + title)
            child, owner = int(title[2:]), int(parent[2:])
            if not seen.add(child):
                raise ValueError('Duplicate episode title: ' + title)
            counts['episode_source_rows'] += 1
            if child not in eligible:
                counts['episodes_without_eligible_title'] += 1
                continue
            if owner not in eligible:
                counts['episodes_without_eligible_parent'] += 1
                continue
            season, season_status = _number(season)
            number, number_status = _number(number)
            counts['episodes_unknown_season'] += int(season_status == 'unknown')
            counts['episodes_unknown_number'] += int(number_status == 'unknown')
            counts['episodes_invalid_number'] += int('invalid' in (season_status, number_status))
            parents.add(owner)
            writer.writerow([title, parent, season, number])
            written += 1
    return dict(**counts, episode_rows=written, series_with_episodes=parents.count,
                episode_provenance=_provenance(episode_source, download_date,
                                               'https://datasets.imdbws.com/title.episode.tsv.gz'))
