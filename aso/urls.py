from django.urls import path

from . import views

app_name = "aso"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("methodology/", views.methodology_view, name="methodology"),
    path("setup/", views.setup_view, name="setup"),
    path("search/", views.search_view, name="search"),
    path("opportunity/", views.opportunity_view, name="opportunity"),
    path("opportunity/search/", views.opportunity_search_view, name="opportunity_search"),
    path("opportunity/search-country/", views.opportunity_search_country_view, name="opportunity_search_country"),
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
    path("auto-refresh/status/", views.auto_refresh_status_view, name="auto_refresh_status"),
    path("keywords/<int:keyword_id>/trend/", views.keyword_trend_view, name="keyword_trend"),
    path("version-check/", views.version_check_view, name="version_check"),
    path("download/dmg/", views.download_dmg_view, name="download_dmg"),
    # Pro promotional pages (shown in free version nav)
    path("pro/ai-researcher/", views.pro_promo_researcher_view, name="pro_promo_researcher"),
    path("pro/ai-competitor/", views.pro_promo_competitor_view, name="pro_promo_competitor"),
    path("pro/simulator/", views.pro_promo_simulator_view, name="pro_promo_simulator"),
]
