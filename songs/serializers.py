from rest_framework import serializers
from .models import MusicRegion, Artist, Song, Rating
import re, unicodedata

_PUNCT = re.compile(
    r"[‐-‒–—―ー〜~＾^・･·\.\,\(\)\[\]\{\}<>＜＞「」『』【】（）／/\\|:;!?\u3000\"'`]+"
)
_SPACE = re.compile(r"\s+")

_ROMAN_MAP = {
    "Ⅰ": "i",
    "Ⅱ": "ii",
    "Ⅲ": "iii",
    "Ⅳ": "iv",
    "Ⅴ": "v",
    "Ⅵ": "vi",
    "Ⅶ": "vii",
    "Ⅷ": "viii",
    "Ⅸ": "ix",
    "Ⅹ": "x",
}
_METRE_MAP = {"㍍": "m", "メートル": "m", "ﾒｰﾄﾙ": "m"}


def normalize_for_match(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s).strip().lower()

    # よくある表記ゆらぎの正規化
    for k, v in _ROMAN_MAP.items():
        s = s.replace(k.lower(), v)
    for k, v in _METRE_MAP.items():
        s = s.replace(k.lower(), v)

    # 記号を落とす & 空白を潰す
    s = _PUNCT.sub("", s)
    s = _SPACE.sub(" ", s).strip()
    return s


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


class MusicRegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MusicRegion
        fields = ["id", "code", "name"]


class ArtistSerializer(serializers.ModelSerializer):
    region = MusicRegionSerializer(read_only=True)

    class Meta:
        model = Artist
        fields = ["id", "name", "format_name", "region"]


class SongSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)

    class Meta:
        model = Song
        fields = ["id", "title", "format_title", "is_cover", "artist"]


class RatingSerializer(serializers.ModelSerializer):
    """
    自分の採点をCRUDするためのSerializer。
    POST/PATCHでは song はIDで渡します。
    """

    song = serializers.PrimaryKeyRelatedField(queryset=Song.objects.all())
    score = serializers.IntegerField(min_value=0, max_value=100)

    class Meta:
        model = Rating
        fields = ["id", "song", "score", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SongLookupCreateSerializer(serializers.Serializer):
    artist = serializers.CharField(max_length=100)
    title = serializers.CharField(max_length=100)
    region_code = serializers.CharField(
        max_length=10, required=False, allow_null=True, allow_blank=True
    )
    is_cover = serializers.BooleanField(required=False, allow_null=True)

    def lookup_only(self, validated):
        in_artist_raw = (validated["artist"] or "").strip()
        in_title_raw = (validated["title"] or "").strip()
        if not in_artist_raw or not in_title_raw:
            raise serializers.ValidationError({"detail": "not_found"})

        # 1) まず従来どおり（DB値そのままの大小無視）
        exact_qs = (
            Song.objects.select_related("artist")
            .only("id", "title", "artist_id", "artist__name")
            .filter(artist__name__iexact=in_artist_raw, title__iexact=in_title_raw)
        )
        c = exact_qs.count()
        if c == 1:
            s = exact_qs.first()
            return {
                "song_id": s.id,
                "artist_id": s.artist_id,
                "created": {"region": False, "artist": False, "song": False},
            }
        if c > 1:
            return self._multiple(exact_qs)

        # 2) 正規化してから Python 側で完全一致比較（DBは変更しない）
        norm_artist = normalize_for_match(in_artist_raw)
        norm_title = normalize_for_match(in_title_raw)

        # 候補の絞り込み：icontains で広めに拾うが上限あり
        # キーワードは最初の有意トークンに寄せる（無駄ヒットを減らす）
        artist_token = (norm_artist.split(" ") or [""])[0]
        title_token = (norm_title.split(" ") or [""])[0]

        cand_qs = (
            Song.objects.select_related("artist")
            .only("id", "title", "artist_id", "artist__name")
            .filter(artist__name__icontains=artist_token, title__icontains=title_token)
            .order_by("artist__name", "title")[:200]  # 上限で保護
        )

        matches = []
        for s in cand_qs:
            if (
                normalize_for_match(s.artist.name) == norm_artist
                and normalize_for_match(s.title) == norm_title
            ):
                matches.append(s)

        if len(matches) == 1:
            s = matches[0]
            return {
                "song_id": s.id,
                "artist_id": s.artist_id,
                "created": {"region": False, "artist": False, "song": False},
            }
        if len(matches) > 1:
            return self._multiple(matches)

        # 見つからない
        raise serializers.ValidationError(
            {
                "detail": "not_found",
                "echo": {"artist_in": norm_artist, "title_in": norm_title},
            }
        )

    def _multiple(self, qs):
        # クエリセット/リストどちらでもOK
        arr = list(qs)[:50]
        raise serializers.ValidationError(
            {
                "detail": "multiple_matches",
                "candidates": [
                    {
                        "song_id": s.id,
                        "artist_id": s.artist_id,
                        "artist": s.artist.name,
                        "title": s.title,
                    }
                    for s in arr
                ],
            }
        )

    def create(self, validated):
        # 既定は作成しない仕様なので、“?create=true” の時だけ呼ばれる想定
        try:
            return self.lookup_only(validated)
        except serializers.ValidationError as e:
            if not (
                isinstance(e.detail, dict) and e.detail.get("detail") == "not_found"
            ):
                raise
        # 作成はしない運用なら、ここに来た場合も not_found をそのまま上げる方が安全
        raise serializers.ValidationError({"detail": "not_found"})

        artist_name = _norm(validated["artist"])
        title = _norm(validated["title"])
        is_cover = validated.get("is_cover", None)

        artist, c_artist = Artist.objects.get_or_create(
            name=artist_name, region=None, defaults={"format_name": artist_name}
        )
        song, c_song = Song.objects.get_or_create(
            title=title,
            artist=artist,
            defaults={"format_title": title, "is_cover": is_cover},
        )
        if is_cover is not None and song.is_cover != is_cover:
            song.is_cover = is_cover
            song.save(update_fields=["is_cover"])

        return {
            "song_id": song.id,
            "artist_id": artist.id,
            "created": {"region": False, "artist": c_artist, "song": c_song},
        }
