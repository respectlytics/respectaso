from django.urls import path

from . import views

app_name = "aso"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("methodology/", views.methodology_view, name="methodology"),
    path("setup/", views.setup_view, name="setup"),
    path("search/", views.search_view, name="search"),
    path("history/", views.history_view, name="history"),
    path("opportunity/", views.opportunity_view, name="opportunity"),
    path("opportunity/search/", views.opportunity_search_view, name="opportunity_search"),
    path("opportunity/save/", views.opportunity_save_view, name="opportunity_save"),
    path("export/history.csv", views.export_history_csv_view, name="export_history_csv"),
    path("apps/", views.apps_view, name="apps"),
    path("apps/lookup/", views.app_lookup_view, name="app_lookup"),
    path("apps/<int:app_id>/delete/", views.app_delete_view, name="app_delete"),
    path("keywords/<int:keyword_id>/delete/", views.keyword_delete_view, name="keyword_delete"),
    path("results/<int:result_id>/delete/", views.result_delete_view, name="result_delete"),
    path("keywords/bulk-delete/", views.keywords_bulk_delete_view, name="keywords_bulk_delete"),
    path("keywords/<int:keyword_id>/refresh/", views.keyword_refresh_view, name="keyword_refresh"),
    path("keywords/bulk-refresh/", views.keywords_bulk_refresh_view, name="keywords_bulk_refresh"),
]
