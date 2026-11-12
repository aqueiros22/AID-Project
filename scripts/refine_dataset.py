"""Build releases, annual series intervals and an IMDb ratings extract."""
import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from scripts.paths import RAW_DATA, RATINGS_DATA, REFINED
from scripts.ratings_etl import add_ratings


SERIES_TYPES = frozenset({'tvSeries', 'tvMiniSeries'})


def build(source, output, cutoff, *, minimum_release_facts=10001, ratings_source=None, download_date="2026-11-12"):
    """Validate staged outputs before publishing; roll back publication errors."""
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.refine-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'next'
        report = _build(source, staging, cutoff, minimum_release_facts=minimum_release_facts)
        report.update(add_ratings(staging, ratings_source, download_date))
        report["source_provenance"].update(provided_by="IMDb Non-Commercial Datasets", download_date=download_date, url="https://datasets.imdbws.com/title.basics.tsv.gz")
        (staging / "validation_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        output.mkdir(parents=True, exist_ok=True)
        backup = Path(temporary) / 'previous'
        backup.mkdir()
        published, saved = [], []
        try:
            for path in sorted(staging.iterdir()):
                destination = output / path.name
                if destination.exists():
                    os.replace(destination, backup / path.name)
                    saved.append(path.name)
                os.replace(path, destination)
                published.append(path.name)
            for name in ('fact_year_end_catalog.csv', 'dim_rating_extract.csv'):
                legacy = output / name
                if legacy.exists():
                    os.replace(legacy, backup / legacy.name)
                    saved.append(legacy.name)
        except Exception:
            for name in published:
                (output / name).unlink()
            for name in saved:
                os.replace(backup / name, output / name)
            raise
    print(json.dumps(report, indent=2))
    return report


def _build(source, output, cutoff, *, minimum_release_facts=10001):
    if not 1 <= cutoff <= 9999:
        raise ValueError('Cutoff year must be between 1 and 9999')
    output.mkdir(parents=True, exist_ok=True)
    dimensions = {name: {} for name in ('title_type', 'genre_set', 'audience')}
    # Start and end+1 deltas avoid expanding each series into individual years.
    events = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    counts = Counter()
    for name in (
        'excluded_missing_start_year', 'excluded_invalid_start_year',
        'excluded_outside_cutoff_year', 'missing_runtime_facts',
        'invalid_runtime_facts', 'unknown_genre_facts', 'unknown_audience_facts',
        'series_candidates', 'series_with_known_end', 'series_with_unknown_end',
        'series_excluded_invalid_end_year', 'series_known_end_after_cutoff',
        'snapshot_facts', 'known_end_series_years', 'unknown_end_series_years',
        'known_end_series_at_cutoff', 'unknown_end_series_at_cutoff',
    ):
        counts[name] = 0
    years = set()

    def key(name, value):
        members = dimensions[name]
        if value not in members:
            members[value] = len(members) + 1
        return members[value]

    def writer(handle, columns):
        result = csv.writer(handle)
        result.writerow(columns)
        return result

    with source.open(encoding='utf-8', newline='') as raw, (output / 'fact_title_release.csv').open('w', encoding='utf-8', newline='') as target:
        facts = writer(target, ['title_id', 'primary_title', 'year_key', 'title_type_key', 'genre_set_key', 'audience_key', 'title_count', 'runtime_minutes', 'runtime_known_count'])
        reader = csv.DictReader(raw, delimiter='\t', quoting=csv.QUOTE_NONE)
        required = {'tconst', 'titleType', 'primaryTitle', 'isAdult', 'startYear',
                    'endYear', 'runtimeMinutes', 'genres'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Source TSV is missing required columns')
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Malformed TSV record at line {reader.line_num}')
            counts['source_rows'] += 1
            year = row['startYear'].strip()
            if not year.isdigit() or not 1 <= int(year) <= cutoff:
                counts['excluded_unknown_or_outside_cutoff_year'] += 1
                reason = ('missing_start_year' if year in ('\\N', '') else
                          'invalid_start_year' if not year.isdigit() else
                          'outside_cutoff_year')
                counts[f'excluded_{reason}'] += 1
                continue
            year = int(year)
            genre = ','.join(sorted({value.strip() for value in row['genres'].split(',')
                                     if value.strip() not in ('', '\\N')})) or 'Unknown'
            audience = {'0': 'Not adult-flagged', '1': 'Adult-flagged'}.get(row['isAdult'], 'Unknown')
            counts['unknown_genre_facts'] += int(genre == 'Unknown')
            counts['unknown_audience_facts'] += int(audience == 'Unknown')
            keys = (key('title_type', row['titleType']), key('genre_set', genre), key('audience', audience))
            runtime = row['runtimeMinutes'].strip()
            known = runtime.isdigit() and int(runtime) > 0
            if not known:
                counts['missing_runtime_facts' if runtime in ('\\N', '') else 'invalid_runtime_facts'] += 1
            minutes = int(runtime) if known else 0
            facts.writerow([row['tconst'], row['primaryTitle'], year, *keys, 1, minutes, int(known)])
            years.add(year)
            counts['release_facts'] += 1
            counts['known_runtime_facts'] += int(known)
            counts['total_runtime_minutes'] += minutes

            if row['titleType'] not in SERIES_TYPES:
                continue
            counts['series_candidates'] += 1
            raw_end = row['endYear'].strip()
            unknown_end = raw_end in ('\\N', '')
            if unknown_end:
                # A cumulative metadata count, not an inferred airing interval.
                last_reporting_year = cutoff
                counts['series_with_unknown_end'] += 1
            elif raw_end.isdigit() and year <= int(raw_end) <= 9999:
                end = int(raw_end)
                last_reporting_year = min(end, cutoff)
                counts['series_with_known_end'] += 1
                counts['series_known_end_after_cutoff'] += int(end > cutoff)
            else:
                # Invalid ends are not evidence that a series is still airing.
                # Its release remains in the detail fact table.
                counts['series_excluded_invalid_end_year'] += 1
                continue
            index = int(unknown_end)
            events[year][keys][index] += 1
            events[last_reporting_year + 1][keys][index] -= 1
            prefix = 'unknown_end' if unknown_end else 'known_end'
            counts[f'{prefix}_series_years'] += last_reporting_year - year + 1
            counts[f'{prefix}_series_at_cutoff'] += int(last_reporting_year == cutoff)

    if not years:
        raise ValueError('No eligible titles in the source')
    # One membership per distinct genre in each combination. The combination
    # remains the fact FK, so the same bridge also filters aggregate snapshots.
    genre_members = sorted({
        genre for genre_set in dimensions['genre_set']
        for genre in genre_set.split(',')
    })
    dimensions['genre'] = {genre: index for index, genre in enumerate(genre_members, 1)}
    bridge = [
        (genre_set_key, dimensions['genre'][genre])
        for genre_set, genre_set_key in dimensions['genre_set'].items()
        for genre in genre_set.split(',')
    ]
    with (output / 'bridge_genre_set_genre.csv').open('w', encoding='utf-8', newline='') as target:
        rows = writer(target, ['genre_set_key', 'genre_key'])
        rows.writerows(bridge)
    for name, members in dimensions.items():
        with (output / f'dim_{name}.csv').open('w', encoding='utf-8', newline='') as target:
            rows = writer(target, [f'{name}_key', name])
            rows.writerows((value, label) for label, value in members.items())
    with (output / 'dim_year.csv').open('w', encoding='utf-8', newline='') as target:
        rows = writer(target, ['year_key', 'year', 'decade', 'century', 'snapshot_date'])
        for year in range(min(years), cutoff + 1):
            rows.writerow([year, year, year // 10 * 10, (year - 1) // 100 + 1, f'{year:04d}-12-31'])

    running = defaultdict(lambda: [0, 0])
    snapshot_series_years = [0, 0]
    with (output / 'fact_series_year_snapshot.csv').open('w', encoding='utf-8', newline='') as target:
        rows = writer(target, ['year_key', 'title_type_key', 'genre_set_key', 'audience_key', 'known_interval_series_count', 'started_series_unknown_end_count'])
        for year in range(min(years), cutoff + 1):
            for keys, values in events.get(year, {}).items():
                running[keys][0] += values[0]
                running[keys][1] += values[1]
                if min(running[keys]) < 0:
                    raise ValueError('Negative series count in snapshot')
                if running[keys] == [0, 0]:
                    del running[keys]
            for keys, values in sorted(running.items()):
                rows.writerow([year, *keys, *values])
                counts['snapshot_facts'] += 1
                for index in (0, 1):
                    snapshot_series_years[index] += values[index]
    if counts['release_facts'] < minimum_release_facts:
        raise ValueError(f'Requirement: at least {minimum_release_facts:,} release facts')
    for index, prefix in enumerate(('known_end', 'unknown_end')):
        if snapshot_series_years[index] != counts[f'{prefix}_series_years']:
            raise ValueError(f'{prefix}: snapshot reporting-year memberships do not match source rules')
        if sum(value[index] for value in running.values()) != counts[f'{prefix}_series_at_cutoff']:
            raise ValueError(f'{prefix}: final snapshot does not match source intervals')
    report = dict(counts)
    with source.open('rb') as handle:
        source_sha256 = hashlib.file_digest(handle, 'sha256').hexdigest()
    report.update(
        source_provenance={
            'provided_by': 'IMDb Non-Commercial Datasets',
            'source_file': str(source.resolve()),
            'sha256': source_sha256,
            'size_bytes': source.stat().st_size,
            'original_extraction_date': None,
            'extract_version': None,
        },
        cutoff_year=cutoff, first_year=min(years),
        dimension_members={name: len(values) for name, values in dimensions.items()},
        year_members=cutoff - min(years) + 1,
        genre_bridge_rows=len(bridge),
        series_types=sorted(SERIES_TYPES),
        snapshot_semantics_version=2,
        snapshot_rule='Known interval: startYear <= year <= endYear; unknown end: startYear <= year, status indeterminate',
        missing_end_policy='Count already-started series with unknown end separately; do not infer continued airing or combine into an airing total',
        measure_definitions={
            'known_interval_series_count': 'Valid known start/end interval includes reference year (inclusive)',
            'started_series_unknown_end_count': 'Series started by reference year with missing end; airing status indeterminate',
        },
        series_year_control_note='Technical reporting-year memberships for each measure, not observed years of airing',
        unknown_end_values=['\\N', ''],
        invalid_end_policy='Exclude from series snapshot only; retain release fact',
        validation_checks={
            'minimum_release_facts': True,
            'nonnegative_snapshot_counts': True,
            'reporting_year_memberships_match_source': True,
            'cutoff_counts_match_source_intervals': True,
        },
    )
    (output / 'validation_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    # Retire the previous generated fact after the replacement has passed validation.
    (output / 'fact_year_end_catalog.csv').unlink(missing_ok=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=RAW_DATA)
    parser.add_argument('--output', type=Path, default=REFINED)
    parser.add_argument('--cutoff-year', type=int, default=2026, help='Last year to include (may be incomplete)')
    parser.add_argument('--ratings-source', type=Path, default=RATINGS_DATA)
    parser.add_argument('--download-date', default='2026-11-12')
    args = parser.parse_args()
    build(args.source, args.output, args.cutoff_year, ratings_source=args.ratings_source, download_date=args.download_date)
