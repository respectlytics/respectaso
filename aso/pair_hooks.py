"""Other features hear when the user removes rows from Search History.

A Search History row is a keyword in a storefront. The Pro build's Rival
Tracker follows some of those pairs; when the user deletes a row on the
Dashboard, the pair stops being followed too, because a Rival Tracker
keyword is a Search History keyword, not a copy of one.

Free-tier module: it names no other app. aso_pro registers its listener in
its ready().
"""

import logging

logger = logging.getLogger(__name__)

# Callables taking a set of (keyword_id, country) pairs that no longer have
# any row in Search History.
removed_listeners: list = []


def pairs_removed(pairs) -> None:
    """Tell every listener which pairs left Search History."""
    pairs = set(pairs or ())
    if not pairs:
        return
    for listener in removed_listeners[:]:
        try:
            listener(pairs)
        except Exception:  # a listener must never fail the deletion
            logger.exception("A Search History removal listener failed")
