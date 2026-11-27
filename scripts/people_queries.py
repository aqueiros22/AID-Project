"""Analyse credits (director, writer, actor) and series structure.

Every query goes through ``bridge_title_person`` or ``title_episode``; a title with several
directors counts once for each of them, so groups overlap and totals must not be summed.
"""

PEOPLE_QUERIES = {
    '11. Directors with the most movies and their current ratings': """
        SELECT p.person_name, COUNT(*) AS movies, COUNT(r.title_id) AS rated_movies,
               ROUND(AVG(r.average_rating), 2) AS mean_title_rating
        FROM bridge_title_person b
        JOIN dim_person p USING (person_key)
        JOIN fact_title_release f ON f.title_id = b.title_id
        JOIN dim_title_type t ON t.title_type_key = f.title_type_key
        LEFT JOIN fact_title_rating_snapshot r ON r.title_id = f.title_id
        WHERE b.role = 'director' AND t.title_type = 'movie'
        GROUP BY p.person_key, p.person_name
        ORDER BY movies DESC, p.person_name LIMIT 20
    """,
    '12. Best-rated directors with at least 5 movies of 1000 votes': """
        SELECT p.person_name, COUNT(*) AS movies_1000_votes,
               ROUND(AVG(r.average_rating), 2) AS mean_title_rating
        FROM bridge_title_person b
        JOIN dim_person p USING (person_key)
        JOIN fact_title_release f ON f.title_id = b.title_id
        JOIN dim_title_type t ON t.title_type_key = f.title_type_key
        JOIN fact_title_rating_snapshot r ON r.title_id = f.title_id
        WHERE b.role = 'director' AND t.title_type = 'movie' AND r.num_votes >= 1000
        GROUP BY p.person_key, p.person_name
        HAVING COUNT(*) >= 5
        ORDER BY mean_title_rating DESC, movies_1000_votes DESC, p.person_name LIMIT 20
    """,
    '13. Directors by birth decade (known birth year only)': """
        SELECT (p.birth_year / 10) * 10 AS birth_decade,
               COUNT(DISTINCT p.person_key) AS directors,
               COUNT(*) AS movie_credits
        FROM bridge_title_person b
        JOIN dim_person p USING (person_key)
        JOIN fact_title_release f ON f.title_id = b.title_id
        JOIN dim_title_type t ON t.title_type_key = f.title_type_key
        WHERE b.role = 'director' AND t.title_type = 'movie' AND p.birth_year IS NOT NULL
        GROUP BY birth_decade ORDER BY birth_decade
    """,
    '14. Seasons and episodes per series (episodes with known numbers)': """
        SELECT s.primary_title AS series, COUNT(*) AS episodes,
               COUNT(DISTINCT e.season_number) AS seasons,
               MAX(e.season_number) AS last_season
        FROM title_episode e
        JOIN fact_title_release s ON s.title_id = e.parent_series_id
        GROUP BY e.parent_series_id, s.primary_title
        ORDER BY episodes DESC, series LIMIT 20
    """,
    '15. Most frequent leading actors (billing order 1)': """
        SELECT p.person_name, COUNT(*) AS leading_roles
        FROM bridge_title_person b JOIN dim_person p USING (person_key)
        WHERE b.role = 'actor' AND b.billing_order = 1
        GROUP BY p.person_key, p.person_name
        ORDER BY leading_roles DESC, p.person_name LIMIT 20
    """,
}
