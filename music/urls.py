from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),  # ログイン
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("songs.urls")),
    path("api/", include("songs.api_urls")),
    path("api/auth/token/", obtain_auth_token),
]
