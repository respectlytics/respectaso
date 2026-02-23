from django.contrib import admin

from .models import App, Keyword, SearchResult


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("name", "bundle_id", "created_at")
    search_fields = ("name", "bundle_id")


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
