"""Default project paths, independent of the process working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA = PROJECT_ROOT / 'data' / 'raw' / 'title.basics.tsv'
RATINGS_DATA = PROJECT_ROOT / 'data' / 'raw' / 'title.ratings.tsv'
REFINED = PROJECT_ROOT / 'data' / 'refined'
WAREHOUSE = PROJECT_ROOT / 'data' / 'warehouse' / 'warehouse.db'
SCHEMA = PROJECT_ROOT / 'sql' / 'warehouse_schema.sql'
MEDIA = PROJECT_ROOT / 'media'
