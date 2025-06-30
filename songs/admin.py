from django.contrib import admin
from .models import Artist, Song, Rating
from import_export import resources
from import_export.admin import ImportExportModelAdmin


# 共通のベース管理クラス
class BaseResourceAdmin(ImportExportModelAdmin):
    pass


class ArtistResource(resources.ModelResource):
    class Meta:
        model = Artist


class ArtistAdmin(BaseResourceAdmin):
    resource_class = ArtistResource
    search_fields = ["name"]


class SongResource(resources.ModelResource):
    class Meta:
        model = Song


class SongAdmin(BaseResourceAdmin):
    resource_class = SongResource
    list_display = ["title", "artist"]
    search_fields = ["title", "artist__name"]


class RatingResource(resources.ModelResource):
    class Meta:
        model = Rating


class RatingAdmin(BaseResourceAdmin):
    resource_class = RatingResource
    list_display = ["song", "song__artist", "user", "score"]
    search_fields = ["user__username", "song__title", "song__artist__name"]


admin.site.register(Artist, ArtistAdmin)
admin.site.register(Song, SongAdmin)
admin.site.register(Rating, RatingAdmin)
