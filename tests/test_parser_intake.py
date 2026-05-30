from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fake_status_display import FakeStatusDisplay

from application_pipeline.content_gate import ContentGate
from application_pipeline.dedup import load as dedup_load
from application_pipeline.extracts import load_card_store
from application_pipeline.freshness_gate import FreshnessGate
from application_pipeline.parser_intake import Dropped, ParserIntake
from application_pipeline.parser_log import RunLog
from application_pipeline.parsers import PositionStub
from application_pipeline.parsers.types import EnrichResult


class _UnexpectedEnrichParser:
    def __enter__(self) -> "_UnexpectedEnrichParser":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def discover(self, query: object) -> list[PositionStub]:
        return []

    def enrich(self, stub: PositionStub) -> EnrichResult:
        raise AssertionError("discover-arm freshness drop must stop before enrich()")


class _PassThroughPreFilter:
    def admit(self, stub: PositionStub) -> bool:
        return True


def test_discover_freshness_drop_marks_matched_alias_expired_before_downstream_steps(
    tmp_path: Path,
) -> None:
    seen_path = tmp_path / ".seen.json"
    extracts_path = tmp_path / "extracts.json"
    logs_dir = tmp_path / "logs"
    canonical_url = "https://example.com/original"
    alias_url = "https://example.com/alias"

    seen_path.write_text(
        json.dumps(
            {
                "7": {
                    "urls": [canonical_url],
                    "company_lc": "acme",
                    "title_lc": "platform engineer",
                    "location_lc": "hamburg",
                    "status": "matched",
                    "status_last_changed": "2026-01-01",
                }
            }
        ),
        encoding="utf-8",
    )
    extracts_path.write_text(
        json.dumps(
            {
                "7": {
                    "header": "Platform Engineer\nAcme · Hamburg\n2026-01-01 · Senior",
                    "summary": "Persisted summary",
                    "body": "Persisted body",
                }
            }
        ),
        encoding="utf-8",
    )

    card_store = load_card_store(extracts_path)
    dedup_store = dedup_load(seen_path, card_store=card_store)
    run_log = RunLog(logs_dir)
    display = FakeStatusDisplay()
    freshness_gate = FreshnessGate(
        anchored_today=date(2026, 5, 30),
        max_listing_age_days=30,
        dedup=dedup_store,
        display=display,
        run_log=run_log,
    )
    intake = ParserIntake(
        parser=_UnexpectedEnrichParser(),
        freshness_gate=freshness_gate,
        deduplication=dedup_store,
        domain_pre_filter=_PassThroughPreFilter(),
        content_gate=ContentGate(display=display, run_log=run_log),
        card_store=card_store,
    )

    outcome = intake.process_position_stub(
        PositionStub(
            url=alias_url,
            title="Platform Engineer",
            source="test",
            company="Acme",
            location="Hamburg",
            posted_date=date(2026, 4, 1),
        )
    )

    assert isinstance(outcome, Dropped)
    assert outcome.reason == "freshness_discover"
    assert outcome.parser_row_metric == "freshness_dropped"

    seen_data = json.loads(seen_path.read_text(encoding="utf-8"))
    assert seen_data["7"]["status"] == "expired"
    assert seen_data["7"]["urls"] == [alias_url, canonical_url]

    assert card_store.get(7) is None

    transcript_rows = [
        json.loads(line)
        for line in (logs_dir / "pipeline" / "freshness.transcripts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert transcript_rows == [
        {
            "url": alias_url,
            "title": "Platform Engineer",
            "source": "test",
            "posted_date": "2026-04-01",
            "deadline": None,
            "anchored_today": "2026-05-30",
            "age_days": 59,
            "passes": False,
            "reason": "too_old",
            "gate_arm": "discover",
        }
    ]
