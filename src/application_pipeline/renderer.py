def render(rank: int, header: str, summary: str, url: str, body: str) -> str:
    lines = header.split("\n", 1)
    title = lines[0]
    metadata = lines[1] if len(lines) > 1 else ""
    return (
        f"# **{rank}:** {title}\n"
        f"\n"
        f"{metadata}\n"
        f"{url}\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"{body}\n"
        f"\n"
        f"---\n"
    )
