from django.contrib import admin
from .models import Artist, Song, Rating, MusicRegion, ArtistYearPreference, UserProfile
from import_export import resources
from import_export.admin import ImportExportModelAdmin


# 共通のベース管理クラス
class BaseResourceAdmin(ImportExportModelAdmin):
    pass


class MusicRegionResource(resources.ModelResource):
    class Meta:
        model = MusicRegion


class MusicRegionAdmin(BaseResourceAdmin):
    resource_class = MusicRegionResource
    search_fields = ["name"]


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


class ArtistYearPreferenceResource(resources.ModelResource):
    class Meta:
        model = ArtistYearPreference


class ArtistYearPreferenceAdmin(BaseResourceAdmin):
    resource_class = ArtistYearPreferenceResource
    list_display = ["user", "artist", "year", "score"]
    search_fields = ["user__username", "artist__name"]
    list_filter = ["user", "year", "score"]


class UserProfileResource(resources.ModelResource):
    class Meta:
        model = UserProfile


class UserProfileAdmin(BaseResourceAdmin):
    resource_class = UserProfileResource
    list_display = ["user", "birth_year"]
    search_fields = ["user__username"]
    list_filter = ["birth_year"]


admin.site.register(MusicRegion, MusicRegionAdmin)
admin.site.register(Artist, ArtistAdmin)
admin.site.register(Song, SongAdmin)
admin.site.register(Rating, RatingAdmin)
admin.site.register(ArtistYearPreference, ArtistYearPreferenceAdmin)
admin.site.register(UserProfile, UserProfileAdmin)
