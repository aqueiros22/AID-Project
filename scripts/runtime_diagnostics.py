"""Reproducible distribution and sensitivity analysis of series runtimes."""
import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from scripts.build_warehouse import open_warehouse
from scripts.paths import PROJECT_ROOT, RAW_DATA, WAREHOUSE

OUTPUT = PROJECT_ROOT / 'data' / 'runtime_diagnostics.json'


def percentile(values, probability):
    """Linear interpolation at (n - 1) * p, including the endpoints."""
    position = (len(values) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(values, total_titles):
    """Known positive runtimes only; thresholds are diagnostics, not cleaning rules."""
    values = sorted(values)
    result = dict(total_titles=total_titles, known_titles=len(values),
                  coverage_pct=100 * len(values) / total_titles if total_titles else None)
    if not values:
        return dict(result, mean=None, median=None, p95=None, p99=None,
                    maximum=None, runtime_sum=0, thresholds=[])
    thresholds = []
    for threshold in (120, 240, 1440):
        retained = [v for v in values if v <= threshold]
        removed = len(values) - len(retained)
        thresholds.append(dict(max_minutes=threshold, removed_titles=removed,
                               removed_pct=100 * removed / len(values),
                               mean_retained=statistics.mean(retained) if retained else None))
    return dict(result, mean=statistics.mean(values), median=statistics.median(values),
                p95=percentile(values, .95), p99=percentile(values, .99),
                maximum=values[-1], runtime_sum=sum(values), thresholds=thresholds,
                mean_without_largest=statistics.mean(values[:-1]) if len(values) > 1 else None,
                largest_runtime_share_pct=100 * values[-1] / sum(values))


def investigate(conn, raw_source=None):
    groups = defaultdict(list)
    cursor = conn.execute("""
        SELECT f.title_id, f.primary_title, f.year_key, f.runtime_minutes, f.runtime_known_count
        FROM fact_title_release f JOIN dim_title_type t USING (title_type_key)
        WHERE t.title_type = 'tvSeries' AND f.year_key >= 1980
        ORDER BY f.year_key, f.title_id
    """)
    for row in cursor:
        groups[row[2] // 10 * 10].append(row)
    decades = {str(d): summarize([r[3] for r in rows if r[4]], len(rows))
               for d, rows in sorted(groups.items())}
    largest = sorted((r for r in groups.get(2000, []) if r[4]), key=lambda r: (-r[3], r[0]))[:10]
    targets = {r[0]: r for r in largest}
    raw_matches = {}
    # Literal TSV splitting avoids quote interpretation and scans only until all targets match.
    if raw_source is not None:
        with Path(raw_source).open(encoding='utf-8', newline='') as handle:
            header = handle.readline().rstrip('\r\n').split('\t')
            for line in handle:
                title_id = line.split('\t', 1)[0]
                if title_id in targets:
                    record = dict(zip(header, line.rstrip('\r\n').split('\t')))
                    row = targets[title_id]
                    if (record['titleType'] != 'tvSeries' or int(record['startYear']) != row[2]
                            or int(record['runtimeMinutes']) != row[3]):
                        raise ValueError('Source and warehouse type, year or runtime differ')
                    raw_matches[title_id] = record
                    if len(raw_matches) == len(targets):
                        break
        if len(raw_matches) != len(targets):
            raise ValueError('Could not trace all top runtime titles to the supplied source')
    provenance = json.loads(conn.execute('SELECT source_report_json FROM warehouse_metadata').fetchone()[0])
    return dict(scope='tvSeries released/started from 1980 through the warehouse cutoff',
                percentile_method='linear interpolation at (n - 1) * p',
                source_provenance=provenance['source_provenance'],
                inspected_source=str(Path(raw_source).resolve()) if raw_source is not None else None,
                decades=decades,
                top_2000s=[dict(title_id=r[0], title=r[1], start_year=r[2], runtime_minutes=r[3],
                               raw_record=raw_matches.get(r[0])) for r in largest])


def markdown(report):
    if not report['inspected_source']:
        raise ValueError('Run diagnostics with a raw source before reporting source verification')
    lines = ['### Runtime anomaly investigation', '',
             'Supplement to Query 1, restricted to `tvSeries`. Missing/invalid runtimes are excluded '
             'from all duration statistics and reported through coverage. Percentiles use linear '
             'interpolation at `(n - 1) * p`. No source values or warehouse facts are changed.', '',
             '| Start decade | Eligible series | Known runtimes | Coverage | Mean (min) | Median (min) | P95 | P99 | Maximum |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for decade, row in report['decades'].items():
        lines.append(f"| {decade}s | {row['total_titles']:,} | {row['known_titles']:,} | "
                     f"{row['coverage_pct']:.2f}% | {row['mean']:.2f} | {row['median']:.2f} | "
                     f"{row['p95']:.2f} | {row['p99']:.2f} | {row['maximum']:,} |")
    row = report['decades']['2000']
    top = report['top_2000s'][0]
    lines += ['', f"The largest recorded value is **{top['title']}** (`{top['title_id']}`, "
              f"start year {top['start_year']}): **{top['runtime_minutes']:,} minutes**. "
              f"This one record accounts for **{row['largest_runtime_share_pct']:.2f}%** of the "
              f"2000s known-runtime sum. Removing only this largest observation for comparison "
              f"reduces the mean from **{row['mean']:.2f} to {row['mean_without_largest']:.2f} minutes**. "
              f"The median is **{row['median']:.0f} minutes**.", '',
              'The top ten values were checked against `title.basics.tsv`; their type, start year '
              'and runtime match the warehouse. The extreme value therefore originates in the '
              'source, rather than the ETL conversion. This check does not establish whether the '
              'source value is erroneous, cumulative, or uses another interpretation of series '
              'duration. It is not justified to label it a normal episode length or silently replace it.', '',
              '| 2000s sensitivity scenario | Observations removed | Share removed | Mean remaining (min) |',
              '| --- | ---: | ---: | ---: |']
    for test in row['thresholds']:
        lines.append(f"| Keep runtimes <= {test['max_minutes']:,} min | {test['removed_titles']:,} | "
                     f"{test['removed_pct']:.2f}% | {test['mean_retained']:.2f} |")
    lines += ['', 'The 120, 240 and 1,440 minute limits are illustrative sensitivity thresholds '
              '(two hours, four hours and one day), not validated definitions of a correct series runtime. '
              'Their differing results show why an arbitrary cutoff cannot be presented as a correction. '
              'For comparisons, report median, upper percentiles, coverage and the unmodified mean together.', '',
              '| Title ID | Series | Start year | Source and warehouse runtime (min) |',
              '| --- | --- | ---: | ---: |']
    for top in report['top_2000s'][:5]:
        title = top['title'].replace('|', '\\|')
        lines.append(f"| {top['title_id']} | {title} | {top['start_year']} | {top['runtime_minutes']:,} |")
    lines += ['', 'Reproduce with `python -m scripts.runtime_diagnostics`. '
              'The complete statistics and original source records for the ten largest values are '
              'stored in `data/runtime_diagnostics.json`.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=WAREHOUSE)
    parser.add_argument('--source', type=Path, default=RAW_DATA)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    conn = open_warehouse(args.database)
    try:
        result = investigate(conn, args.source)
    finally:
        conn.close()
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(markdown(result))
