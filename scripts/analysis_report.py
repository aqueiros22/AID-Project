"""Render the measured results and interpretation of the ten project queries."""
import json
from scripts.paths import PROJECT_ROOT, REFINED
from scripts.runtime_diagnostics import OUTPUT as RUNTIME_REPORT, markdown as runtime_markdown


def main():
    report = json.loads((REFINED / 'validation_report.json').read_text(encoding='utf-8'))
    results = json.loads((PROJECT_ROOT / 'data/analysis_results.json').read_text(encoding='utf-8'))
    diagnostics = json.loads(RUNTIME_REPORT.read_text(encoding='utf-8'))
    if diagnostics['source_provenance']['sha256'] != report['source_provenance']['sha256']:
        raise ValueError('Runtime diagnostics use a different source; rerun scripts.runtime_diagnostics')
    total, rated = report['release_facts'], report['rating_facts']
    lines = ['# Data analysis — IMDb download 12 November 2026', '',
             'Generated from the current ETL controls and executed warehouse queries. '
             'All ratings refer to the current snapshot, with one row per rated title; 2026 and the 2020s are incomplete. '
             'Download date and source hash are ETL provenance, not a dimensional axis; '
             'the warehouse does not retain rating history.', '',
             '## Population and data quality', '', '| Indicator | Value |', '| --- | ---: |']
    indicators = {
        'Source title records': report['source_rows'],
        'Eligible release facts': total,
        'Excluded missing start years': report['excluded_missing_start_year'],
        'Excluded malformed start years': report['excluded_invalid_start_year'],
        'Excluded numeric years outside cutoff': report['excluded_outside_cutoff_year'],
        'Known positive runtimes': report['known_runtime_facts'],
        'Missing runtimes': report['missing_runtime_facts'],
        'Invalid or nonpositive runtimes': report['invalid_runtime_facts'],
        'Unknown genre combinations': report['unknown_genre_facts'],
        'Unknown adult flags': report['unknown_audience_facts'],
        'Series snapshot rows': report['snapshot_facts'],
        'Eligible series': report['series_candidates'],
        'Series with known end': report['series_with_known_end'],
        'Series with unknown end': report['series_with_unknown_end'],
        'Series excluded due to invalid end': report['series_excluded_invalid_end_year'],
        'Source ratings': report['rating_source_rows'],
        'Eligible rating facts': rated,
        'Ratings without eligible title': report['ratings_without_eligible_title'],
        'Eligible titles without rating': total - rated,
        'Votes on eligible titles': report['rating_votes_total'],
    }
    lines += [f'| {key} | {value:,} |' for key, value in indicators.items()]
    lines += ['', f'**Rating coverage is {100 * rated / total:.2f}%** of eligible titles. '
              f'The remaining {100 * (total-rated) / total:.2f}% have no rating in this extract. '
              'A larger catalogue can lower this percentage even while matching more rating records. '
              'Unmatched ratings include titles absent from basics or excluded by the year rules; '
              'this aggregate does not distinguish those causes.', '',
              f'The year dimension spans {report["first_year"]}–{report["cutoff_year"]}. '
              f'Dimension member counts: {report["dimension_members"]}. '
              f'The genre bridge has {report["genre_bridge_rows"]:,} memberships.', '']
    explanations = [
        'Counts are additive; mean duration uses only titles with a positive known runtime. '
        'Changes in runtime may reflect changing metadata coverage as well as title composition. '
        'The 2020s are a partial decade and must not be compared as a completed decade.',
        'Each process is first aggregated by year, avoiding multiplication of facts. '
        'New releases are a flow; interval and unknown-end measures describe different populations. '
        'An unknown end cannot establish ongoing broadcasting. Historical rows use current metadata.',
        'The format breakdown describes adult-flagged titles, not age certifications or audience behaviour. '
        'Changes in format mix cannot establish a causal change in distribution channels.',
        'Missingness varies by type. Runtime comparisons need the known-runtime denominator; '
        'substituting zero would artificially depress averages.',
        'Genre membership includes mixed combinations, while standalone counts include exactly one genre. '
        'The groups overlap, so their counts cannot be added to obtain a distinct movie total.',
        'This comparison uses the same genre bridge on annual series aggregates. '
        'Known intervals do not establish actual annual activity; the separate unknown-end counts '
        'show why known intervals alone are incomplete as an activity proxy.',
        'Coverage differences demonstrate that availability of ratings is uneven. '
        'Mean title rating describes only rated titles; total votes count votes, not unique viewers.',
        'These are current ratings grouped by release decade, not ratings recorded in each decade. '
        'Comparing all rated movies with the 1,000-vote subset tests sensitivity to low-vote titles; '
        'the threshold also changes the population and does not remove selection bias.',
        'Means give each rated movie equal weight within each genre. Coverage and the 1,000-vote '
        'subset should accompany any genre comparison. Mixed-genre movies appear in several groups; '
        'these groups are not independent or additive.',
        'This is a project ranking of movies with at least 10,000 votes, ordered by rating, votes '
        'and identifier. It is not IMDb Top 250. The threshold improves vote support but is an '
        'analytical choice, and does not establish objective quality.',
    ]
    for number, ((title, result), explanation) in enumerate(zip(results.items(), explanations), 1):
        columns, rows = result['columns'], result['rows']
        lines += ['## ' + title, '', explanation, '',
                  '| ' + ' | '.join(columns) + ' |',
                  '| ' + ' | '.join('---' for _ in columns) + ' |']
        for row in rows:
            values = ['—' if v is None else str(v).replace('|', '\\|').replace('\n', ' ') for v in row]
            lines.append('| ' + ' | '.join(values) + ' |')
        lines.append('')
        if number == 1:
            movies = [r for r in rows if r[1] == 'movie']
            if movies:
                row = movies[0]
                lines += [f'The {row[0]}s contain {row[2]:,} eligible movies, with '
                          f'{row[4]}% runtime coverage and a mean known duration of {row[5]} minutes. '
                          'These three quantities describe volume, completeness and duration separately.', '']
            lines += [runtime_markdown(diagnostics), '']
        if number == 2 and rows:
            row = rows[-1]
            lines += [f'For {row[0]}, there are {row[1]:,} new series, {row[2]:,} known intervals '
                      f'covering the year, and {row[3]:,} already-started series with an unknown end. '
                      'The much larger unknown-end group makes an inferred airing total unjustified.', '']
        if number == 3 and rows:
            row = rows[0]
            label, count = max(zip(columns[2:], row[2:]), key=lambda pair: pair[1])
            lines += [f'In the {row[0]}s, {label} contributes {count:,} of {row[1]:,} '
                      f'adult-flagged titles ({100 * count / row[1]:.1f}%). '
                      'This is a share of recorded titles, not viewers or revenue.', '']
        if number == 4 and rows:
            row = rows[0]
            lines += [f'The highest missing-runtime percentage among the reported categories is '
                      f'{row[0]}: {row[3]}%, or {row[2]:,} of {row[1]:,} titles. '
                      'Duration is not equally meaningful or equally documented for all formats.', '']
        if number == 5 and rows:
            row = rows[0]
            lines += [f'{row[0]} leads the reported movie memberships with {row[1]:,} titles, '
                      f'but only {row[2]:,} ({row[3]}%) have that genre alone. '
                      'Filtering exact combinations would omit the remaining mixed-genre titles.', '']
        if number == 6 and rows:
            row = rows[0]
            lines += [f'{row[0]} has {row[1]:,} known intervals in 2010 and {row[2]:,} in 2020; '
                      f'its separate unknown-end count in 2020 is {row[3]:,}. '
                      'This difference describes recorded interval metadata, not proven growth in broadcasting.', '']
        if number == 7 and rows:
            eligible = [r for r in rows if r[1] >= 10000]
            most, least = max(eligible, key=lambda r: r[3]), min(eligible, key=lambda r: r[3])
            lines += [f'Among types with at least 10,000 titles, **{most[0]}** has the highest '
                      f'coverage ({most[3]}%), and **{least[0]}** the lowest ({least[3]}%). '
                      'This is direct evidence against treating the overall rating mean as representative of all types.', '']
        if number == 8:
            gaps = [r for r in rows if r[4] is not None and r[6] is not None]
            if gaps:
                row = max(gaps, key=lambda r: abs(r[6] - r[4]))
                lines += [f'The largest absolute change under the vote threshold occurs in the {row[0]}s: '
                          f'{row[4]:.2f} for all rated movies versus {row[6]:.2f} for movies with at least '
                          f'1,000 votes ({row[5]:,} movies). This is a composition effect, not a before/after change in ratings.', '']
        if number == 10 and rows:
            row = rows[0]
            lines += [f'The first movie under these rules is **{row[1]}** ({row[2]}), '
                      f'rated {row[3]} with {row[4]:,} votes.', '']
    lines += ['## Figures', '']
    for filename in sorted((PROJECT_ROOT / 'media').glob('*.png')):
        lines += [f'![{filename.stem}](../media/{filename.name})', '']
    lines += ['## Interpretation limits', '',
              'The updated catalogue extends recent-year coverage, but is not a production census. '
              'Ratings are available for a selected minority of titles. Release cohorts differ in exposure '
              'time and vote accumulation. No causal claims about genre, quality or popularity follow '
              'from these descriptive comparisons. A future history of separately dated extracts would '
              'be required to measure rating changes over time.', '']
    (PROJECT_ROOT / 'docs/Data_Analysis.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
