from application_pipeline import SourceEntry

KEYWORDS = [
    "software engineer",
    "software developer",
    "backend engineer",
    "python developer",
]

SKILLS = [
    "Python",
    "SQL",
    "Git",
    "Docker",
]

SOURCES = [
    SourceEntry(parser_type="bundesagentur_api"),
]

LOCATIONS: list[str] = []

INCLUDE_REMOTE = True
