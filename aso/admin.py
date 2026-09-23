from django.contrib import admin

from .models import App, Keyword, SearchResult


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("name", "bundle_id", "created_at")
    search_fields = ("name", "bundle_id")
    exclude = ("store_profiles",)
    readonly_fields = ("ratings_summary",)

    @admin.display(description="Store profile by storefront")
    def ratings_summary(self, obj):
        """One line per storefront, newest first: what the opportunity score
        measures this app by there. Read-only, and never raw JSON."""
        from django.utils.html import format_html_join

        from . import countries

        entries = []
        for code, entry in (obj.store_profiles or {}).items():
            entry = entry or {}
            entries.append((entry.get("checked_at") or "", code, entry))
        if not entries:
            return "Not read yet. It fills in the first time a keyword is scored for this app."
        entries.sort(reverse=True)
        rows = []
        for checked, code, entry in entries:
            average = entry.get("average")
            stars = f"{average:.1f} stars" if isinstance(average, (int, float)) else "no rating yet"
            released = (entry.get("released") or "")[:10] or "release date unknown"
            rows.append((
                countries.flag(code), countries.name(code),
                f"{int(entry.get('count') or 0):,}", stars, released, checked[:10],
            ))
        return format_html_join(
            "\n",
            "<div>{} {}: {} ratings, {}, released {} "
            "<span style='color:#888'>(checked {})</span></div>",
            rows,
        )


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ("keyword", "app", "created_at")
    list_filter = ("app",)
    search_fields = ("keyword",)


@admin.register(SearchResult)
class SearchResultAdmin(admin.ModelAdmin):
    list_display = (
        "keyword",
        "popularity_score",
        "difficulty_score",
        "country",
        "searched_at",
    )
    list_filter = ("country", "searched_at")
    readonly_fields = ("searched_at",)
