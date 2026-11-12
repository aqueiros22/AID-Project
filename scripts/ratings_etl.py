"""Join current ratings to eligible releases, keeping one row per title."""
import csv
import hashlib
import re
from datetime import date
from pathlib import Path


def add_ratings(output, source, extraction_date):
    ratings = {}
    provenance = None
    if source is not None:
        date.fromisoformat(extraction_date)
        source = Path(source)
        with source.open(encoding='utf-8', newline='') as handle:
            reader = csv.DictReader(handle, delimiter='\t', quoting=csv.QUOTE_NONE)
            if reader.fieldnames != ['tconst', 'averageRating', 'numVotes']:
                raise ValueError('Invalid ratings columns')
            for row in reader:
                title = row['tconst']
                if None in row or not re.fullmatch(r'tt\d+', title or ''):
                    raise ValueError('Malformed ratings row')
                if title in ratings:
                    raise ValueError('Duplicate rating title: ' + title)
                rating, votes = float(row['averageRating']), int(row['numVotes'])
                if not 1 <= rating <= 10 or votes <= 0:
                    raise ValueError('Invalid rating or vote count: ' + title)
                ratings[title] = (rating, votes)
        with source.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        provenance = dict(source_file=str(source.resolve()), sha256=digest,
                          size_bytes=source.stat().st_size,
                          download_date=extraction_date,
                          url='https://datasets.imdbws.com/title.ratings.tsv.gz')
    total = len(ratings)
    matched = votes_total = 0
    with (output / 'fact_title_rating_snapshot.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['title_id', 'year_key', 'title_type_key',
                         'genre_set_key', 'audience_key', 'average_rating', 'num_votes', 'rated_title_count'])
        with (output / 'fact_title_release.csv').open(encoding='utf-8', newline='') as releases:
            for row in csv.DictReader(releases):
                rating = ratings.pop(row['title_id'], None)
                if rating is not None:
                    writer.writerow([row['title_id'], row['year_key'], row['title_type_key'],
                                     row['genre_set_key'], row['audience_key'], *rating, 1])
                    matched += 1
                    votes_total += rating[1]
    return dict(rating_facts=matched, rating_source_rows=total,
                ratings_without_eligible_title=len(ratings), rating_votes_total=votes_total,
                ratings_provenance=provenance)
