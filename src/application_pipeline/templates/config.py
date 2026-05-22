from application_pipeline import SourceEntry

SOURCES = [
    SourceEntry(parser_type="bundesagentur_api"),
]

LOCATIONS: list[str] = []

INCLUDE_REMOTE = True

MAX_LISTING_AGE_DAYS = 180  # Freshness Gate threshold: listings older than this (in days) are dropped (>= 1).

CLAUDE_CLASSIFY_PARALLELISM = 4  # Relevance Classifier parallel worker pool size (>= 1); increase to classify faster, decrease to reduce Claude API concurrency.

# LAYOUT defaults to layout.py in the same directory as this file (required).
# Set LAYOUT = None to skip and use the built-in minimal template instead.
