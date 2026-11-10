"""Query the persistent SQLite warehouse and export analytical plots to media/."""
import argparse
import sqlite3
from pathlib import Path
from scripts.genre_queries import find_titles_by_genres, release_count_by_genres, series_counts_by_genres
from scripts.build_warehouse import open_warehouse
from scripts.paths import MEDIA, WAREHOUSE


def print_terminal_queries(conn: sqlite3.Connection) -> None:
    queries = {
        "1. [Additive] Release Volume and Average Runtime Evolution (Movies vs TV Series by Decade)": """
            SELECT 
                y.decade, 
                t.title_type, 
                SUM(f.title_count) AS total_releases,
                ROUND(SUM(f.runtime_minutes) * 1.0 / SUM(f.runtime_known_count), 1) AS avg_runtime_mins
            FROM fact_title_release f
            JOIN dim_year y ON f.year_key = y.year_key
            JOIN dim_title_type t ON f.title_type_key = t.title_type_key
            WHERE t.title_type IN ('movie', 'tvSeries') 
              AND y.decade >= 1980 
              AND f.runtime_known_count > 0
            GROUP BY y.decade, t.title_type
            ORDER BY y.decade DESC, t.title_type;
        """,
        "2. [Semi-Additive] Known Series Intervals and Started Series with Unknown End": """
            SELECT 
                y.year AS snapshot_year, 
                t.title_type, 
                SUM(c.known_interval_series_count) AS known_intervals,
                SUM(c.started_series_unknown_end_count) AS started_unknown_end
            FROM fact_series_year_snapshot c
            JOIN dim_year y ON c.year_key = y.year_key
            JOIN dim_title_type t ON c.title_type_key = t.title_type_key
            WHERE y.year IN (1990, 2000, 2010, 2020) 
              AND t.title_type IN ('tvSeries', 'tvMiniSeries')
            GROUP BY y.year, t.title_type
            ORDER BY y.year DESC, t.title_type;
        """,
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
            ORDER BY missing_percentage DESC
            LIMIT 5;
        """,
        "5. [Combinatorial Fragmentation] Top 5 Exact Genre Sets in 'Movies'": """
            SELECT 
                g.genre_set, 
                SUM(f.title_count) AS total_movies
            FROM fact_title_release f
            JOIN dim_genre_set g ON f.genre_set_key = g.genre_set_key
            JOIN dim_title_type t ON f.title_type_key = t.title_type_key
            WHERE t.title_type = 'movie' 
              AND g.genre_set != 'Unknown'
            GROUP BY g.genre_set
            ORDER BY total_movies DESC
            LIMIT 5;
        """,
    }

    for title, sql in queries.items():
        print(title)
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        width = max(110, len(columns) * 21 - 3)  # grow the rule with the number of columns
        print("-" * width)
        print(" | ".join(f"{col:<18}" for col in columns))
        print("-" * width)
        for row in cursor.fetchall():
            print(" | ".join(f"{str(val)[:18]:<18}" for val in row))
        print("\n")


def generate_plots(conn: sqlite3.Connection, media_dir: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.ticker import PercentFormatter

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

    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(decades, movies_rt, marker="o", linewidth=2.5, color="#1f77b4", label="Movies")
    ax.plot(decades, series_rt, marker="s", linewidth=2.5, color="#ff7f0e", label="TV Series")
    for x, y in zip(decades, movies_rt):
        ax.annotate(f"{y:.1f}m", (x, y), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9)
    for x, y in zip(decades, series_rt):
        ax.annotate(f"{y:.1f}m", (x, y), textcoords="offset points", xytext=(0, -14), ha="center", fontsize=9)
    ax.set_title("Average Known Runtime by Decade (1980s–2020s)", fontweight="bold", pad=12)
    ax.set_xlabel("Decade")
    ax.set_ylabel("Average Runtime (Minutes)")
    ax.set_xticks(decades)
    ax.set_ylim(30, 105)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()
    fig.savefig(media_dir / "1_runtime_evolution.png")
    plt.close(fig)

    # --- Plot 2: Annual Series Snapshot (Query 2) ---
    data_q2 = conn.execute("""
        SELECT y.year, t.title_type,
               SUM(c.known_interval_series_count), SUM(c.started_series_unknown_end_count)
        FROM fact_series_year_snapshot c
        JOIN dim_year y ON c.year_key = y.year_key
        JOIN dim_title_type t ON c.title_type_key = t.title_type_key
        WHERE y.year IN (1990, 2000, 2010, 2020) AND t.title_type IN ('tvSeries', 'tvMiniSeries')
        GROUP BY y.year, t.title_type
        ORDER BY y.year;
    """).fetchall()

    years = sorted({r[0] for r in data_q2})
    values = {(r[0], r[1]): (r[2] / 1000, r[3] / 1000) for r in data_q2}
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True)
    for column, title_type, label in zip(range(2), ('tvSeries', 'tvMiniSeries'), ('TV series', 'TV miniseries')):
        known = [values.get((year, title_type), (0, 0))[0] for year in years]
        unknown = [values.get((year, title_type), (0, 0))[1] for year in years]
        for row, counts, color, description in (
            (0, known, '#1f77b4', 'Known interval covers year'),
            (1, unknown, '#ff7f0e', 'Started; end unknown; status indeterminate'),
        ):
            ax = axes[row, column]
            bars = ax.bar(years, counts, width=5, color=color)
            ax.bar_label(bars, labels=[f'{value:.2f}k' for value in counts], padding=3, fontsize=9)
            ax.set_title(f'{label}\n{description}', fontweight='bold', fontsize=10)
            ax.set_ylabel('Series (thousands)')
            ax.set_xticks(years)
            ax.set_ylim(0, max(counts + [0.1]) * 1.25)
            ax.grid(True, axis='y', linestyle='--', alpha=0.4)
            if row == 1:
                ax.set_xlabel('Reference year')
    fig.suptitle('Series Intervals and Missing End-Year Metadata', fontweight='bold')
    fig.text(0.5, 0.025, 'Separate measures: do not combine into an airing total. Metadata reflects the current extract.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    fig.savefig(media_dir / "2_series_interval_information.png")
    plt.close(fig)

    # --- Plot 3: Relative Mix of Adult-Flagged Releases by Title Type, per Year (Query 3) ---
    # Every ridge is a format's SHARE (%) of all adult-flagged releases in that year, so a year with
    # 10k titles (8k of them movies) is read on the same scale as a year with 100k titles.
    # Four types get their own ridge: video, tvEpisode, movie and short (short is ~7% of adult-flagged
    # titles in 1970-85, while tvSeries is <0.1%). Every other title type (tvSeries, tvMovie,
    # tvMiniSeries, tvSpecial, tvShort, videoGame, ...) is lumped into "Other types", so the five
    # ridges add up to 100% in every year.
    # Years with very few adult-flagged titles are dropped: a share computed from a handful of
    # titles is mostly noise. Raise/lower the threshold if you want more/less of the early years.
    MIN_ADULT_TITLES_PER_YEAR = 25
    # The source has almost no adult-flagged titles after 2021 (about 9.5k in 2020, 1.3k in 2021,
    # 2 in 2022, none after), so every ridge would nosedive from a data cliff, not a real trend.
    # The plot therefore stops at 2020, the last year with reasonably complete data.
    FIRST_YEAR, LAST_YEAR = 1970, 2020

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

    years_q3 = [r[0] for r in data_q3]
    # Query row = (year, total, video, tvEpisode, movie, short); "Other" = whatever is left of the total.
    # Ridges are listed front -> back. A tall ridge hides whatever is behind it, so the order follows
    # who is tall when: movies dominate the early years (back), video the middle ones, tvEpisode the
    # later ones, and the small groups (short, other) sit in front where they hide nothing.
    categories = ["Other types", "Short", "Home video", "TV episode", "Movie"]
    ridge_counts = [
        lambda r: r[1] - sum(r[2:6]),  # Other types (tvSeries, tvMovie, tvMiniSeries, ...)
        lambda r: r[5],                # short
        lambda r: r[2],                # video
        lambda r: r[3],                # tvEpisode
        lambda r: r[4],                # movie
    ]
    series_list = [[100.0 * count(r) / r[1] for r in data_q3] for count in ridge_counts]
    colors = ["#7f7f7f", "#2ca02c", "#e377c2", "#17becf", "#9467bd"]

    fig = plt.figure(figsize=(11, 6.5))
    ax = fig.add_subplot(111, projection="3d")
    # stretch the year axis so the ridges are not squashed; zoom fills the empty margins
    ax.set_box_aspect((2.4, 2.1, 1), zoom=1.15)

    verts = []
    for z_vals in series_list:
        # Close the polygon at the base (z=0) so it fills like a 3D ridge
        v = [(years_q3[0], 0)] + list(zip(years_q3, z_vals)) + [(years_q3[-1], 0)]
        verts.append(v)

    poly = PolyCollection(verts, facecolors=colors, edgecolors="black", linewidths=1.2, alpha=0.75)
    ax.add_collection3d(poly, zs=range(len(categories)), zdir="y")

    # Plot crisp top edge lines for each 3D curve
    for idx, (z_vals, color) in enumerate(zip(series_list, colors)):
        ax.plot(years_q3, [idx] * len(years_q3), z_vals, color=color, linewidth=2.2)

    ax.set_xlabel("Release Year", labelpad=14)
    ax.set_zlabel("Share of the Year's Adult-Flagged Releases", labelpad=14)
    ax.set_yticks(range(len(categories)))
    ax.set_yticklabels(categories, rotation=-20, ha="left", va="center")
    ax.zaxis.set_major_formatter(PercentFormatter(100, decimals=0))
    # Put the % axis on the opposite (left) edge so its title can never collide with the
    # format labels on the right. Needs matplotlib >= 3.10; older versions keep the default side.
    try:
        ax.zaxis.set_ticks_position("lower")
        ax.zaxis.set_label_position("lower")
    except Exception:
        pass
    # push tick labels away from the panes so they do not sit on top of the ridges
    ax.tick_params(axis="x", pad=6)
    ax.tick_params(axis="y", pad=10)
    ax.tick_params(axis="z", pad=8)
    ax.set_xlim(years_q3[0], years_q3[-1])
    ax.set_ylim(-0.5, len(categories) - 0.5)
    ax.set_zlim(0, min(100, max(max(s) for s in series_list) * 1.1))

    fig.suptitle(
        f"Relative Mix of Adult-Flagged Releases by Format, {years_q3[0]}–{years_q3[-1]} (share of each year's total)",
        fontweight="bold", y=0.96,
    )
    ax.view_init(elev=32, azim=-60)  # a bit higher than before so front ridges hide less of the back ones
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.04, top=0.92)
    fig.savefig(media_dir / "3_adult_evolution_by_decade.png", bbox_inches="tight", pad_inches=0.4)
    plt.close(fig)

    # --- Plot 4: Missing Runtime Percentage (Query 4) ---
    data_q4 = conn.execute("""
        SELECT 
            t.title_type,
            ROUND(((SUM(f.title_count) - SUM(f.runtime_known_count)) * 100.0 / SUM(f.title_count)), 2) AS missing_pct
        FROM fact_title_release f
        JOIN dim_title_type t ON f.title_type_key = t.title_type_key
        GROUP BY t.title_type
        HAVING SUM(f.title_count) > 10000
        ORDER BY missing_pct ASC
        LIMIT 5;
    """).fetchall()

    types_q4 = [r[0] for r in data_q4]
    pcts_q4 = [r[1] for r in data_q4]

    fig, ax = plt.subplots(figsize=(8, 4.2))
    bars = ax.barh(types_q4, pcts_q4, color="#d62728", height=0.55)
    ax.bar_label(bars, fmt="%.1f%%", padding=4, fontsize=9)
    ax.set_title("Titles Missing Runtime Information by Category (>10k Titles)", fontweight="bold", pad=12)
    ax.set_xlabel("Missing Runtime (%)")
    ax.set_xlim(0, 112)
    ax.grid(True, axis="x", linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(media_dir / "4_missing_runtime_pct.png")
    plt.close(fig)

    # --- Plot 5: Top 5 Exact Genre Sets in Movies (Query 5) ---
    data_q5 = conn.execute("""
        SELECT g.genre_set, SUM(f.title_count) AS total_movies
        FROM fact_title_release f
        JOIN dim_genre_set g ON f.genre_set_key = g.genre_set_key
        JOIN dim_title_type t ON f.title_type_key = t.title_type_key
        WHERE t.title_type = 'movie' AND g.genre_set != 'Unknown'
        GROUP BY g.genre_set
        ORDER BY total_movies DESC
        LIMIT 5;
    """).fetchall()[::-1]

    genres_q5 = [r[0] for r in data_q5]
    counts_q5 = [r[1] / 1000 for r in data_q5]

    fig, ax = plt.subplots(figsize=(8, 4.2))
    bars = ax.barh(genres_q5, counts_q5, color="#2ca02c", height=0.55)
    ax.bar_label(bars, fmt="%.1fk", padding=4, fontsize=9)
    ax.set_title("Top 5 Exact Genre Sets for Movies (All-Time)", fontweight="bold", pad=12)
    ax.set_xlabel("Total Released Movies (Thousands)")
    ax.set_xlim(0, max(counts_q5) * 1.15)
    ax.grid(True, axis="x", linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(media_dir / "5_top_movie_genres.png")
    plt.close(fig)

    print(f"All 5 plots exported to '{media_dir}/'!")


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
