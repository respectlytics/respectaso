"""
Opportunity tools — opportunity scoring and multi-country comparison.
"""

from mcp_server.src.app import mcp


@mcp.tool()
def analyze_opportunity(keyword: str, country: str = "us") -> dict:
    """
    Return the opportunity score and targeting advice for a keyword.

    Combines popularity and difficulty into an opportunity score (0–100) and
    provides a human-readable classification and targeting recommendation
    (e.g. 'Sweet Spot', 'Hidden Gem', 'Avoid') explaining whether this
    keyword is worth pursuing for ASO.

    Args:
        keyword: The App Store keyword to evaluate (e.g. 'meditation app').
        country: Two-letter App Store country code (default: 'us').

    Returns:
        Dict with keys: keyword, country, popularity_score, difficulty_score,
        opportunity_score, classification, targeting_advice (icon, label,
        description), difficulty_breakdown.
        Returns an error key if the iTunes API call fails.
    """
    # TODO: implement
    raise NotImplementedError("analyze_opportunity is not yet implemented")


@mcp.tool()
def compare_countries(keyword: str, countries: list[str] | None = None) -> dict:
    """
    Scan a keyword across multiple App Store regions and rank by opportunity.

    For each country, fetches live iTunes data and computes popularity,
    difficulty, and opportunity score. Returns a ranked list so you can
    identify which markets offer the best ranking opportunity for a keyword.

    Args:
        keyword: The keyword to evaluate across markets (e.g. 'recipe app').
        countries: List of two-letter country codes to scan. Defaults to a
            representative set of 10 major markets: us, gb, ca, au, de,
            fr, jp, br, mx, in.

    Returns:
        Dict with keys: keyword, results (list sorted by opportunity desc —
        each entry has country, popularity, difficulty, opportunity,
        classification, top_competitor, competitor_count).
        Returns an error key if any iTunes API call fails.
    """
    # TODO: implement
    raise NotImplementedError("compare_countries is not yet implemented")
