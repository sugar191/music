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


class SongResource(resources.ModelResource):
    class Meta:
        model = Song


class SongAdmin(BaseResourceAdmin):
    resource_class = SongResource


class RatingResource(resources.ModelResource):
    class Meta:
        model = Rating


class RatingAdmin(BaseResourceAdmin):
    resource_class = RatingResource


admin.site.register(Artist, ArtistAdmin)
admin.site.register(Song, SongAdmin)
admin.site.register(Rating, RatingAdmin)
