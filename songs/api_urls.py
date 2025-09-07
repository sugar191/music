from django.urls import path
from . import api_views

urlpatterns = [
    path("ratings/score", api_views.get_score, name="get_score"),  # GET
    path("ratings/score/update", api_views.update_score, name="update"),  # POST
    path(
        "ratings/export", api_views.export_ratings, name="export_ratings"
    ),  # ★ 追加: GET
]
