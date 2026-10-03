"""The Keywords page's "Get set up" card (docs/development/APP_SHELL_PLAN.md,
UI_REDESIGN_PLAN.md 6.3).

Each step says whether it is done from the app's own state; nothing is
stored but whether the card is folded (aso.ui_memory). The Apple Ads step is
open exactly while the old recommendation banner would have shown
(aso.context_processors.popularity_source's apple_recommend_connect), so the
recommendation keeps its rule: it goes only when Apple Ads is connected or the
user chooses to stay on the estimate, and the card cannot be removed while
that step is open, only folded.

The last step invites the reader to Pro while Pro is not unlocked, with the
top bar's own Pro button.

Free-tier module: the AI step imports aso_pro inside a guarded branch only.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.urls import reverse


def _pro_step() -> dict | None:
    """The invitation to Pro, while Pro is not unlocked
    (docs/development/PRO_AND_CLICKABLE_TEXT_PLAN.md): the same words and the
    same button as the top bar's Pro button, so it never says a second
    thing. It stays open until a license is active, which in the free
    edition is never, so the card can be folded but not finished there."""
    from .context_processors import pro_button

    context = pro_button(None)
    button = context["pro_button"]
    if not button:
        return None
    renew = button["url"] == context["renew_url"]
    return {"key": "pro",
            "label": "Unlock every tool again with Pro" if renew else "Unlock every tool with Pro",
            "done": False, "hint": "Every Pro tool, for every app you track.",
            "action": {"label": button["label"], "url": button["url"], "tone": "pro"}}


def closed_for_good() -> bool:
    """A Pro user closed the card for good (aso.ui_memory.SETUP_CHECKLIST_CLOSED)."""
    from . import ui_memory
    from .apple_ads import storage

    return bool(storage.load_block("ui").get(ui_memory.SETUP_CHECKLIST_CLOSED))


def checklist(request) -> dict | None:
    """The card's steps, or None when every step is done.

    Without Pro the Pro step stays open, so the card stays (it can be
    folded). With Pro it goes when every step is done, and a Pro user can
    also close it for good at any time (the owner, 2026-10-02)."""
    from . import ui_memory
    from .context_processors import popularity_source
    from .models import App, SearchResult
    from .pro_access import has_pro_license

    pro = has_pro_license()
    if pro and closed_for_good():
        return None
    if not App.objects.exists() and not SearchResult.objects.exists():
        return None   # the Keywords page's first run card speaks instead (KEYWORDS_PAGE_PLAN.md M2.5)
    apple_open = bool(popularity_source(request)["apple_recommend_connect"])
    steps = [
        {"key": "app", "label": "Add your app", "done": App.objects.exists(), "hint": "",
         "action": {"label": "Add your app", "url": reverse("aso:apps"), "tone": "primary"}},
        {"key": "keywords", "label": "Track your first keywords", "done": SearchResult.objects.exists(),
         "hint": "Type the words people would search for.", "action": None},
        {"key": "apple", "label": "Use Apple's own popularity numbers", "done": not apple_open,
         "hint": "Free, about 5 minutes, no ad spend.",
         "action": {"label": "Connect Apple Ads",
                    "url": reverse("aso:settings_popularity") + "#apple-connection", "tone": "apple"}},
    ]
    if django_apps.is_installed("aso_pro") and pro:
        from aso_pro.views import _has_ai_configured

        steps.append({"key": "ai", "label": "Choose your AI provider", "done": _has_ai_configured(),
                      "hint": "", "action": {"label": "Choose", "url": reverse("aso_pro:settings_ai"),
                                             "tone": "secondary"}})
    pro_step = _pro_step()
    if pro_step:
        steps.append(pro_step)
    done = sum(1 for step in steps if step["done"])
    if done == len(steps):
        return None
    return {
        "steps": steps,
        "done": done,
        "total": len(steps),
        "folded": bool(request.session.get(ui_memory.SETUP_CHECKLIST_FOLDED)),
        "can_close": pro,
        "apple_open": apple_open,
    }
