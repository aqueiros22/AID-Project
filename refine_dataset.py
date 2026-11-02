"""Build a dimensional title-release dataset using only Python's standard library."""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def build(source, output, cutoff):
    output.mkdir(parents=True, exist_ok=True)
    dimensions = {name: {} for name in ('title_type', 'genre_set', 'audience')}
    annual = defaultdict(lambda: [0, 0])
    counts = Counter()
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
        for row in csv.DictReader(raw, delimiter='\t', quoting=csv.QUOTE_NONE):
            counts['source_rows'] += 1
            year = row['startYear']
            if not year.isdigit() or not 1 <= int(year) <= cutoff:
                counts['excluded_unknown_or_outside_cutoff_year'] += 1
                continue
            year = int(year)
            genre = ','.join(sorted(set(row['genres'].split(',')))) if row['genres'] != '\\N' else 'Unknown'
            audience = {'0': 'Not adult-flagged', '1': 'Adult-flagged'}.get(row['isAdult'], 'Unknown')
            keys = (key('title_type', row['titleType']), key('genre_set', genre), key('audience', audience))
            runtime = row['runtimeMinutes']
            known = runtime.isdigit() and int(runtime) > 0
            minutes = int(runtime) if known else 0
            facts.writerow([row['tconst'], row['primaryTitle'], year, *keys, 1, minutes, int(known)])
            values = annual[(year, *keys)]
            values[0] += 1
            values[1] += minutes
            years.add(year)
            counts['release_facts'] += 1
            counts['known_runtime_facts'] += int(known)
            counts['total_runtime_minutes'] += minutes

    if not years:
        raise ValueError('No eligible titles in the source')
    for name, members in dimensions.items():
        with (output / f'dim_{name}.csv').open('w', encoding='utf-8', newline='') as target:
            rows = writer(target, [f'{name}_key', name])
            rows.writerows((value, label) for label, value in members.items())
    with (output / 'dim_year.csv').open('w', encoding='utf-8', newline='') as target:
        rows = writer(target, ['year_key', 'year', 'decade', 'century', 'snapshot_date'])
        for year in range(min(years), cutoff + 1):
            rows.writerow([year, year, year // 10 * 10, (year - 1) // 100 + 1, f'{year:04d}-12-31'])

    running = defaultdict(lambda: [0, 0])
    by_year = defaultdict(list)
    for (year, *keys), values in annual.items():
        by_year[year].append((tuple(keys), values))
    with (output / 'fact_year_end_catalog.csv').open('w', encoding='utf-8', newline='') as target:
        rows = writer(target, ['year_key', 'title_type_key', 'genre_set_key', 'audience_key', 'catalog_title_count', 'catalog_runtime_minutes'])
        for year in range(min(years), cutoff + 1):
            for keys, values in by_year[year]:
                running[keys][0] += values[0]
                running[keys][1] += values[1]
            for keys, values in sorted(running.items()):
                rows.writerow([year, *keys, *values])
                counts['snapshot_facts'] += 1
    assert counts['release_facts'] > 10000, 'Requirement: more than 10,000 release facts'
    assert sum(value[0] for value in running.values()) == counts['release_facts']
    assert sum(value[1] for value in running.values()) == counts['total_runtime_minutes']
    report = dict(counts)
    report.update(cutoff_year=cutoff, first_year=min(years), dimension_members={name: len(values) for name, values in dimensions.items()}, year_members=cutoff - min(years) + 1)
    (output / 'validation_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data.tsv'))
    parser.add_argument('--output', type=Path, default=Path('refined'))
    parser.add_argument('--cutoff-year', type=int, default=2026, help='Last complete calendar year to include')
    args = parser.parse_args()
    build(args.source, args.output, args.cutoff_year)
