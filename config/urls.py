from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "StayLit Apparel — Business Manager"
admin.site.site_title = "StayLit Business Manager"
admin.site.index_title = "Administration"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("apps.web.urls")),
]
