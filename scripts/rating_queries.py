"""Analyse the current ratings snapshot, unique by title_id."""

RATING_QUERIES = {
    '7. Ratings coverage and mean by title type': """
        SELECT t.title_type, COUNT(*) AS total_titles, COUNT(r.title_id) AS rated_titles,
               ROUND(100.0 * COUNT(r.title_id) / COUNT(*), 2) AS coverage_pct,
               ROUND(AVG(r.average_rating), 2) AS mean_title_rating,
               COALESCE(SUM(r.num_votes), 0) AS total_votes
        FROM fact_title_release f JOIN dim_title_type t USING (title_type_key)
        LEFT JOIN fact_title_rating_snapshot r ON r.title_id = f.title_id
        GROUP BY t.title_type ORDER BY total_titles DESC
    """,
    '8. Current movie ratings by release decade': """
        SELECT y.decade, COUNT(*) AS total_movies, COUNT(r.title_id) AS rated_movies,
               ROUND(100.0 * COUNT(r.title_id) / COUNT(*), 2) AS coverage_pct,
               ROUND(AVG(r.average_rating), 2) AS mean_title_rating,
               SUM(CASE WHEN r.num_votes >= 1000 THEN 1 ELSE 0 END) AS movies_1000_votes,
               ROUND(AVG(CASE WHEN r.num_votes >= 1000 THEN r.average_rating END), 2) AS mean_1000_votes
        FROM fact_title_release f JOIN dim_title_type t USING (title_type_key)
        JOIN dim_year y USING (year_key)
        LEFT JOIN fact_title_rating_snapshot r ON r.title_id = f.title_id
        WHERE t.title_type = 'movie' AND y.decade >= 1950
        GROUP BY y.decade ORDER BY y.decade
    """,
    '9. Movie ratings and coverage by individual genre': """
        SELECT g.genre, COUNT(*) AS total_movies, COUNT(r.title_id) AS rated_movies,
               ROUND(100.0 * COUNT(r.title_id) / COUNT(*), 2) AS coverage_pct,
               ROUND(AVG(r.average_rating), 2) AS mean_title_rating,
               SUM(CASE WHEN r.num_votes >= 1000 THEN 1 ELSE 0 END) AS movies_1000_votes,
               ROUND(AVG(CASE WHEN r.num_votes >= 1000 THEN r.average_rating END), 2) AS mean_1000_votes
        FROM fact_title_release f JOIN dim_title_type t USING (title_type_key)
        JOIN bridge_genre_set_genre b USING (genre_set_key)
        JOIN dim_genre g USING (genre_key)
        LEFT JOIN fact_title_rating_snapshot r ON r.title_id = f.title_id
        WHERE t.title_type = 'movie' AND g.genre <> 'Unknown'
        GROUP BY g.genre ORDER BY total_movies DESC
    """,
    '10. Top 20 movies with at least 10000 votes': """
        SELECT r.title_id, f.primary_title, r.year_key AS release_year,
               r.average_rating, r.num_votes
        FROM fact_title_rating_snapshot r
        JOIN fact_title_release f ON f.title_id = r.title_id
        JOIN dim_title_type t ON t.title_type_key = r.title_type_key
        WHERE t.title_type = 'movie' AND r.num_votes >= 10000
        ORDER BY r.average_rating DESC, r.num_votes DESC, r.title_id LIMIT 20
    """,
}


def generate_rating_plots(conn, media):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = conn.execute(next(iter(RATING_QUERIES.values()))).fetchall()
    rows = [r for r in rows if r[1] >= 10000]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh([r[0] for r in rows], [r[3] for r in rows], color='#267b83')
    ax.bar_label(bars, fmt='%.1f%%', padding=3)
    ax.set(xlabel='Titles with a rating (%)', xlim=(0, 110),
           title='Rating coverage by title type — IMDb download 12 November 2026')
    fig.tight_layout(); fig.savefig(media / '7_rating_coverage.png'); plt.close(fig)
    rows = conn.execute(list(RATING_QUERIES.values())[1]).fetchall()
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot([r[0] for r in rows], [r[4] for r in rows], 'o-', label='All rated movies')
    ax.plot([r[0] for r in rows], [r[6] for r in rows], 'o-', label='At least 1,000 votes')
    ax.set(xlabel='Release decade (2020s incomplete)', ylabel='Mean of title ratings',
           title='Current movie ratings by release decade; not historical ratings')
    ax.legend(); fig.tight_layout(); fig.savefig(media / '8_movie_ratings_by_decade.png'); plt.close(fig)
