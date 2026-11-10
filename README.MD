# Dimensional Analysis of IMDb Audiovisual Titles

The project dataset was provided by the course professor and is available from
[Google Drive](https://drive.google.com/file/d/1vTW1wmApo5b8ZvQGzIOJ8E2XF4GIFgJc/view?usp=sharing).

The implemented warehouse is the persistent local SQLite file `data/warehouse/warehouse.db`.
Its schema is defined in [sql/warehouse_schema.sql](sql/warehouse_schema.sql), with
primary keys, foreign keys, strict types and checks enforced during loading.
The CSV files in `data/refined/` are intermediate ETL outputs. Queries open the
existing database directly and do not recreate it or reload the CSV files.

## Project Structure

```text
projeto/
├── README.MD
├── scripts/                  # Transformation, loading, queries and shared paths
├── tests/                    # Transformation and warehouse tests
├── sql/                      # Persistent warehouse schema
├── docs/                     # Report guidelines
├── data/
│   ├── raw/                  # Source data.tsv
│   ├── refined/              # Generated CSVs and ETL validation report
│   └── warehouse/            # Persistent warehouse.db
└── media/                    # Analytical figures
```



## 1. Subject Description and Assignment Requirements

### 1.1. Subject and Objectives

This project analyses the evolution of audiovisual titles represented in an
IMDb metadata extract. It examines releases by year, known series intervals
and missing end-year metadata, distinguishing title types, genre combinations, and adult
content flags.

The model supports two analytical processes: counting titles in their release
or start year, and reconstructing annual snapshots of series interval information.
The second process separately counts known start/end intervals that cover the
reference year and already-started series with an unknown end year.

**Unknown means indeterminate:** a missing `endYear` does not imply that a series
is still airing. Such titles contribute only to a cumulative count of series
already started with missing end information. The two measures are reported
separately and are not combined into an estimate of series in airing. A known
interval covering a year also does not prove that episodes aired in that year.

The main analytical questions are:

- How has the annual number of released titles evolved?
- Which title types and genre combinations are most represented?
- How are releases distributed by adult content flag?
- What are the total known runtime and average known runtime by category and year?
- How many known series intervals cover each year, by type and genre combination?
- How many series had started by each year but have an unknown end year in the extract?

### 1.2. Data Source and Scope

The source is the IMDb title metadata dataset provided by the course professor,
stored locally as `data/raw/data.tsv`, containing 7,697,093 records and the
fields `tconst`, `titleType`, `primaryTitle`, `originalTitle`, `isAdult`,
`startYear`, `endYear`, `runtimeMinutes`, and `genres`.

The file contains title metadata; it does not contain sales, revenue, viewing
figures, platform availability, or ratings. Analysis therefore focuses on the
number and characteristics of the titles present in this extract. The
original extraction date and extract version were not recorded in the project;
neither is inferred from the file's modification time. The ETL records the source
file's SHA-256 hash and byte size so that subsequent runs can identify the exact
input used.

The supplied source was selected for its fit with the analytical objectives:
title identifiers support title-level facts, dates support temporal analysis,
and title types, genres and adult flags support shared dimensions. Its volume
also supports the assignment's fact-count requirement. One source is sufficient
for this scope; adding ratings or financial sources would require additional
questions and explicit integration rules. Coverage is not assumed to represent
all worldwide audiovisual production, and films, series and episodes must be
distinguished when interpreting totals.

Acquisition is manual: obtain the professor-provided file and place it at
`data/raw/data.tsv`. Extraction is automated from that local TSV; the project
does not scrape IMDb or connect to its internal database. Refreshes rerun the
complete transformation and loading process rather than updating incrementally.

Records are included when `startYear` is numeric and falls between 1 and the
configured cutoff year. For series, this field represents the start year.
Titles without an eligible year are excluded without inventing dates. All
eligible title types are retained, including movies, series, and episodes.

The series snapshot includes only `tvSeries` and `tvMiniSeries`; individual
episodes, movies and other title types remain available in release facts.
Both start and end years are included in a known interval. Missing end years
(`\N` or an empty field) remain unknown: the series is included in the separate
already-started/unknown-end metadata count from its start through the reporting
cutoff. No end date or continued broadcasting is inferred.
Malformed end years and end years earlier than the start are excluded from the
snapshot and counted in the validation report; their release facts are retained.

The current results, recorded in [validation_report.json](data/refined/validation_report.json), are:

| Indicator | Value |
| --- | ---: |
| Source records | 7,697,093 |
| Release fact table records | 6,901,413 |
| Records excluded due to unknown or out-of-range years | 795,680 |
| Records excluded due to missing start years | 795,670 |
| Records excluded due to malformed start years | 0 |
| Records excluded due to numeric start years outside the range | 10 |
| Titles with known positive runtime | 2,138,467 |
| Eligible titles with missing runtime | 4,762,936 |
| Eligible titles with invalid or nonpositive runtime | 10 |
| Eligible titles with unknown genres | 539,459 |
| Eligible titles with unknown adult content flag | 0 |
| Total known runtime, in minutes | 94,369,177 |
| Series snapshot fact table records | 66,301 |
| Eligible series (`tvSeries` and `tvMiniSeries`) | 224,004 |
| Series with known end year | 73,634 |
| Series with unknown end year, airing status indeterminate | 150,370 |
| Series excluded from snapshots due to invalid end years | 0 |
| Time range | 1874–2026 |

The current cutoff is 2026. As of this model revision, 10 November 2026, this
year is still incomplete. Each snapshot describes the annual interval, not the
status on 31 December. The `snapshot_date` attribute is only a conventional
label for the year. To exclude the incomplete calendar year, regenerate with
`--cutoff-year 2025`; this does not resolve missing or outdated source metadata.

### 1.3. Mapping to Assignment Requirements

| Assignment requirement | Implementation |
| --- | --- |
| More than 10,000 facts | `fact_title_release` contains 6,901,413 records. |
| At least one additive measure | `title_count`, `runtime_minutes`, and `runtime_known_count` in the release fact table. |
| Aggregated facts or snapshots with a semi-additive measure | `fact_series_year_snapshot` separately counts known intervals covering a year and already-started series with unknown end, additive across disjoint categories within a year but not across years as distinct-series counts. |
| At least four dimensions, including a temporal dimension | Year, title type, genre combination, and adult content flag are directly referenced; individual genre is an additional dimension reached through a bridge. |
| Dimensions shared by both types of facts | The four direct dimensions are shared, as are the genre dimension and membership bridge through `genre_set_key`. |

The snapshots represent **annual series interval information**, using dates
and missing-end metadata in the extract. They do not establish
that episodes aired in every included year or describe what IMDb knew at each
historical date. Historically observed operational snapshots would require
an additional source with dated observations.

## 2. Dimensional Planning

### 2.1. Dimensional Bus Matrix

The matrix relates analytical processes to conformed dimensions: dimensions
with the same members, keys, and meaning in both processes.

| Process / fact table | Year | Title type | Genre combination | Adult content flag | Individual genre |
| --- | :---: | :---: | :---: | :---: | :---: |
| Releases — `fact_title_release` | X | X | X | X | Via bridge |
| Annual series interval information — `fact_series_year_snapshot` | X | X | X | X | Via bridge |

In the first table, the year identifies the title's release/start year. In the
second, it identifies the snapshot reference year. The dimension is shared,
but its temporal role depends on the process being analysed.
The title-type dimension is shared in full, but only its `tvSeries` and
`tvMiniSeries` members are referenced by the series snapshot.

### 2.2. Dimension Dictionary

The following logical types are implemented by [sql/warehouse_schema.sql](sql/warehouse_schema.sql).
SQLite `STRICT` tables use `INTEGER` for integer measures and `TEXT` for labels
and ISO-8601 dates; SQLite integers support the logical `BIGINT` measures below.
Category keys are integers assigned by the transformation process. The CSV
exports themselves do not enforce constraints; the persistent warehouse does.

#### Time Dimension — `dim_year`

Grain: one record per year, including years without releases between the
first eligible year and the configured cutoff. It currently contains 153 members.

| Attribute | Logical type | Role | Description |
| --- | --- | --- | --- |
| `year_key` | INTEGER | PK | Numeric natural key for the year. |
| `year` | INTEGER | Attribute | Calendar year. |
| `decade` | INTEGER | Attribute | First year of the decade, calculated using integer division; for example, 1990. |
| `century` | INTEGER | Attribute | Calendar century; for example, 2000 belongs to the 20th century and 2001 to the 21st. |
| `snapshot_date` | DATE | Attribute | Conventional label of 31 December for the reference year; not an observed airing date or year-end status. |

Supports aggregation from year to decade or from year to century. Decade and
century are alternative groupings: the decade convention used can cross a
century boundary. A strict decade → century hierarchy is not assumed.
The source used does not provide release months or days.

#### Title Type Dimension — `dim_title_type`

Grain: one record per source title type. It currently contains 13 members.

| Attribute | Logical type | Role | Description |
| --- | --- | --- | --- |
| `title_type_key` | INTEGER | PK | Surrogate key for the title type. |
| `title_type` | TEXT | Attribute | Original category, such as `movie`, `short`, or `tvEpisode`. |

#### Genre Combination Dimension — `dim_genre_set`

Grain: one record per distinct genre combination after sorting and removing
duplicates. It currently contains 2,244 members, including the `Unknown` member
for unknown genres.

| Attribute | Logical type | Role | Description |
| --- | --- | --- | --- |
| `genre_set_key` | INTEGER | PK | Surrogate key for the combination. |
| `genre_set` | TEXT | Attribute | Normalised genre list, such as `Comedy,Romance`, or `Unknown`. |

Each title belongs to exactly one combination. Analysis of `Comedy,Romance`
refers to that exact combination, rather than all comedy or romance titles.
Individual genre filtering uses the following dimension and bridge while
retaining this original combination and the grain of both fact tables.

#### Individual Genre Dimension — `dim_genre`

Grain: one record per distinct genre label, plus `Unknown` for the unknown
combination. There are 29 members, including `Unknown`. Labels retain the source's English names, such as `Comedy` and
`Romance`; searching for comedy uses `Comedy`.

| Attribute | Logical type | Role | Description |
| --- | --- | --- | --- |
| `genre_key` | INTEGER | PK | Surrogate key assigned in sorted label order. |
| `genre` | TEXT | Attribute | One individual genre label or the `Unknown` member. |

#### Genre Membership Bridge — `bridge_genre_set_genre`

Grain: one record per distinct combination × constituent genre, with 6,331
memberships in the current extract. The composite
primary key is `(genre_set_key, genre_key)`. This is a relationship table,
not a third fact table or a title-level bridge containing millions of rows.

| Attribute | Logical type | Role | Description |
| --- | --- | --- | --- |
| `genre_set_key` | INTEGER | PK component; FK → `dim_genre_set` | Genre combination. |
| `genre_key` | INTEGER | PK component; FK → `dim_genre` | One constituent genre. |

For example, `Comedy,Romance` has two memberships: `Comedy` and `Romance`.
Searching for `Comedy` therefore includes both `Comedy` and `Comedy,Romance`.
The `Unknown` combination has one membership to the `Unknown` genre member.

**Aggregation rule:** filters use `EXISTS`, so each fact row is selected once
even when multiple selected genres match. Selecting several genres uses OR
(any match). When reporting separately by individual genre, a hybrid title
contributes its full count to each applicable genre. Genre totals overlap and
must not be added together as a distinct-title total; runtime totals have the
same overlap. Exact genre combinations remain disjoint and additive.

#### Adult Content Flag Dimension — `dim_audience`

Grain: one record per interpreted value of `isAdult`. It currently contains
two observed members; the process also supports an `Unknown` member.

| Attribute | Logical type | Role | Description |
| --- | --- | --- | --- |
| `audience_key` | INTEGER | PK | Surrogate key for the classification. |
| `audience` | TEXT | Attribute | `Not adult-flagged`, `Adult-flagged`, or `Unknown`. |

This indicator is not an age rating and does not establish that an unflagged
title is suitable for children.

### 2.3. Fact Dictionary

#### Release Facts — `fact_title_release`

Grain: one record per source title with an eligible year. The primary key is
`title_id`, corresponding to `tconst`. SQLite enforces uniqueness and rejects
duplicate identifiers during warehouse loading.

| Attribute | Logical type | Role | Description / aggregation |
| --- | --- | --- | --- |
| `title_id` | TEXT | PK | IMDb title identifier, unique and non-null. |
| `primary_title` | TEXT | Descriptive | Main title name; not unique. |
| `year_key` | INTEGER | FK → `dim_year` | Release/start year. |
| `title_type_key` | INTEGER | FK → `dim_title_type` | Title type. |
| `genre_set_key` | INTEGER | FK → `dim_genre_set` | Genre combination. |
| `audience_key` | INTEGER | FK → `dim_audience` | Adult content flag. |
| `title_count` | INTEGER | Additive measure | Value of 1 per record; aggregated by summation. |
| `runtime_minutes` | BIGINT | Additive measure | Known positive runtime in minutes; zero when unknown or invalid. |
| `runtime_known_count` | INTEGER | Additive measure | Value of 1 when valid positive runtime exists, and 0 otherwise. |

The identifier acts as a degenerate dimension: it identifies the title without
creating an additional dimension containing millions of members. The name is
retained as a descriptive attribute in the detail fact table.

Average known runtime is a derived **non-additive** measure:

```text
average known runtime = SUM(runtime_minutes) / SUM(runtime_known_count)
```

Use decimal division and return null when the denominator is zero. Group
averages must not be summed or combined using an unweighted average of
averages; they must be recalculated from totals and counts.

#### Annual Series Interval Information — `fact_series_year_snapshot`

Grain: one record per reference year × series type × genre combination × adult
content flag with at least one nonzero measure. The logical primary
key combines the four foreign keys. Absent combinations represent zero.

| Attribute | Logical type | Role | Description / aggregation |
| --- | --- | --- | --- |
| `year_key` | INTEGER | PK component; FK → `dim_year` | Snapshot reference year. |
| `title_type_key` | INTEGER | PK component; FK → `dim_title_type` | `tvSeries` or `tvMiniSeries`. |
| `genre_set_key` | INTEGER | PK component; FK → `dim_genre_set` | Genre combination. |
| `audience_key` | INTEGER | PK component; FK → `dim_audience` | Adult content flag. |
| `known_interval_series_count` | BIGINT | Semi-additive measure | Series with valid known end year and `startYear <= reference year <= endYear`. |
| `started_series_unknown_end_count` | BIGINT | Semi-additive measure | Series with `startYear <= reference year` and missing end in the extract; actual airing status is indeterminate. |

Each measure can be summed across series types, genre combinations and
classifications within the same year. **Do not combine the measures as an airing
total or use their sum as the denominator of an airing-status percentage.** They
have different inclusion rules: the first describes known intervals covering a
year; the second accumulates already-started titles with missing end metadata.
The first is not the total number actually airing either: it excludes titles
whose intervals cannot be established. A known end beyond the cutoff remains
known and is evaluated only within the reporting years.

Do not sum annual values of either measure to obtain distinct series over a
period: a series can appear in several years. Such sums are reporting-year
memberships, not observed years of broadcast activity or distinct-title totals.
The snapshot contains no runtime measure because title runtime does not measure
the amount of content aired in a particular year.

## 3. Dimensional Data Model Explained

### 3.1. Structure and Relationships

The model is a **fact constellation**, with two facts sharing four directly
referenced dimensions. Each fact row references one member of each direct
dimension. An additional individual-genre dimension is connected through the
combination membership bridge, allowing both facts to be filtered by genre.

```mermaid
erDiagram
    dim_year ||--o{ fact_title_release : "release year"
    dim_title_type ||--o{ fact_title_release : "type"
    dim_genre_set ||--o{ fact_title_release : "combination"
    dim_audience ||--o{ fact_title_release : "flag"
    dim_year ||--o{ fact_series_year_snapshot : "reference year"
    dim_title_type ||--o{ fact_series_year_snapshot : "series type"
    dim_genre_set ||--o{ fact_series_year_snapshot : "combination"
    dim_audience ||--o{ fact_series_year_snapshot : "flag"
    dim_genre_set ||--|{ bridge_genre_set_genre : "contains"
    dim_genre ||--o{ bridge_genre_set_genre : "member of"

    dim_year {
        int year_key PK
        int year
        int decade
        int century
        date snapshot_date
    }
    dim_title_type {
        int title_type_key PK
        text title_type
    }
    dim_genre_set {
        int genre_set_key PK
        text genre_set
    }
    dim_genre {
        int genre_key PK
        text genre
    }
    bridge_genre_set_genre {
        int genre_set_key PK, FK
        int genre_key PK, FK
    }
    dim_audience {
        int audience_key PK
        text audience
    }
    fact_title_release {
        text title_id PK
        text primary_title
        int year_key FK
        int title_type_key FK
        int genre_set_key FK
        int audience_key FK
        int title_count
        bigint runtime_minutes
        int runtime_known_count
    }
    fact_series_year_snapshot {
        int year_key PK, FK
        int title_type_key PK, FK
        int genre_set_key PK, FK
        int audience_key PK, FK
        bigint known_interval_series_count
        bigint started_series_unknown_end_count
    }
```

The diagram represents the relational schema implemented in `data/warehouse/warehouse.db`.
SQLite enforces the primary keys, foreign keys, nullability, strict physical
types and measure checks. An additional `warehouse_metadata` table records
the build timestamp, source report and database validation results; it is
operational metadata, not a third dimensional fact table.

### 3.2. Rationale for Design Decisions

**Separating the two processes.** The release fact table answers questions
about what was released in a given year. The snapshot separately describes known
intervals covering that year and already-started series with missing end metadata.
A known-ended series leaves the interval count after its end year; unknown-ended
titles remain in a metadata accumulation without being labelled as airing.
The snapshot uses `endYear`, which cannot be recovered from release counts.

**Two fact tables.** A separate cumulative-catalog fact is not retained. Cumulative
release counts can be calculated from release facts if needed. The stored snapshot
instead supports the distinct analysis of series lifecycles. It still materialises
derived data, with storage and maintenance costs, but adds information about ends.

**Conformed dimensions.** Both processes use the same keys and descriptions,
allowing new series, known intervals and missing-end metadata to be analysed with the same filters.
For this comparison, release facts must be restricted to `tvSeries` and
`tvMiniSeries`. There is no direct relationship between the fact tables.
Comparing them requires first aggregating each table to the desired common
grain, avoiding row multiplication caused by a direct join.

**Genre combinations and individual genres.** Combinations remain the fact
foreign keys, preserving disjoint categories. The combination-to-genre bridge
provides membership filtering without expanding either fact table. `EXISTS`
prevents duplicate rows in OR filters. Reports grouped by individual genre have
overlapping categories, so their totals must not be summed into an overall count.

**The role of `endYear`.** Source end years are used during ETL to establish
series interval membership. They do not require another dimension for the
current aggregated snapshot: its year key is the reference year, not the end
year of every series in a group. One group can contain series with different
ends. Individual end years are not retained in the facts. Exposing them for
title-level analysis would require detail storage, such as an `end_year_key`
referencing the existing `dim_year` in the role of end year (with explicit
unknown handling and coverage of known ends beyond the cutoff). This would
reuse the temporal dimension rather than add an `endYear` attribute to each
snapshot category or create a separate end-year dimension.

**Annual temporal precision.** Start and end dates have year precision, so the
time dimension has an annual grain. The 31 December label identifies the
reference year, not an actual release date or status on that day. The snapshot
includes a series that ended at any point in the reference year.

**Unknown runtime.** Unknown, non-numeric, or non-positive values contribute
zero to total runtime and zero to `runtime_known_count`. This zero is an
aggregation convention; it does not mean that the title actually lasts zero minutes.

### 3.3. Snapshot Construction and Interpretation

For eligible `tvSeries` and `tvMiniSeries`, the two measures have separate rules:

```text
known_interval_series_count:
    valid known endYear and startYear <= reference_year <= endYear

started_series_unknown_end_count:
    missing endYear and startYear <= reference_year
```

The cutoff limits reporting years; it is not an inferred end date. Missing
`endYear` values (`\N` or empty) remain unknown. Malformed non-missing ends or
ends before the start are excluded from the snapshot and logged separately.
Their release facts remain available.

For known intervals, the ETL adds one at the start and subtracts one in the year
after the end. For missing ends, it accumulates already-started titles as a
metadata count. These titles remain in that count through the reporting cutoff,
without asserting that they continue airing. Groups are aggregated by the four
foreign keys; groups with both measures zero are omitted.

For example, series A starts in 2018 and ends in 2020; series B starts in 2019
and has no end year. With a 2021 reporting cutoff and the same categories:

| Year | New series releases | Known interval covers year | Started with unknown end; status indeterminate |
| --- | ---: | ---: | ---: |
| 2018 | 1 | 1 | 0 |
| 2019 | 1 | 1 | 1 |
| 2020 | 0 | 1 | 1 |
| 2021 | 0 | 0 | 1 |

In 2021, A's known interval no longer covers the year. B has started and its end
is unknown; this does not establish whether it was airing in 2021. No combined
airing total is produced. Summing either column across years counts repeated
reporting-year memberships, not distinct series or observed broadcast years.
Known/unknown end classification reflects the current extract's information,
not what was known at each historical date.

### 3.4. Analytical Limitations

- Snapshots reconstruct annual series intervals from the extract; they are not
  observations of actual broadcasting schedules or historical IMDb membership.
- Missing end years leave airing status indeterminate. Their cumulative metadata
  count cannot establish how many of those series were actually airing.
- A known end year removes a series from later known-interval counts, while its
  release fact remains. Gaps, hiatuses and revivals are not represented.
- Coverage depends on the extract and on known years. Counts do not establish
  the total volume of worldwide audiovisual production.
- The year 2026 is incomplete, and the extract does not establish that all
  titles assigned to that year had actually been released by the preparation date.
- Movies, series, and episodes are distinct entities in the source. Specific
  analyses should filter by type. Summing runtimes across series and episodes
  does not necessarily represent minutes of unique content.
- The adult content flag does not replace an age rating.

## 4. Data Extraction, Transformation, Loading, and Validation

The [scripts/refine_dataset.py](scripts/refine_dataset.py) script uses only Python's standard
library (Python 3.11+ for source hashing) and preserves the source file. The process:

1. Reads `data/raw/data.tsv` and selects records with eligible years.
2. Normalises genre combinations and interprets the adult content flag.
3. Assigns category keys and writes release facts.
4. Generates the four direct dimensions, individual genres and the combination-to-genre bridge.
5. Writes known-interval counts and separate cumulative counts of started series with unknown end, without imputing airing status.
6. Checks the fact threshold, nonnegative counts, reporting-year memberships and final-year counts against each measure's source rules.

Then [scripts/build_warehouse.py](scripts/build_warehouse.py) loads these outputs into the
persistent database, using only Python's standard library and SQLite 3.37+.

Run in PowerShell from the project directory:

```powershell
# Reproduce the current version's cutoff
.\venv\Scripts\python.exe -m scripts.refine_dataset --cutoff-year 2026

# Load the persistent database after generating CSVs
.\venv\Scripts\python.exe -m scripts.build_warehouse

# Verify an existing database without rebuilding it or reading CSVs
.\venv\Scripts\python.exe -m scripts.build_warehouse --verify

# Run analytical queries without matplotlib or figure generation
.\venv\Scripts\python.exe -m scripts.run_queries --no-plots

# Boundary and reconciliation tests (small synthetic dataset)
.\venv\Scripts\python.exe -m unittest discover -s tests -v

# Execute the six queries and regenerate figures using Python with matplotlib
python -m scripts.run_queries

# Search individual genres while retaining each title's full genre combination
.\venv\Scripts\python.exe -m scripts.run_queries --genre Comedy --snapshot-year 2020

# OR search: each matching title or snapshot row contributes once
.\venv\Scripts\python.exe -m scripts.run_queries --genre Comedy --genre Romance --snapshot-year 2020

# Optional separate version with an earlier cutoff
.\venv\Scripts\python.exe -m scripts.refine_dataset --cutoff-year 2025 --output data/refined_2025
.\venv\Scripts\python.exe -m scripts.build_warehouse --source data/refined_2025 --database data/warehouse/warehouse_2025.db
.\venv\Scripts\python.exe -m scripts.run_queries --database data/warehouse/warehouse_2025.db --no-plots
```

The transformation first generates and validates files in a temporary directory
next to `data/refined/`. Failed transformation or validation leaves existing
outputs intact. Publication replaces generated files with rollback on ordinary
file-operation errors and preserves unrelated files. This is not an atomic
multi-file transaction against process termination or power loss; transformation
and loading must run sequentially. It does not update the database
until `scripts/build_warehouse.py` is run. Queries default to `data/warehouse/warehouse.db`, with
`--database` available for another version. The default generated database is
ignored by Git and should be regenerated using these commands when reproducing
the project.

### 4.1. Persistent Database and Integrity

The loader creates a temporary database next to the destination, applies the
schema, and loads all five dimensions before the bridge and facts. All data
inserts and index creation occur in one transaction with
`PRAGMA foreign_keys = ON`. It creates indexes for release type/year and genre
filtering, then runs `ANALYZE` to support query planning.

Enforced rules include dimension primary keys and unique labels, the release
`title_id` primary key, the four-key snapshot primary key, the two-key bridge
primary key, and foreign keys for every dimensional reference. Checks reject
negative counts/runtimes, a release count other than one, inconsistent runtime
availability, and empty snapshot groups. Triggers reject snapshot inserts or
type changes referring to title types other than series and miniseries.

Before publishing the new database, the loader runs `PRAGMA foreign_key_check`
and `PRAGMA integrity_check`, checks all table counts against the ETL report,
and reconciles release measures, snapshot reporting-year memberships and cutoff-year totals.
It also verifies the full genre membership mapping and series-type scope.
Build metadata and validation results are stored inside `warehouse_metadata`.

Only a successfully committed and validated database replaces `data/warehouse/warehouse.db`.
If loading or validation fails, the temporary build is removed and the previous
database is preserved. Queries use a read-only connection to the existing file;
a missing database produces an error instead of silently creating an empty one.
Closing the query process leaves the warehouse on disk. `--verify` repeats
database checks using its internally stored source report, without the CSVs.

Any other SQLite client that opens a writable connection must also execute
`PRAGMA foreign_keys = ON`, because SQLite controls foreign-key enforcement
per connection. The project's connection helper enables it for every connection.

The current database uses schema version 2 and the ETL report records
`snapshot_semantics_version: 2`. Version 1 databases or reports are rejected so
old assumed-ongoing semantics cannot be silently reused. To upgrade, regenerate
CSVs with `python -m scripts.refine_dataset` and rebuild with
`python -m scripts.build_warehouse`; normal queries never mutate the schema.

### 4.2. Intermediate Files and Transformation Checks

| Generated file | Contents |
| --- | --- |
| `dim_year.csv` | Time dimension. |
| `dim_title_type.csv` | Title types. |
| `dim_genre_set.csv` | Genre combinations. |
| `dim_genre.csv` | Individual genre labels, including `Unknown`. |
| `bridge_genre_set_genre.csv` | Unique combination-to-genre memberships. |
| `dim_audience.csv` | Adult content flags. |
| `fact_title_release.csv` | Facts per title. |
| `fact_series_year_snapshot.csv` | Known intervals covering each year and started series with unknown end, reported separately. |
| `validation_report.json` | Counts, exclusions, and control totals. |

The persistent result is `data/warehouse/warehouse.db` under `data/warehouse/`; the SQL schema and
Python loader are versioned so it can be rebuilt from the intermediate files.

The script checks that there are more than 10,000 release facts. Known-interval
memberships reconcile against inclusive interval lengths within the reporting
range. Unknown-end memberships reconcile against the number of reporting years
from each start through the cutoff, without inferring a real end. Each final-year
measure is checked separately. These are technical reconciliations, not distinct
series or observed broadcast-year metrics. Invalid end years are counted separately.

The validation report separates missing start years, malformed start years and
numeric years outside the configured range. For eligible releases it separately
counts missing and invalid/nonpositive runtimes, unknown genres and unknown adult
flags. These quality counters describe retained facts, while start-year exclusions
describe rejected source rows. Empty genre labels and `\\N` are treated as missing;
genre labels are trimmed, deduplicated and sorted. Required TSV columns and record
widths are checked before processing. Source provenance includes the professor as
provider, the input path, byte size and SHA-256 hash. Unknown extraction dates and
versions remain explicitly null. Reports generated before these additions must
be regenerated to include the new fields.

The synthetic tests check inclusive boundaries, unknown ends, known ends beyond
the cutoff, invalid intervals, non-series exclusion, an empty snapshot, dimension
references and unique snapshot keys. Database tests additionally verify enforced
primary and foreign keys, checks and types, persistence after CSV removal,
read-only queries, and preservation of the previous database after duplicate
input or inconsistent control totals. Surrogate keys depend on source reading
order; dimensions and facts must be loaded from the same execution.
After successful generation, the script removes the obsolete generated
`fact_year_end_catalog.csv`. The warehouse loader explicitly loads the five
dimensions, membership bridge and two fact tables. The query script opens the
persistent result directly.

Genre membership tests also cover mixed genres, duplicate source labels,
case-insensitive filtering, OR searches without double counting, unknown genres
and the same filter applied to the annual snapshot. Search values are passed
as SQL parameters. The CLI prints matching release counts, series interval information
for the selected year as two separate counts, and up to 20 matching titles with their full combination.

For example, a title search for `Comedy` can use this SQL. Replace the literal
with a bound parameter in application code. To search any of several genres,
use `IN ('Comedy', 'Romance')` inside the same `EXISTS` clause.

```sql
SELECT f.title_id, f.primary_title, s.genre_set
FROM fact_title_release f
JOIN dim_genre_set s ON s.genre_set_key = f.genre_set_key
WHERE EXISTS (
    SELECT 1
    FROM bridge_genre_set_genre b
    JOIN dim_genre g ON g.genre_key = b.genre_key
    WHERE b.genre_set_key = f.genre_set_key
      AND g.genre = 'Comedy'
);
```

The same membership predicate applies to `fact_series_year_snapshot` via its
`genre_set_key`. Filter to one reference year before summing snapshot measures.

## 5. Querying and Data Analysis

This section uses the dimensional model to extract insights from the IMDb metadata. Six queries are run directly against the persistent SQLite warehouse (`python -m scripts.run_queries`). Queries 2 and 6 use the series snapshot; Queries 5 and 6 use the individual-genre bridge; Query 2 combines both fact tables.

### 5.1. Analytical Questions Addressed

1. How have the volume of releases and the average known runtime evolved across decades for movies and TV series?
2. How do the number of new series, the known series intervals covering each year and the cumulative series with an unknown end year compare over time?
3. Which title types make up the adult-flagged content, and how does that mix change by decade?
4. How complete is the runtime information, per title type?
5. How many movies belong to each individual genre, and how many carry that genre alone?
6. How do known series intervals differ by individual genre between 2010 and 2020?

### 5.2. Data Interpretation and Insights

Observed results are separated from possible explanations. One fact conditions every temporal result below: **the extract thins out after 2021**.

| Release year | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Titles in `fact_title_release` | 365,347 | 317,638 | 70,560 | 2,155 | 217 | 54 |

Years after 2021 are therefore not comparable with earlier ones, and the "2020s" decade is effectively 2020–2021. The 2026 cutoff only bounds the reporting years; it does not mean the data is current to 2026.

**A. Release Volume and Average Runtime (Query 1)**

`fact_title_release` is grouped by decade and title type. The average is `SUM(runtime_minutes) / SUM(runtime_known_count)`, so titles without runtime do not distort it.

| Decade | Type | Releases | With known runtime | Avg. known runtime (min) |
| --- | --- | ---: | ---: | ---: |
| 1980s | movie | 42,495 | 71.5% | 92.0 |
| 1980s | tvSeries | 10,453 | 55.1% | 52.3 |
| 1990s | movie | 44,061 | 69.9% | 92.3 |
| 1990s | tvSeries | 18,586 | 51.6% | 52.0 |
| 2000s | movie | 72,759 | 80.9% | 89.4 |
| 2000s | tvSeries | 41,144 | 49.9% | 51.1 |
| 2010s | movie | 155,803 | 80.7% | 89.9 |
| 2010s | tvSeries | 89,632 | 35.4% | 40.9 |
| 2020s (2020–21) | movie | 23,381 | 54.0% | 89.7 |
| 2020s (2020–21) | tvSeries | 11,873 | 28.1% | 44.4 |

-   **Observed results:** the average runtime of movies is stable (89–92 minutes) across five decades. For TV series it stays near 51–52 minutes until the 2000s, then drops to about 41–44 minutes in the 2010s and 2020s. Series releases grow from 10,453 to 89,632 between the 1980s and the 2010s.
-   **Interpretation:** the drop coincides with a fall in runtime coverage for series (55.1% in the 1980s to 28.1% in 2020–21), so the 2010s average describes a smaller and possibly different subset of series. A shift towards shorter formats (web or short-form series) is a plausible explanation, but the model cannot confirm it: `tvSeries` is not split by format and most recent series have no runtime. The result should not be read as "television episodes became shorter".

![Average Known Runtime by Decade](media/1_runtime_evolution.png)

**B. New Series, Known Intervals and Missing End Years (Query 2, drill-across)**

This query uses both fact tables. Each is first aggregated to the common grain (year), then combined, so rows are not multiplied. Releases are restricted to `tvSeries` and `tvMiniSeries`, the types covered by the snapshot.

| Year | New series (release fact) | Known interval covers year | Started, end unknown (cumulative) |
| --- | ---: | ---: | ---: |
| 1960 | 596 | 1,088 | 2,421 |
| 1980 | 1,040 | 2,052 | 10,990 |
| 2000 | 3,005 | 4,284 | 30,490 |
| 2010 | 6,949 | 5,311 | 64,852 |
| 2018 | 12,725 | 6,974 | 129,915 |
| 2020 | 11,770 | 4,096 | 147,279 |
| 2021 | 3,276 | 748 | 150,207 |
| 2026 | 1 | 5 | 150,370 |

-   **Observed results:** new series per year grow from 596 (1960) to a peak of 12,725 (2018). Known intervals covering a year peak earlier and lower (about 7,800 in 2017) and then fall to 4,096 in 2020 and 748 in 2021. The cumulative count of series with an unknown end reaches 150,370, which is 67.1% of the 224,004 eligible series.
-   **Interpretation:** known intervals grow more slowly than new series because most series have no recorded end year, so they never enter the known-interval measure. The sharp fall after 2017 is consistent with an extract effect: recent series are unlikely to have an end year recorded yet, and the extract thins out after 2021. It does not show that fewer series were airing. Known intervals are also a lower bound for activity; they cannot be added to the unknown-end count to estimate series in airing.
-   **Aggregation:** each measure is compared separately across years; summing annual values gives reporting-year memberships, not distinct series. The unknown-end count is on its own panel because it is about ten times larger.

![New series, known intervals and unknown-end series by year](media/2_series_releases_vs_intervals.png)

**C. Distribution of Adult-Flagged Content Across Title Types and Decades (Query 3)**

By cross-referencing `dim_title_type`, `dim_audience` and `dim_year`, the analysis isolates the adult-flagged titles and follows how their format mix changes over time. Each format is read as a percentage of the adult-flagged titles of its own decade (and, in the figure, of its own year) rather than as a raw count. This matters because the volume of adult-flagged titles grows roughly 42× between the 1970s (3,105 titles) and the 2010s (131,180), so raw counts would let the most recent decades drown out everything before them.

-   **Observed Results:** The format mix is almost completely replaced over five decades, in three successive handovers. Across the whole period, TV episodes account for 56.6% of the 232,891 adult-flagged titles, home video for 38.3% and theatrical movies for only 3.8%.
    -   **1970s, the theatrical era:** movies make up 86.2% of adult-flagged titles. Most of the remaining 12.5% is shorts (a follow-up check over 1970–1985 puts shorts at 7.4%), while video is still marginal at 1.2%.
    -   **1980s–1990s, home video takes over:** video jumps from 1.2% to 45.3% in the 1980s, almost level with movies (52.2%), and reaches 90.5% in the 1990s, when movies are down to 7.7%. Movies peak in absolute terms in the 1980s (3,399 titles) and never return to that volume.
    -   **2000s–2010s, TV episodes take over:** episodes go from under 1% in the 1990s to 34.0% in the 2000s and 76.7% in the 2010s (100,590 titles). Video is still the majority in the 2000s (64.1%, its absolute peak at 40,810 titles) but falls to 21.6% in the 2010s. It also falls in absolute terms (28,346 titles), so its loss of share is not just dilution by a growing total.
    -   **Everything else:** theatrical movies stay at or below roughly 1.5% from the 2000s onwards, and shorts plus all remaining formats combined stay under 2% from the 1980s onwards.
-   **Interpretation:** The data shows three successive handovers (theatrical movies, then home video, then TV episodes), each leading format giving up most of its share within one to two decades. This is consistent with adult production moving away from film-based distribution towards home video and later towards episodic, online-style distribution. The data only shows how IMDb classifies these titles, so the causes behind the shifts remain an interpretation. After the 1980s, theatrical movies are a negligible share, which fits the idea that this content rarely follows traditional theatrical pipelines.
-   **Data Notes and Limitations:** The 2020s are truncated: the source holds almost no adult-flagged titles after 2021 (9,478 in 2020, 1,312 in 2021, 2 in each of 2022 and 2023, none afterwards), so that decade reflects essentially two years (87.7% TV episodes, 10.1% video) and is not comparable to a full decade. For the same reason the figure stops at 2020. The percentages describe what adult-flagged content is made of, not how much of each format is adult-flagged, which would require each format's full catalog as the denominator.

The figure below shows the same composition at annual resolution (1970–2020), which makes the timing of each handover visible. For every year, each ridge is a format's share of that year's adult-flagged titles, so the five ridges always add up to 100%. Shorts get their own ridge because of their weight in the early years, while all remaining types (including `tvSeries`) are grouped as "Other types". Years with fewer than 25 adult-flagged titles are omitted so that tiny samples do not distort the percentages.

![Relative Mix of Adult-Flagged Titles by Format and Year](media/3_adult_evolution_by_decade.png)

**D. Runtime Completeness by Title Type (Query 4)**

The release fact table compares `title_count` with `runtime_known_count` for every title type with more than 10,000 titles.

| Title type | Titles | Missing runtime |
| --- | ---: | ---: |
| `videoGame` | 27,110 | 99.3% |
| `tvEpisode` | 4,907,683 | 82.7% |
| `tvMiniSeries` | 34,195 | 59.6% |
| `tvSeries` | 189,809 | 56.5% |
| `tvSpecial` | 31,632 | 52.2% |
| `short` | 777,919 | 33.7% |
| `tvMovie` | 127,074 | 31.1% |
| `movie` | 499,883 | 29.1% |
| `video` | 296,586 | 28.3% |

-   **Observed results:** runtime is missing for 99.3% of video games and 82.7% of TV episodes, and for more than half of the series, miniseries and specials. Even movies and videos miss it in about 29%.
-   **Interpretation:** the source records the existence of a title far more reliably than its technical metadata, and episodes are by far the largest type (4.9 million of 6.9 million releases). This justifies keeping `runtime_known_count` as a measure: dividing runtime by the number of titles instead would understate every average. It also qualifies Query 1: runtime averages describe only the titles that have the attribute.

![Titles Missing Runtime Information by Type](media/4_missing_runtime_pct.png)

**E. Individual Genres and the Genre Bridge (Query 5)**

Through `bridge_genre_set_genre`, each movie is counted in every genre of its combination. The query compares that membership count with the exact combination that contains only that genre.

| Genre | Movies containing the genre | Movies with only that genre | Share with only that genre |
| --- | ---: | ---: | ---: |
| Drama | 181,850 | 90,336 | 49.7% |
| Documentary | 97,849 | 73,634 | 75.3% |
| Comedy | 87,349 | 35,465 | 40.6% |
| Action | 40,507 | 9,539 | 23.5% |
| Romance | 39,655 | 5,438 | 13.7% |
| Crime | 29,411 | 3,177 | 10.8% |
| Thriller | 27,906 | 6,917 | 24.8% |
| Horror | 23,460 | 8,918 | 38.0% |

-   **Observed results:** a ranking by exact combination (as in the previous version of this report) puts standalone Drama (90.3k), Documentary (73.6k) and Comedy (35.5k) first, followed by `Drama,Romance` (11.2k) and `Comedy,Drama` (10.6k). Counting every movie that contains the genre gives a different picture: Romance appears in 39,655 movies but only 5,438 (13.7%) are tagged Romance alone, and Crime only 10.8%. Documentary is the opposite: 75.3% of documentaries carry no other genre.
-   **Interpretation:** exact combinations hide genres that are usually combined. Romance and Crime are much larger than their standalone combinations suggest, and they are under-represented in an exact-combination ranking. Documentary appears strong in both views because it is rarely mixed. This is the effect of the grain: each title belongs to one combination, and the bridge recovers the individual-genre view without expanding the fact table.
-   **Aggregation:** the genre rows overlap. A `Comedy,Romance` movie counts once in Comedy and once in Romance, so these totals must not be added to obtain a number of movies.

![Movies by individual genre: membership vs exact combination](media/5_genre_membership_vs_exact.png)

**F. Known Series Intervals by Individual Genre (Query 6)**

The same bridge is applied to `fact_series_year_snapshot`. The table shows series with a known interval covering 2010 and 2020, and the series that had started by 2020 with an unknown end.

| Genre | Known intervals, 2010 | Known intervals, 2020 | Started by 2020, end unknown |
| --- | ---: | ---: | ---: |
| Drama | 984 | 1,025 | 21,920 |
| Comedy | 1,452 | 904 | 34,297 |
| Documentary | 606 | 862 | 19,131 |
| Crime | 253 | 280 | 4,455 |
| Animation | 444 | 246 | 7,078 |
| Reality-TV | 474 | 230 | 14,822 |
| Romance | 210 | 221 | 5,250 |
| Family | 408 | 193 | 7,480 |

-   **Observed results:** known intervals in 2020 are led by Drama, Comedy and Documentary. Between 2010 and 2020, Drama, Documentary, Crime and Romance stay level or rise, while Comedy, Animation, Reality-TV and Family fall.
-   **Interpretation:** these differences should be read with care. The 2020 counts are affected by the extract effect described in B, and the unknown-end counts are roughly 16 to 64 times larger than the known intervals, so the known intervals cover only a small and possibly non-representative part of each genre. Genres with many series without an end year (Comedy has 34,297) are the most affected. The query shows how the model supports genre filters on the snapshot, not a verified change in genre popularity.

![Known series intervals by individual genre, 2010 vs 2020](media/6_series_by_genre.png)

## 6. Critical Reflection: Dimensional Model vs. the Operational Source

The only source available to the project is `data/raw/data.tsv`, a flat extract of IMDb title metadata. No assumption is made about how IMDb stores its data internally: the comparison is between the dimensional model built in this project and the extract it was built from, which plays the role of the operational data.

### 6.1. Comparison with the Source Extract

| Aspect | Source extract (`data/raw/data.tsv`) | Dimensional model |
| --- | --- | --- |
| Structure | One flat table with 7,697,093 records and nine text fields. | Two fact tables, four shared dimensions, an individual-genre dimension and a membership bridge. |
| Measures | Only `runtimeMinutes`, stored as text with `\N` for unknown values. | Additive and semi-additive measures with explicit aggregation rules. |
| Unknown values | Encoded as `\N` and handled differently by each query. | Handled once in the ETL: `Unknown` members, runtime availability and a separate unknown-end count. |
| Genres | One comma-separated string per title. | One normalised combination per title, plus individual genre memberships for both facts. |
| Annual series intervals | Each query must filter series and evaluate `startYear`/`endYear` for every reference year. | Precomputed in `fact_series_year_snapshot`, with known-end and unknown-end counts kept separate. |
| Integrity | None; the file is plain text. | Primary keys, foreign keys, strict types and checks enforced in SQLite, with transactional loading and reconciliation. |

### 6.2. Advantages of the Dimensional Model

**Consistent and standardised measures.** The average runtime in Query 1 is always `SUM(runtime_minutes) / SUM(runtime_known_count)`. Query 4 shows why this matters: runtime is missing for 28% to 99% of titles depending on the type, so dividing by the number of titles would understate every average. Release counts and runtime totals are additive; the two snapshot measures are semi-additive across time, and the model documents that they must not be summed across years or combined into an airing total.

**Shared dimensions across fact tables.** Query 2 puts new series (release fact), known intervals and unknown-end series (snapshot) side by side by year. This is possible because both facts use the same year, type, genre combination and audience keys: each table is aggregated to the year and the results are joined, with no direct relationship between the facts and no row multiplication. Query 6 reuses on the snapshot the same genre bridge that Query 5 uses on the releases.

**Simpler analytical queries.** Each query in section 5 is a join of a fact table with a few small dimensions (153 years, 13 types, 29 genres). The series part of Query 2 reads the 66,301-row snapshot instead of evaluating start and end years for 224,004 series in every reference year. Query 6 would otherwise require splitting the genre string of every series.

**Visible data quality.** The model turns source gaps into measurable quantities: runtime completeness by type (Query 4), and 150,370 of 224,004 eligible series (67.1%) without an end year (Query 2). Both are reported instead of being silently interpreted.

**Persistent and verifiable storage.** The warehouse is a SQLite file with keys, constraints and reconciliation checks, so every query reads the same validated data and the build can be reproduced from the source.

### 6.3. Limitations

**Additional storage for snapshots.** `fact_series_year_snapshot` materialises derived counts, so a series contributes to many annual rows. This is justified by the interval analysis, but the table must be maintained and rebuilt with the ETL. A cumulative-catalog fact is not retained, because it can be calculated from the release facts.

**Maintenance of the ETL process.** Each new extract requires transformation followed by a full warehouse rebuild; incremental updates are not implemented. Surrogate keys depend on the source reading order, so dimensions and facts must be loaded together. SQLite is adequate for this local project, but multi-user or larger deployments would need additional design.

**Loss of detail from the original dataset.** `originalTitle` is not retained, and individual end years are not stored in the aggregate, so title-level interval questions still require the source. 795,680 records have no usable start year and are excluded. The extract has no ratings, votes, people, regions or viewing figures, so it cannot answer questions about popularity, audiences or revenue.

**Overlapping individual genres.** Exact combinations are disjoint, while the bridge allows membership searches. Query 5 shows the consequence: Drama (181,850 movies) and Comedy (87,349) overlap through combinations such as `Comedy,Drama`, so genre totals cannot be added. Filters use `EXISTS` to avoid duplicates, and grouped reports give each genre the full count of the title.

**Incomplete coverage of recent years.** Titles drop from 317,638 in 2020 to 70,560 in 2021 and almost none afterwards. Trends near the end of the period, such as the fall in known intervals after 2017 in Query 2 or the 2020 values in Query 6, mix real change with the extract's coverage and with end years that are not yet recorded. The extraction date and version are unknown, so the cutoff cannot be tied to a real date.

**Incomplete intervals and indeterminate status.** A missing `endYear` leaves the airing status unknown, and known intervals do not reveal hiatuses. Neither group is a complete count of series actually airing, and both reflect the current extract, not what was known in each historical year.

**Annual time granularity.** Start and end years have no month or day precision, so a series that ends during a year is counted in that year, and the 31 December label does not establish activity on that day.

**Interpretation risks.** Movies, series and episodes are distinct entities, so adding their runtimes does not measure unique content. The adult flag is not an age rating. Runtime averages describe only the titles that have a runtime, which for recent series is a minority (28.1% in 2020–21).

### 6.4. Overall Assessment

For release volumes, composition, runtime and series interval metadata, the model gives consistent measures and short queries, and its two fact tables can be compared through shared dimensions. Its main cost is that it inherits the quality of a single extract: the answers about runtime, series intervals and recent years are limited by missing values and by thin coverage after 2021, not by the model. Actual broadcasting activity, popularity and historical source states would require additional data.

## 7. Conclusion

This project implements a fact constellation with two fact tables: `fact_title_release`, with 6,901,413 records and the additive measures `title_count`, `runtime_minutes` and `runtime_known_count`; and `fact_series_year_snapshot`, with 66,301 records and the semi-additive measures `known_interval_series_count` and `started_series_unknown_end_count`. Both share four dimensions (year, title type, genre combination and adult content flag), and an individual-genre dimension is linked to both through a membership bridge. This satisfies the requirements for more than 10,000 facts, additive and semi-additive measures, and shared dimensions including time. The warehouse is a validated, persistent SQLite database built reproducibly from the source file.

The six queries answer the analytical questions as follows:

- **Query 1:** the average runtime of movies is stable at 89–92 minutes across five decades, while that of TV series falls from about 52 to 41–44 minutes after the 2000s. The fall coincides with runtime coverage for series dropping from 55% to 28%, so it is not evidence that episodes became shorter.
- **Query 2:** new series per year peak at 12,725 in 2018, but known intervals reach only about 7,800 and then fall, while 150,370 of 224,004 series (67.1%) have no end year. The two measures are reported separately and the data does not support an airing total.
- **Query 3:** adult-flagged content shifts from theatrical movies (86.2% in the 1970s) to home video (90.5% in the 1990s) and then to TV episodes (76.7% in the 2010s).
- **Query 4:** runtime is missing for 99.3% of video games and 82.7% of TV episodes, and for more than half of the series.
- **Query 5:** exact genre combinations hide genres that are usually combined: only 13.7% of Romance movies and 10.8% of Crime movies carry that genre alone, compared with 75.3% for Documentary.
- **Query 6:** the genre filter works on the series snapshot, but the 2010–2020 differences are small compared with the unknown-end counts and are affected by the extract's coverage.

The main conclusions are methodological. Declaring the grain and the aggregation rule of each measure avoids errors such as averaging averages, adding snapshots across years or summing overlapping genres. The grain also changes the answers: using genre combinations as a dimension simplifies counting but alters genre rankings, and the bridge recovers the individual-genre view. A warehouse built from one extract inherits its limits: its snapshots are reconstructed rather than observed, and its coverage is uneven and ends effectively in 2021.

Future work could include a more recent extract with a recorded extraction date, incremental refreshes, title-level end-year analysis through an `end_year_key` role of the year dimension, and dated or episode-level sources to establish actual yearly activity. Regenerating with `--cutoff-year 2025` would not fix the thin coverage after 2021 or the missing end years.
