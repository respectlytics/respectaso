from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("aso.urls")),
]

# The error pages, in RespectASO's own look (aso/error_views.py).
handler400 = "aso.error_views.bad_request"
handler403 = "aso.error_views.permission_denied"
handler404 = "aso.error_views.page_not_found"
handler500 = "aso.error_views.server_error"
