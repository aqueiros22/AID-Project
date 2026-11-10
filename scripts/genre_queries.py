"""Genre membership filters shared by title detail and annual snapshots.

Selection is OR: choosing Comedy and Romance includes a title with either tag
once. Genre names use the source's English labels. Values are SQL parameters.
"""


def genre_filter(genres):
    genres = tuple(dict.fromkeys(genre.strip() for genre in genres if genre.strip()))
    if not genres:
        raise ValueError('Provide at least one genre')
    placeholders = ', '.join('?' for _ in genres)
    # Fact alias f is used by both callers. EXISTS preserves the original grain
    # even when a title matches more than one of the selected genres.
    return f'''EXISTS (
        SELECT 1
        FROM bridge_genre_set_genre b
        JOIN dim_genre g ON g.genre_key = b.genre_key
        WHERE b.genre_set_key = f.genre_set_key
          AND g.genre COLLATE NOCASE IN ({placeholders})
    )''', genres


def find_titles_by_genres(conn, genres, limit=20):
    predicate, params = genre_filter(genres)
    return conn.execute(f'''
        SELECT f.title_id, f.primary_title, f.year_key, s.genre_set
        FROM fact_title_release f
        JOIN dim_genre_set s ON s.genre_set_key = f.genre_set_key
        WHERE {predicate}
        ORDER BY f.year_key DESC, f.title_id
        LIMIT ?
    ''', (*params, limit)).fetchall()


def release_count_by_genres(conn, genres):
    predicate, params = genre_filter(genres)
    return conn.execute(f'''
        SELECT COALESCE(SUM(f.title_count), 0)
        FROM fact_title_release f
        WHERE {predicate}
    ''', params).fetchone()[0]


def series_counts_by_genres(conn, genres, year):
    """Return known intervals covering year and started series with unknown end.

    The second value has indeterminate airing status. Do not combine the two
    values as a total of series in airing.
    """
    predicate, params = genre_filter(genres)
    return conn.execute(f'''
        SELECT COALESCE(SUM(f.known_interval_series_count), 0),
               COALESCE(SUM(f.started_series_unknown_end_count), 0)
        FROM fact_series_year_snapshot f
        WHERE {predicate} AND f.year_key = ?
    ''', (*params, year)).fetchone()
