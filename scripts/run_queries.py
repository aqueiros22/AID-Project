"""Query the persistent SQLite warehouse and export analytical plots to media/."""
import argparse
import json
import sqlite3
from pathlib import Path
from scripts.genre_queries import find_titles_by_genres, release_count_by_genres, series_counts_by_genres
from scripts.build_warehouse import open_warehouse
from scripts.paths import MEDIA, WAREHOUSE, PROJECT_ROOT
from scripts.rating_queries import RATING_QUERIES, generate_rating_plots
from scripts.runtime_diagnostics import investigate


# Last full calendar year before the documented download date.
LAST_COMPLETE_YEAR = 2025

# Drill-across: each fact table is aggregated to the same grain (year) BEFORE being combined,
# so there is no row multiplication. Releases are restricted to the snapshot's series types.
SERIES_DRILL_ACROSS_SQL = """
    WITH rel AS (
        SELECT f.year_key, SUM(f.title_count) AS new_series
        FROM fact_title_release f
        JOIN dim_title_type t ON t.title_type_key = f.title_type_key
        WHERE t.title_type IN ('tvSeries', 'tvMiniSeries')
        GROUP BY f.year_key
    ), snap AS (
        SELECT year_key,
               SUM(known_interval_series_count) AS known_intervals,
               SUM(started_series_unknown_end_count) AS started_unknown_end
        FROM fact_series_year_snapshot
        GROUP BY year_key
    )
    SELECT y.year,
           COALESCE(rel.new_series, 0) AS new_series,
           COALESCE(snap.known_intervals, 0) AS known_intervals,
           COALESCE(snap.started_unknown_end, 0) AS started_unknown_end
    FROM dim_year y
    LEFT JOIN rel ON rel.year_key = y.year_key
    LEFT JOIN snap ON snap.year_key = y.year_key
    WHERE y.year IN (1960, 1970, 1980, 1990, 2000, 2010, 2015, 2018, 2020, 2021, 2022, 2026)
    ORDER BY y.year;
"""

# Per exact combination first, then through the bridge: a title with several genres counts
# once in EACH of its genres (overlapping groups), while 'standalone' is the exact combination.
GENRE_MEMBERSHIP_SQL = """
    WITH per_set AS (
        SELECT f.genre_set_key, SUM(f.title_count) AS n
        FROM fact_title_release f
        JOIN dim_title_type t ON t.title_type_key = f.title_type_key
        WHERE t.title_type = 'movie'
        GROUP BY f.genre_set_key
    )
    SELECT g.genre,
           SUM(p.n) AS movies_with_genre,
           SUM(CASE WHEN s.genre_set = g.genre THEN p.n ELSE 0 END) AS standalone_only,
           ROUND(SUM(CASE WHEN s.genre_set = g.genre THEN p.n ELSE 0 END) * 100.0 / SUM(p.n), 1) AS pct_standalone
    FROM per_set p
    JOIN dim_genre_set s ON s.genre_set_key = p.genre_set_key
    JOIN bridge_genre_set_genre b ON b.genre_set_key = p.genre_set_key
    JOIN dim_genre g ON g.genre_key = b.genre_key
    WHERE g.genre <> 'Unknown'
    GROUP BY g.genre
    ORDER BY movies_with_genre DESC
    LIMIT 8;
"""

SERIES_BY_GENRE_SQL = """
    SELECT g.genre,
           SUM(CASE WHEN f.year_key = 2010 THEN f.known_interval_series_count ELSE 0 END) AS known_2010,
           SUM(CASE WHEN f.year_key = 2020 THEN f.known_interval_series_count ELSE 0 END) AS known_2020,
           SUM(CASE WHEN f.year_key = 2020 THEN f.started_series_unknown_end_count ELSE 0 END) AS unknown_end_2020
    FROM fact_series_year_snapshot f
    JOIN bridge_genre_set_genre b ON b.genre_set_key = f.genre_set_key
    JOIN dim_genre g ON g.genre_key = b.genre_key
    WHERE f.year_key IN (2010, 2020) AND g.genre <> 'Unknown'
    GROUP BY g.genre
    ORDER BY known_2020 DESC
    LIMIT 8;
"""


def print_terminal_queries(conn: sqlite3.Connection) -> None:
    queries = {
        "1. [Additive] Release Volume and Average Known Runtime (Movies vs TV Series by Decade)": """
            SELECT
                y.decade,
                t.title_type,
                SUM(f.title_count) AS total_releases,
                SUM(f.runtime_known_count) AS titles_with_runtime,
                ROUND(SUM(f.runtime_known_count) * 100.0 / SUM(f.title_count), 1) AS pct_with_runtime,
                ROUND(SUM(f.runtime_minutes) * 1.0 / SUM(f.runtime_known_count), 1) AS avg_runtime_mins
            FROM fact_title_release f
            JOIN dim_year y ON f.year_key = y.year_key
            JOIN dim_title_type t ON f.title_type_key = t.title_type_key
            WHERE t.title_type IN ('movie', 'tvSeries')
              AND y.decade >= 1980
            GROUP BY y.decade, t.title_type
            ORDER BY y.decade DESC, t.title_type;
        """,
        "2. [Drill-across] New Series (releases) vs Known Intervals vs Started with Unknown End, by Year": SERIES_DRILL_ACROSS_SQL,
        "3. [Audience x Time x Title Type] Adult-Flagged Releases Across Decades by Format": """
            SELECT 
                y.decade,
                SUM(f.title_count) AS total_adult,
                SUM(CASE WHEN t.title_type = 'movie' THEN f.title_count ELSE 0 END) AS theatrical_movie,
                SUM(CASE WHEN t.title_type = 'video' THEN f.title_count ELSE 0 END) AS home_video,
                SUM(CASE WHEN t.title_type = 'tvEpisode' THEN f.title_count ELSE 0 END) AS tv_episodes,
                SUM(CASE WHEN t.title_type = 'short' THEN f.title_count ELSE 0 END) AS shorts,
                SUM(CASE WHEN t.title_type NOT IN ('movie', 'video', 'tvEpisode', 'short') THEN f.title_count ELSE 0 END) AS other_formats
            FROM fact_title_release f
            JOIN dim_year y ON f.year_key = y.year_key
            JOIN dim_title_type t ON f.title_type_key = t.title_type_key
            JOIN dim_audience a ON f.audience_key = a.audience_key
            WHERE a.audience = 'Adult-flagged'
              AND y.decade >= 1970
            GROUP BY y.decade
            ORDER BY y.decade DESC;
        """,
        "4. [Data Quality] Percentage of Titles Missing Runtime Information by Category": """
            SELECT 
                t.title_type,
                SUM(f.title_count) AS total_titles,
                SUM(f.title_count) - SUM(f.runtime_known_count) AS missing_runtime,
                ROUND(((SUM(f.title_count) - SUM(f.runtime_known_count)) * 100.0 / SUM(f.title_count)), 2) AS missing_percentage
            FROM fact_title_release f
            JOIN dim_title_type t ON f.title_type_key = t.title_type_key
            GROUP BY t.title_type
            HAVING total_titles > 10000
            ORDER BY missing_percentage DESC;
        """,
        "5. [Genre bridge] Movies: Individual-Genre Membership vs Exact Standalone Combination": GENRE_MEMBERSHIP_SQL.replace("LIMIT 8", "LIMIT 10"),
        "6. [Snapshot x Genre bridge] Known Series Intervals by Individual Genre (2010 vs 2020)": SERIES_BY_GENRE_SQL,
    }

    queries.update(RATING_QUERIES)
    results = {}
    for title, sql in queries.items():
        print(title)
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        width = max(110, len(columns) * 21 - 3)  # grow the rule with the number of columns
        print("-" * width)
        print(" | ".join(f"{col:<18}" for col in columns))
        print("-" * width)
        rows = cursor.fetchall()
        results[title] = {"columns": columns, "rows": rows}
        for row in rows:
            print(" | ".join(f"{str(val)[:18]:<18}" for val in row))
        print("\n")

    (PROJECT_ROOT / "data" / "analysis_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

def generate_runtime_plot(conn: sqlite3.Connection, media_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    media_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "figure.dpi": 300})

    # --- Plot 1: Average Runtime Evolution (Query 1) ---
    data_q1 = conn.execute("""
        SELECT y.decade, t.title_type, SUM(f.runtime_minutes) * 1.0 / SUM(f.runtime_known_count)
        FROM fact_title_release f
        JOIN dim_year y ON f.year_key = y.year_key
        JOIN dim_title_type t ON f.title_type_key = t.title_type_key
        WHERE t.title_type IN ('movie', 'tvSeries') AND y.decade >= 1980 AND f.runtime_known_count > 0
        GROUP BY y.decade, t.title_type
        ORDER BY y.decade;
    """).fetchall()

    decades = sorted({r[0] for r in data_q1})
    movies_rt = [r[2] for r in data_q1 if r[1] == "movie"]
    series_rt = [r[2] for r in data_q1 if r[1] == "tvSeries"]

    diagnostics = investigate(conn)['decades']
    fig, (ax, robust) = plt.subplots(1, 2, figsize=(12, 5.2))
    ax.plot(decades, movies_rt, marker="o", linewidth=2, color="#267b83", label="Movies: full mean")
    ax.plot(decades, series_rt, marker="s", linewidth=2, color="#c45328", label="TV series: full mean")
    for x, y in zip(decades, series_rt):
        ax.annotate(f"{y:.1f}", (x, y), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=9)
    ax.set(title="Unmodified mean of known runtimes", ylabel="Minutes",
           ylim=(0, max(movies_rt + series_rt) * 1.2))
    robust.plot(decades, [diagnostics[str(d)]['median'] for d in decades], 'o-',
                color="#267b83", label="TV series: median")
    robust.plot(decades, [diagnostics[str(d)]['mean_without_largest'] for d in decades], 's--',
                color="#c45328", label="TV series: mean excluding largest")
    robust.set(title="TV series: robust summary and sensitivity", ylabel="Minutes")
    robust.set_ylim(bottom=0)
    for axis in (ax, robust):
        axis.set_xticks(decades)
        axis.set_xticklabels([f"{d}s" + ("*" if d == 2020 else "") for d in decades])
        axis.set_xlabel("Release / start decade")
        axis.grid(axis='y', linestyle='--', alpha=.3)
        axis.legend(loc='lower left' if axis is robust else 'upper left', fontsize=9)
    fig.suptitle("Runtime comparison: the mean is sensitive to extreme source values", fontweight="bold")
    fig.text(.5, .015, "Positive known runtimes only. *2020s incomplete. Excluding the largest value is a sensitivity check, not a correction.",
             ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .05, 1, .94))
    fig.savefig(media_dir / "1_runtime_evolution.png")
    plt.close(fig)


def generate_plots(conn: sqlite3.Connection, media_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    media_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "figure.dpi": 300})

    generate_runtime_plot(conn, media_dir)

    # --- Plot 2: New series vs known intervals vs started-with-unknown-end (Query 2) ---
    # Three separate measures from two fact tables, never added together. The cumulative
    # unknown-end count has its own panel and scale because it describes a different population.
    rows_q2 = conn.execute("""
        WITH rel AS (
            SELECT f.year_key, SUM(f.title_count) AS n
            FROM fact_title_release f JOIN dim_title_type t ON t.title_type_key = f.title_type_key
            WHERE t.title_type IN ('tvSeries', 'tvMiniSeries') GROUP BY f.year_key
        ), snap AS (
            SELECT year_key, SUM(known_interval_series_count) AS k, SUM(started_series_unknown_end_count) AS u
            FROM fact_series_year_snapshot GROUP BY year_key
        )
        SELECT y.year, COALESCE(rel.n, 0), COALESCE(snap.k, 0), COALESCE(snap.u, 0)
        FROM dim_year y LEFT JOIN rel USING (year_key) LEFT JOIN snap USING (year_key)
        WHERE y.year BETWEEN 1950 AND 2026 ORDER BY y.year;
    """).fetchall()
    yrs = [r[0] for r in rows_q2]
    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(10, 7.5), sharex=True)
    ax_top.plot(yrs, [r[1] / 1000 for r in rows_q2], linewidth=2.2, color="#1f77b4", label="New series released (release fact)")
    ax_top.plot(yrs, [r[2] / 1000 for r in rows_q2], linewidth=2.2, color="#2ca02c", label="Known interval covers year (snapshot)")
    ax_bot.plot(yrs, [r[3] / 1000 for r in rows_q2], linewidth=2.2, color="#ff7f0e", label="Started, end year unknown (snapshot, cumulative)")
    for ax, ylabel in ((ax_top, "Series per year (thousands)"), (ax_bot, "Series (thousands)")):
        ax.axvline(LAST_COMPLETE_YEAR + 1, color="grey", linestyle=":", linewidth=1.5)
        ax.set_ylabel(ylabel)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(loc="upper left")
    ax_top.text(LAST_COMPLETE_YEAR - 8, ax_top.get_ylim()[1] * 0.55, "2026 incomplete", fontsize=9, color="grey")
    ax_bot.set_xlabel("Reference / release year")
    fig.suptitle("TV Series: New Releases, Known Intervals and Missing End Years", fontweight="bold")
    fig.text(0.5, 0.01, "Separate measures: not added into an airing total. Metadata reflects the current extract.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    fig.savefig(media_dir / "2_series_releases_vs_intervals.png")
    plt.close(fig)

    # --- Plot 3: 2D small multiples with equal percentage scales and sample sizes ---
    # No occlusion or perspective distortion; retain gaps for years below the threshold.
    MIN_ADULT_TITLES_PER_YEAR = 25
    # Exclude the incomplete download year from annual shares.
    FIRST_YEAR, LAST_YEAR = 1970, LAST_COMPLETE_YEAR

    data_q3 = conn.execute("""
        SELECT 
            y.year,
            SUM(f.title_count) AS total_cnt,
            SUM(CASE WHEN t.title_type = 'video' THEN f.title_count ELSE 0 END) AS video_cnt,
            SUM(CASE WHEN t.title_type = 'tvEpisode' THEN f.title_count ELSE 0 END) AS ep_cnt,
            SUM(CASE WHEN t.title_type = 'movie' THEN f.title_count ELSE 0 END) AS movie_cnt,
            SUM(CASE WHEN t.title_type = 'short' THEN f.title_count ELSE 0 END) AS short_cnt
        FROM fact_title_release f
        JOIN dim_year y ON f.year_key = y.year_key
        JOIN dim_title_type t ON f.title_type_key = t.title_type_key
        JOIN dim_audience a ON f.audience_key = a.audience_key
        WHERE a.audience = 'Adult-flagged'
          AND y.year BETWEEN ? AND ?
        GROUP BY y.year
        HAVING SUM(f.title_count) >= ?
        ORDER BY y.year;
    """, (FIRST_YEAR, LAST_YEAR, MIN_ADULT_TITLES_PER_YEAR)).fetchall()

    years_q3 = list(range(FIRST_YEAR, LAST_YEAR + 1))
    by_year = {r[0]: r for r in data_q3}
    categories = [("Movie", 4), ("Video", 2), ("TV episode", 3), ("Short", 5), ("Other types", None)]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.5), sharex=True)
    colors = ["#267b83", "#c45328", "#5265a8", "#78733a", "#777777"]
    for ax, (category, index), color in zip(axes.flat, categories, colors):
        shares = []
        for year in years_q3:
            row = by_year.get(year)
            count = (row[index] if index else row[1] - sum(row[2:6])) if row else None
            shares.append(100 * count / row[1] if row else float('nan'))
        ax.plot(years_q3, shares, color=color, linewidth=2)
        ax.fill_between(years_q3, shares, color=color, alpha=.12)
        ax.set(title=category, ylim=(0, 100), ylabel="Share of annual titles")
        ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
    count_ax = axes.flat[-1]
    count_ax.plot(years_q3, [by_year[y][1] if y in by_year else float('nan') for y in years_q3], color='#444444')
    count_ax.set(title="Denominator: adult-flagged titles", ylabel="Titles per year", ylim=(0, None))
    for ax in axes.flat:
        ax.set_xlim(FIRST_YEAR, LAST_YEAR)
        ax.grid(axis='y', linestyle='--', alpha=.3)
    for ax in axes[-1]:
        ax.set_xlabel("Release / start year")
    fig.suptitle(f"Adult-flagged titles by source type, {FIRST_YEAR}-{LAST_YEAR}", fontweight='bold')
    fig.text(.5, .015, f"Each share uses all adult-flagged titles in its year. Years with fewer than {MIN_ADULT_TITLES_PER_YEAR} titles are omitted; 2026 is incomplete.",
             ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .05, 1, .94))
    fig.savefig(media_dir / "3_adult_evolution_by_decade.png")
    plt.close(fig)

    # --- Plot 4: Missing Runtime Percentage (Query 4) ---
    data_q4 = conn.execute("""
        SELECT t.title_type,
               ROUND(((SUM(f.title_count) - SUM(f.runtime_known_count)) * 100.0 / SUM(f.title_count)), 2) AS missing_pct
        FROM fact_title_release f
        JOIN dim_title_type t ON f.title_type_key = t.title_type_key
        GROUP BY t.title_type
        HAVING SUM(f.title_count) > 10000
        ORDER BY missing_pct ASC;
    """).fetchall()  # ascending, so the worst type is drawn at the top of the horizontal bars

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.barh([r[0] for r in data_q4], [r[1] for r in data_q4], color="#d62728", height=0.6)
    ax.bar_label(bars, fmt="%.1f%%", padding=4, fontsize=9)
    ax.set_title("Titles Missing Runtime Information by Type (>10k Titles)", fontweight="bold", pad=12)
    ax.set_xlabel("Missing Runtime (%)")
    ax.set_xlim(0, 112)
    ax.grid(True, axis="x", linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(media_dir / "4_missing_runtime_pct.png")
    plt.close(fig)

    # --- Plot 5: Genre membership vs exact standalone combination, movies (Query 5) ---
    data_q5 = conn.execute(GENRE_MEMBERSHIP_SQL).fetchall()
    names = [r[0] for r in data_q5]
    xs = range(len(names))
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar([x - 0.2 for x in xs], [r[1] / 1000 for r in data_q5], width=0.4, color="#2ca02c", label="Movies containing the genre (membership)")
    ax.bar([x + 0.2 for x in xs], [r[2] / 1000 for r in data_q5], width=0.4, color="#9467bd", label="Movies with exactly that genre only")
    for x, r in zip(xs, data_q5):
        ax.annotate(f"{r[3]:.0f}%", (x + 0.2, r[2] / 1000), textcoords="offset points", xytext=(0, 4), ha="center", fontsize=9)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(names)
    ax.set_ylabel("Movies (thousands)")
    ax.set_title("Movies by Individual Genre: Membership vs Exact Combination", fontweight="bold", pad=12)
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    ax.legend()
    fig.text(0.5, 0.005, "Labels: % of the genre's movies that carry no other genre. Genre groups overlap: do not sum them.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(media_dir / "5_genre_membership_vs_exact.png")
    plt.close(fig)

    # --- Plot 6: Known series intervals by individual genre, 2010 vs 2020 (Query 6) ---
    data_q6 = conn.execute(SERIES_BY_GENRE_SQL).fetchall()
    names = [r[0] for r in data_q6]
    xs = range(len(names))
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar([x - 0.2 for x in xs], [r[1] for r in data_q6], width=0.4, color="#1f77b4", label="2010")
    ax.bar([x + 0.2 for x in xs], [r[2] for r in data_q6], width=0.4, color="#ff7f0e", label="2020")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(names)
    ax.set_ylabel("Series with a known interval covering the year")
    ax.set_title("Known Series Intervals by Individual Genre (2010 vs 2020)", fontweight="bold", pad=12)
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    ax.legend()
    fig.text(0.5, 0.005, "Genre groups overlap. Missing end years leave airing status unknown; known intervals do not prove annual activity.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(media_dir / "6_series_by_genre.png")
    plt.close(fig)

    generate_rating_plots(conn, media_dir)
    print(f"All 8 plots exported to '{media_dir}/'!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=WAREHOUSE)
    parser.add_argument('--no-plots', action='store_true', help='Print analytical queries without generating figures')
    parser.add_argument('--genre', action='append', help='Individual genre, e.g. Comedy; repeat for an OR search')
    parser.add_argument('--snapshot-year', type=int, help='Reference year for a genre search; defaults to cutoff')
    args = parser.parse_args()
    db_conn = open_warehouse(args.database)
    if args.genre:
        year = args.snapshot_year
        if year is None:
            year = db_conn.execute('SELECT MAX(year_key) FROM dim_year').fetchone()[0]
        if not db_conn.execute('SELECT 1 FROM dim_year WHERE year_key = ?', (year,)).fetchone():
            parser.error('Snapshot year must be within the generated year dimension')
        print('Genres (any match):', ', '.join(args.genre))
        print('Matching releases:', release_count_by_genres(db_conn, args.genre))
        known, unknown = series_counts_by_genres(db_conn, args.genre, year)
        print(f'Known series intervals covering {year}: {known}')
        print(f'Series started by {year} with unknown end: {unknown} (airing status indeterminate)')
        print('These measures must not be combined into an airing total.')
        print('First 20 matching titles (most recent first):')
        for title in find_titles_by_genres(db_conn, args.genre):
            print(' | '.join(map(str, title)))
    else:
        print_terminal_queries(db_conn)
        if not args.no_plots:
            generate_plots(db_conn, MEDIA)
    db_conn.close()
