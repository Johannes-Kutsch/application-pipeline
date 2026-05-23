import pytest

from application_pipeline.renderer import render


def test_render_returns_fixed_card_structure() -> None:
    result = render(rank=3, header="Engineer · Acme · Berlin", summary="Strong fit.")
    assert result == "# **3:** Engineer · Acme · Berlin\n\nStrong fit.\n"


@pytest.mark.parametrize("rank", [1, 2, 3, 4, 5])
def test_render_all_valid_ranks(rank: int) -> None:
    result = render(rank=rank, header="Role · Co · City", summary="Paragraph.")
    assert result == f"# **{rank}:** Role · Co · City\n\nParagraph.\n"
