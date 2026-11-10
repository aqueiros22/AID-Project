## Section 5 – Data Analysis and Interpretation

For **Section 5**, the following analytical questions are appropriate for the dimensional model:

- How has the number of releases evolved over time, by year and title type?
- What are the most frequent genre combinations?
- Which titles or series snapshots contain an individual genre, including mixed combinations?
- How does the average known runtime vary by title type and decade?
- How many known series intervals cover each reference year?
- How many series had started by each year but have an unknown end in the extract?
- What percentage of titles have missing runtime information?
- How has the annual number of title releases changed over time?
- Which decades recorded the highest number of releases?
- Which title types experienced the largest year-over-year increase in releases?
- How has the proportion of movies, TV series, and TV episodes changed over time?
- What are the most frequent genre combinations for each title type?
- How have the leading genre combinations changed across decades?
- Which title types have the highest proportion of adult-flagged titles?

The analysis should distinguish between **observed results** and possible explanations. For example, an increase in recorded titles may reflect changes in dataset coverage rather than solely an increase in audiovisual production.

## Section 6 – Critical Discussion

The discussion should address both the advantages and limitations of the dimensional model.

**Advantages:**
- Consistent and standardized measures.
- Shared dimensions across fact tables.
- Simpler analytical queries and reporting.
- Persistent SQLite storage with enforced keys, strict types, checks and validated transactional loading.

**Limitations:**
- Additional storage requirements for periodic snapshots.
- Need to maintain and update ETL processes.
- Loss of certain attributes from the original dataset.
- Individual genre groups overlap; do not sum them into a distinct-title total. Use `EXISTS` for membership filters to avoid duplicates.
- Missing `endYear` leaves airing status indeterminate. Already-started series with unknown ends are reported separately and never added to the known-interval measure as an airing total.
- Annual snapshots include both start and end years and cannot reveal hiatuses or activity on a specific date.

The model contains two fact tables: `fact_title_release` for all eligible title
releases and `fact_series_year_snapshot` for annual intervals of `tvSeries` and
`tvMiniSeries`. The snapshot has two semi-additive measures:
`known_interval_series_count`, for valid recorded intervals covering the year,
and `started_series_unknown_end_count`, for already-started series with missing
end metadata. The second is a cumulative metadata count, not a count of active
series. Neither measure alone, nor their sum, establishes an actual airing total.
Summing a measure across years counts reporting-year memberships rather than
distinct series or observed broadcast years. A cumulative-catalog fact table is
not retained.

Query 2 and its figure must show the two measures separately, without stacked
totals or an airing-status percentage. For example, in 2020 there are 2,606
`tvSeries` with known intervals covering the year and 127,815 started series
whose end is unknown. The latter group's actual airing status is indeterminate.
The known-interval group is incomplete as an actual activity measure and does
not establish that episodes aired every year of the recorded interval.

Individual genres are stored in `dim_genre` and linked to combinations through
`bridge_genre_set_genre`. Both facts retain `genre_set_key`, so the same bridge
supports both processes. A Comedy search includes Comedy,Romance. The bridge is
a relationship table, not a third fact table. Source `endYear` is used by ETL;
the annual snapshot's year is a reference year, not the individual series end.

Since the data source is a TSV extract, avoid making assumptions about the internal operational database architecture of IMDb.

For source selection and ETL, state that the dataset was provided by the course
professor. Justify its suitability through title identifiers, temporal fields,
descriptive categories and sufficient volume. Distinguish manual acquisition
from automated TSV extraction, transformation and SQLite loading. Do not invent
an original extraction date, version or licence. Cite the generated source hash
and size for reproducibility, and report missing/invalid start years, years
outside the cutoff, missing/invalid runtimes and unknown categories separately.
Transformation validates staged files before publication and rolls back ordinary
publication errors; loading publishes a validated database by atomic replacement.
Neither incremental refreshes nor historical source observations are implemented.

The implemented warehouse is `data/warehouse/warehouse.db`, created by `scripts/build_warehouse.py`
using `sql/warehouse_schema.sql`. CSVs are intermediate ETL files. Queries open the
existing database directly; they do not rebuild an in-memory database. The loader
rejects duplicate keys and orphan references, reconciles control totals, and
preserves the previous database if rebuilding fails. `warehouse_metadata` stores
build provenance and validation, and is not a dimensional fact table.
Schema version 2 and snapshot semantics version 2 prevent older assumed-ongoing
outputs from being silently reused. All historical classifications reflect the
current extract's metadata, not historical knowledge or broadcast observations.

## Current Progress and Priorities

**Section 4 is already well developed. The main remaining task is Section 5.**

Once the analytical queries have been implemented and their results interpreted, writing the critical discussion and final conclusion will become significantly easier.
