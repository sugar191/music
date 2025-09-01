from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views_api import SongViewSet, RatingViewSet

router = DefaultRouter()
router.register(r"songs", SongViewSet, basename="songs")
router.register(r"ratings", RatingViewSet, basename="ratings")

urlpatterns = [
    path("", include(router.urls)),
]
