"""Build releases (with series end metadata), an annual catalogue snapshot and an IMDb ratings extract."""
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
            for name in ('fact_year_end_catalog.csv', 'dim_rating_extract.csv', 'fact_series_year_snapshot.csv'):
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
    # New titles per start year and category; the catalogue stock is their running sum.
    new_titles = defaultdict(Counter)
    counts = Counter()
    for name in (
        'excluded_missing_start_year', 'excluded_invalid_start_year',
        'excluded_outside_cutoff_year', 'missing_runtime_facts',
        'invalid_runtime_facts', 'unknown_genre_facts', 'unknown_audience_facts',
        'series_candidates', 'series_with_known_end', 'series_with_unknown_end',
        'series_excluded_invalid_end_year', 'series_known_end_after_cutoff',
        'known_end_series_years', 'unknown_end_series_years',
        'known_end_series_at_cutoff', 'unknown_end_series_at_cutoff',
        'catalog_snapshot_facts', 'catalog_new_titles_total', 'catalog_titles_at_cutoff',
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
        facts = writer(target, ['title_id', 'primary_title', 'year_key', 'title_type_key', 'genre_set_key', 'audience_key', 'title_count', 'runtime_minutes', 'runtime_known_count', 'series_end_status', 'end_year'])
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
            end_status, end_year = _series_end(row, year, cutoff, counts)
            facts.writerow([row['tconst'], row['primaryTitle'], year, *keys, 1, minutes, int(known),
                            end_status or '', '' if end_year is None else end_year])
            years.add(year)
            new_titles[year][keys] += 1
            counts['release_facts'] += 1
            counts['known_runtime_facts'] += int(known)
            counts['total_runtime_minutes'] += minutes

    if not years:
        raise ValueError('No eligible titles in the source')
    # One membership per distinct genre in each combination. The combination
    # remains the fact FK, so the same bridge also filters the catalogue snapshot.
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

    # Dense stock: once a category appears it has a row in every later year.
    stock = Counter()
    with (output / 'fact_catalog_year_snapshot.csv').open('w', encoding='utf-8', newline='') as target:
        rows = writer(target, ['year_key', 'title_type_key', 'genre_set_key', 'audience_key', 'new_title_count', 'cumulative_title_count'])
        for year in range(min(years), cutoff + 1):
            flow = new_titles.get(year, {})
            stock.update(flow)
            for keys in sorted(stock):
                rows.writerow([year, *keys, flow.get(keys, 0), stock[keys]])
                counts['catalog_snapshot_facts'] += 1
                counts['catalog_new_titles_total'] += flow.get(keys, 0)
    counts['catalog_titles_at_cutoff'] = sum(stock.values())
    if counts['release_facts'] < minimum_release_facts:
        raise ValueError(f'Requirement: at least {minimum_release_facts:,} release facts')
    if not counts['catalog_new_titles_total'] == counts['catalog_titles_at_cutoff'] == counts['release_facts']:
        raise ValueError('Catalogue snapshot does not reconcile with release facts')
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
        snapshot_semantics_version=3,
        snapshot_rule='Catalogue snapshot: one row per reference year and category from its first title through cutoff',
        series_end_rule='Known interval: startYear <= year <= endYear; unknown end: startYear <= year, status indeterminate',
        missing_end_policy='Count already-started series with unknown end separately; do not infer continued airing or combine into an airing total',
        measure_definitions={
            'new_title_count': 'Eligible titles whose start year is the reference year (flow, additive)',
            'cumulative_title_count': 'Eligible titles with start year up to the reference year (stock, semi-additive over time)',
            'series_end_status': 'known / unknown / invalid end year for tvSeries and tvMiniSeries; NULL for other types',
        },
        series_year_control_note='Technical reporting-year memberships for each series measure, not observed years of airing',
        unknown_end_values=['\\N', ''],
        invalid_end_policy='Store status invalid and no end year; excluded from series interval measures; release retained',
        validation_checks={
            'minimum_release_facts': True,
            'catalog_flow_matches_releases': True,
            'catalog_stock_at_cutoff_matches_releases': True,
        },
    )
    (output / 'validation_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    # Retire previous generated facts after the replacement has passed validation.
    for legacy in ('fact_year_end_catalog.csv', 'fact_series_year_snapshot.csv'):
        (output / legacy).unlink(missing_ok=True)
    return report


def _series_end(row, year, cutoff, counts):
    """Classify a series end year and update the series control counts.

    Returns (status, end_year): status is None for non-series types, and end_year
    is set only for a valid known end. Control counts are reporting-year
    memberships used to validate the series queries, not years of airing.
    """
    if row['titleType'] not in SERIES_TYPES:
        return None, None
    counts['series_candidates'] += 1
    raw_end = row['endYear'].strip()
    if raw_end in ('\\N', ''):
        # A cumulative metadata count, not an inferred airing interval.
        counts['series_with_unknown_end'] += 1
        counts['unknown_end_series_years'] += cutoff - year + 1
        counts['unknown_end_series_at_cutoff'] += 1
        return 'unknown', None
    if raw_end.isdigit() and year <= int(raw_end) <= 9999:
        end = int(raw_end)
        last_reporting_year = min(end, cutoff)
        counts['series_with_known_end'] += 1
        counts['series_known_end_after_cutoff'] += int(end > cutoff)
        counts['known_end_series_years'] += last_reporting_year - year + 1
        counts['known_end_series_at_cutoff'] += int(last_reporting_year == cutoff)
        return 'known', end
    # Invalid ends are not evidence that a series is still airing.
    counts['series_excluded_invalid_end_year'] += 1
    return 'invalid', None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=RAW_DATA)
    parser.add_argument('--output', type=Path, default=REFINED)
    parser.add_argument('--cutoff-year', type=int, default=2026, help='Last year to include (may be incomplete)')
    parser.add_argument('--ratings-source', type=Path, default=RATINGS_DATA)
    parser.add_argument('--download-date', default='2026-11-12')
    args = parser.parse_args()
    build(args.source, args.output, args.cutoff_year, ratings_source=args.ratings_source, download_date=args.download_date)
