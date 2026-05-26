import pytest

from application_pipeline.renderer import render


def test_render_full_card_layout() -> None:
    header = "Senior Engineer\nAcme · Berlin · On-site\n2026-01-01 · Senior · €80k"
    result = render(
        rank=1,
        header=header,
        summary="A strong fit for the role.",
        url="https://example.com/job/123",
        body="Full job description here.",
    )
    expected = (
        "# **1:** Senior Engineer\n"
        "\n"
        "Acme · Berlin · On-site\n"
        "2026-01-01 · Senior · €80k\n"
        "https://example.com/job/123\n"
        "\n"
        "A strong fit for the role.\n"
        "\n"
        "---\n"
        "\n"
        "Full job description here.\n"
        "\n"
        "---\n"
    )
    assert result == expected


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_render_all_valid_ranks(rank: int) -> None:
    result = render(
        rank=rank,
        header="Role · Co · City\nCompany · City\n2026-01-01",
        summary="Paragraph.",
        url="https://example.com",
        body="Body text.",
    )
    assert f"# **{rank}:** Role · Co · City" in result
