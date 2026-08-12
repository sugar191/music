from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    path("admin/", admin.site.urls),
    # ログインは songs/urls.py の "login/" に一本化した。
    # 以前はここにも name="login" があり、2つ登録された同名URLの
    # 「後勝ち」で reverse('login') が /login/ を指す一方、
    # LOGIN_URL 既定値の /accounts/login/ にも飛べる状態になっていた。
    # 古いブックマーク救済のためリダイレクトだけ残す。
    path(
        "accounts/login/",
        RedirectView.as_view(pattern_name="login", query_string=True),
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("songs.urls")),
    path("api/", include("songs.api_urls")),
    path("api/auth/token/", obtain_auth_token),
]
