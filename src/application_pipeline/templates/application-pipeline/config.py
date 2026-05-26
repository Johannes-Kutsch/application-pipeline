from application_pipeline import SourceEntry

SOURCES = [
    SourceEntry(parser_type="bundesagentur_api"),
    # SourceEntry(parser_type="stellen_hamburg_api"),  # Hamburg public-sector job board (API)
    # SourceEntry(parser_type="jobs_beim_staat_html"),  # German public-sector aggregator (HTML scrape)
]

LOCATIONS: list[str] = []

INCLUDE_REMOTE = True

MAX_LISTING_AGE_DAYS = 180  # Freshness Gate threshold: listings older than this (in days) are dropped (>= 1).

CLAUDE_CLASSIFY_PARALLELISM = 4  # Relevance Classifier parallel worker pool size (>= 1); increase to classify faster, decrease to reduce Claude API concurrency.

DEDUP_COOLDOWN_DAYS = 30  # How long (in days) a seen entry in state "selected_by_judge" or "expired" suppresses duplicate detection before decaying (>= 1).

# LAYOUT defaults to layout.py in the same directory as this file (required).
# Set LAYOUT = None to skip and use the built-in minimal template instead.
