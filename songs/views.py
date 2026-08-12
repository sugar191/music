import os
import re
import json
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Q,
    OuterRef,
    Subquery,
    IntegerField,
    DecimalField,
    Count,
    Min,
)
from django.db.models.functions import Coalesce, Lower
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from urllib.parse import urlencode
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from mutagen.easyid3 import EasyID3
from .models import (
    Artist,
    Song,
    Rating,
    MusicRegion,
    ArtistYearPreference,
    UserProfile,
)
from .services import (
    TOP_NS,
    call_artist_song_top_n,
    call_artist_insufficient_songs,
    call_artist_top_n_multi,
    call_creator_song_top_n,
    call_creator_insufficient_songs,
    call_creator_top_n_multi,
    call_song_ranking,
    count_song_ranking,
)

CREATOR_TYPE_LABELS = {
    "lyricist": "作詞",
    "composer": "作曲",
    "year": "年",
}

MUSIC_DIR = r"C:\Users\pawab\Music"


# ランキング系4画面（歌手別/作詞別/作曲別/年別TOP）の分割表示設定。
# 全件を一度に返すとHTMLが数MBになるため、親カード→その他の順に少しずつ追加する。
RANKING_PARENT_PAGE_SIZE = 20
RANKING_OTHERS_PAGE_SIZE = 200

# 「もっと見る」の残件数表示に使う親の単位ラベル
RANKING_PARENT_LABELS = {
    "artist": "歌手",
    "lyricist": "作詞",
    "composer": "作曲",
    "year": "年",
}


def _resolve_ranking_params(request):
    """ランキング系4画面の共通パラメータ解決（region_id / selected_user / top_n）"""
    if "region_id" not in request.GET:
        # region_id パラメータがまったくない場合の処理（例：初期値1を使う）
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    try:
        top_n = int(request.GET.get("top_n", 5))
    except ValueError:
        top_n = 5

    return region_id, selected_user, top_n


def _ranking_dataset(kind, user_id, top_n, region_id):
    """
    ランキング系4画面のデータを共通形式で組み立てる。
    kind: 'artist' | 'lyricist' | 'composer' | 'year'
    戻り値: (rankings, insufficient_songs)
      rankings: [{parent_rank, parent_name, parent_link, total_score, songs:[...]}, ...]
    """
    if kind == "artist":
        top_n_data = call_artist_song_top_n(user_id, top_n, region_id)
        insufficient_data = call_artist_insufficient_songs(user_id, top_n, region_id)

        # artistごとに曲をグルーピング
        grouped = defaultdict(list)
        for row in top_n_data:
            key = (
                row["artist_rank"],
                row["artist_id"],
                row["artist_name"],
                row["total_score"],
            )
            # song側に共通キー row_rank を付与（テンプレートで kind 非依存に扱うため）
            row["row_rank"] = row.get("rank_artist")
            grouped[key].append(row)

        # ソートしてリスト化
        sorted_parents = sorted(grouped.items(), key=lambda x: x[0][0])

        # テンプレート共通形式（parent_rank/parent_name/parent_link）に正規化
        rankings = []
        for key, songs in sorted_parents:
            artist_rank, artist_id, artist_name, total_score = key
            rankings.append(
                {
                    "parent_rank": artist_rank,
                    "parent_name": artist_name,
                    "parent_link": reverse("artist_songs", args=[artist_id]),
                    "total_score": total_score,
                    "songs": songs,
                }
            )
    else:
        top_n_data = call_creator_song_top_n(user_id, top_n, region_id, kind)
        insufficient_data = call_creator_insufficient_songs(
            user_id, top_n, region_id, kind
        )

        # クリエイターごとに曲をグルーピング
        grouped = defaultdict(list)
        for row in top_n_data:
            key = (row["creator_rank"], row["creator"], row["total_score"])
            # song側に共通キー row_rank を付与
            row["row_rank"] = row.get("rank_creator")
            grouped[key].append(row)

        sorted_parents = sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1] or ""))

        # テンプレート共通形式（parent_rank/parent_name/parent_link）に正規化
        creator_songs_url = reverse("creator_songs")
        rankings = []
        for key, songs in sorted_parents:
            creator_rank, creator_name, total_score = key
            qs = urlencode({"type": kind, "name": creator_name})
            rankings.append(
                {
                    "parent_rank": creator_rank,
                    "parent_name": creator_name,
                    "parent_link": f"{creator_songs_url}?{qs}",
                    "total_score": total_score,
                    "songs": songs,
                }
            )

    # その他テーブルの曲にも共通キー row_rank を付与
    for s in insufficient_data:
        s["row_rank"] = s.get("rank_within_insufficient")

    return rankings, insufficient_data


def _load_more_label(kind, remaining_parents, remaining_others):
    """「もっと見る」の文言。続きがなければ空文字を返す。"""
    if remaining_parents > 0:
        return f"もっと見る（残り{RANKING_PARENT_LABELS[kind]} {remaining_parents} 件）"
    if remaining_others > 0:
        return f"もっと見る（その他 残り {remaining_others} 曲）"
    return ""


def _ranking_page_context(kind, rankings, insufficient_songs):
    """初回描画ぶん（親カードの先頭ページのみ）と「もっと見る」の状態を返す。"""
    parents = rankings[:RANKING_PARENT_PAGE_SIZE]
    remaining_parents = len(rankings) - len(parents)
    return {
        "kind": kind,
        "rankings": parents,
        # 「その他」は親カードを使い切ってから読み込むため初回は空
        "insufficient_songs": [],
        "has_others": bool(insufficient_songs),
        "parent_offset": len(parents),
        "others_offset": 0,
        "load_more_label": _load_more_label(
            kind, remaining_parents, len(insufficient_songs)
        ),
    }


# 歌手別TOP
@login_required
def ranking_view(request):
    # プルダウンの選択肢を取得
    ranking_options = [5, 10, 15, 20]
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user, top_n = _resolve_ranking_params(request)

    rankings, insufficient_data = _ranking_dataset(
        "artist", selected_user.id, top_n, region_id
    )

    context = {
        "ranking_options": ranking_options,
        "regions": regions,
        "all_users": users,
        "top_n": top_n,
        "region_id": region_id,
        "selected_user": selected_user,
        "is_own_page": selected_user == request.user,
    }
    context.update(_ranking_page_context("artist", rankings, insufficient_data))

    return render(request, "songs/artist_ranking.html", context)


# 歌手ランキング
@login_required
def artist_list_view(request):
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    # 入力したパラメータを取得
    if "region_id" not in request.GET:
        # region_id パラメータがまったくない場合の処理（例：初期値1を使う）
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None
    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    # 4つの top_n を1クエリでまとめて取得し、共通形式
    # (display_name/display_link/display_rank/total_score) に正規化する
    rows = call_artist_top_n_multi(selected_user.id, region_id)

    top_lists = []
    for n in TOP_NS:
        ranked = sorted(
            (r for r in rows if r[f"rank_{n}"] is not None),
            key=lambda r, n=n: r[f"order_{n}"],
        )
        top_lists.append(
            (
                f"TOP{n}",
                [
                    {
                        "display_name": r["artist_name"],
                        "display_link": reverse("artist_songs", args=[r["artist_id"]]),
                        "display_rank": r[f"rank_{n}"],
                        "total_score": r[f"total_{n}"],
                    }
                    for r in ranked
                ],
            )
        )

    return render(
        request,
        "songs/top_grid.html",
        {
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "kind": "artist",
            "kind_label": "歌手",
            "top_lists": top_lists,
        },
    )


# 作詞別TOP / 作曲別TOP（creator_type = 'lyricist' or 'composer'）
@login_required
def creator_list_view(request, creator_type):
    if creator_type not in CREATOR_TYPE_LABELS:
        return redirect("artist_list")

    ranking_options = [5, 10, 15, 20]
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user, top_n = _resolve_ranking_params(request)

    rankings, insufficient_data = _ranking_dataset(
        creator_type, selected_user.id, top_n, region_id
    )

    template_map = {
        "lyricist": "songs/lyricist_ranking.html",
        "composer": "songs/composer_ranking.html",
        "year": "songs/year_ranking.html",
    }

    context = {
        "ranking_options": ranking_options,
        "regions": regions,
        "all_users": users,
        "top_n": top_n,
        "region_id": region_id,
        "selected_user": selected_user,
        "is_own_page": selected_user == request.user,
        "creator_type": creator_type,
        "creator_label": CREATOR_TYPE_LABELS[creator_type],
    }
    context.update(_ranking_page_context(creator_type, rankings, insufficient_data))

    return render(request, template_map[creator_type], context)


# 曲メタ情報の表示フラグ。各テンプレートの include 指定と一致させること。
RANKING_KIND_FLAGS = {
    "artist": {
        "show_artist": False,
        "show_lyricist": True,
        "show_composer": True,
        "show_year": True,
    },
    "lyricist": {
        "show_artist": True,
        "show_lyricist": False,
        "show_composer": True,
        "show_year": True,
    },
    "composer": {
        "show_artist": True,
        "show_lyricist": True,
        "show_composer": False,
        "show_year": True,
    },
    "year": {
        "show_artist": True,
        "show_lyricist": True,
        "show_composer": True,
        "show_year": False,
    },
}


@login_required
def ranking_more_view(request):
    """
    ランキング系4画面共通の「もっと見る」。
    親カードが残っていれば親を、使い切っていれば「その他」の行を追加で返す。
    """
    kind = request.GET.get("kind", "artist")
    if kind not in RANKING_KIND_FLAGS:
        return JsonResponse({"error": "不正な kind です"}, status=400)

    region_id, selected_user, top_n = _resolve_ranking_params(request)

    def _offset(name):
        try:
            return max(int(request.GET.get(name, 0)), 0)
        except ValueError:
            return 0

    parent_offset = _offset("parent_offset")
    others_offset = _offset("others_offset")

    rankings, insufficient_songs = _ranking_dataset(
        kind, selected_user.id, top_n, region_id
    )

    parents = rankings[parent_offset : parent_offset + RANKING_PARENT_PAGE_SIZE]
    if parents:
        # 親カードが残っているうちは「その他」に進まない
        others = []
    else:
        others = insufficient_songs[
            others_offset : others_offset + RANKING_OTHERS_PAGE_SIZE
        ]

    parent_offset += len(parents)
    others_offset += len(others)

    is_own_page = selected_user == request.user

    parents_html = ""
    if parents:
        card_context = {"rankings": parents, "is_own_page": is_own_page}
        card_context.update(RANKING_KIND_FLAGS[kind])
        parents_html = render_to_string(
            "songs/partials/_ranking_cards.html", card_context, request=request
        )

    others_html = ""
    if others:
        others_html = render_to_string(
            "songs/partials/_others_rows.html",
            {"insufficient_songs": others, "is_own_page": is_own_page},
            request=request,
        )

    return JsonResponse(
        {
            "parents_html": parents_html,
            "others_html": others_html,
            "parent_offset": parent_offset,
            "others_offset": others_offset,
            "load_more_label": _load_more_label(
                kind,
                len(rankings) - parent_offset,
                len(insufficient_songs) - others_offset,
            ),
        }
    )


# 作詞TOP / 作曲TOP / 年TOP（artist_list と共通の top_grid.html を使用）
@login_required
def creator_grid_view(request, creator_type):
    if creator_type not in CREATOR_TYPE_LABELS:
        return redirect("artist_list")

    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    if "region_id" not in request.GET:
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    # 4つの top_n を1クエリでまとめて取得し、共通形式
    # (display_name/display_link/display_rank/total_score) に正規化する
    creator_songs_url = reverse("creator_songs")
    rows = call_creator_top_n_multi(selected_user.id, region_id, creator_type)

    top_lists = []
    for n in TOP_NS:
        ranked = sorted(
            (r for r in rows if r[f"rank_{n}"] is not None),
            key=lambda r, n=n: (r[f"rank_{n}"], r["creator"] or ""),
        )
        items = []
        for r in ranked:
            qs = urlencode({"type": creator_type, "name": r["creator"]})
            items.append(
                {
                    "display_name": r["creator"],
                    "display_link": f"{creator_songs_url}?{qs}",
                    "display_rank": r[f"rank_{n}"],
                    "total_score": r[f"total_{n}"],
                }
            )
        top_lists.append((f"TOP{n}", items))

    return render(
        request,
        "songs/top_grid.html",
        {
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "kind": creator_type,
            "kind_label": CREATOR_TYPE_LABELS[creator_type],
            "top_lists": top_lists,
        },
    )


# 作詞ランク / 作曲ランク / 年ランク（artist_rank_matrix と共通の rank_matrix.html を使用）
@login_required
def creator_matrix_view(request, creator_type):
    if creator_type not in CREATOR_TYPE_LABELS:
        return redirect("artist_rank_matrix")

    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    if "region_id" not in request.GET:
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    # 4つの top_n を1クエリでまとめて取得（従来は top_n ごとに4回実行していた）
    creator_songs_url = reverse("creator_songs")
    rows_by_creator = {}
    for r in call_creator_top_n_multi(selected_user.id, region_id, creator_type):
        if all(r[f"rank_{n}"] is None for n in TOP_NS):
            # どの top_n の条件も満たさないクリエイターは従来どおり表示しない
            continue
        creator = r["creator"]
        qs = urlencode({"type": creator_type, "name": creator})
        row = {
            "display_name": creator,
            "display_link": f"{creator_songs_url}?{qs}",
        }
        for n in TOP_NS:
            if r[f"rank_{n}"] is not None:
                row[f"rank_{n}"] = r[f"rank_{n}"]
                row[f"score_{n}"] = r[f"total_{n}"]
        rows_by_creator[creator] = row

    BIG = 10**9

    def sort_key(row):
        return (
            row.get("rank_5", BIG),
            row.get("rank_10", BIG),
            row.get("rank_15", BIG),
            row.get("rank_20", BIG),
            row.get("display_name", ""),
        )

    matrix_rows = sorted(rows_by_creator.values(), key=sort_key)

    return render(
        request,
        "songs/rank_matrix.html",
        {
            "kind": creator_type,
            "kind_label": CREATOR_TYPE_LABELS[creator_type],
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "rows": matrix_rows,
        },
    )


# 曲の作詞者・作曲者・年を更新するAJAXエンドポイント
@require_POST
@login_required
def update_song_credits_view(request):
    song_id = request.POST.get("song_id")
    field = request.POST.get("field")  # 'lyricist' / 'composer' / 'year'
    value = request.POST.get("value", "").strip()

    if field not in ("lyricist", "composer", "year"):
        return JsonResponse({"success": False, "error": "無効なフィールドです"}, status=400)

    try:
        song = Song.objects.get(pk=song_id)
    except Song.DoesNotExist:
        return JsonResponse({"success": False, "error": "曲が存在しません"}, status=404)

    if field == "year":
        if value:
            try:
                value_to_save = int(value)
            except ValueError:
                return JsonResponse(
                    {"success": False, "error": "年は数値で入力してください"}, status=400
                )
        else:
            value_to_save = None
    else:
        value_to_save = value or None

    setattr(song, field, value_to_save)
    song.save(update_fields=[field])
    return JsonResponse({"success": True, "value": value_to_save})


@login_required
def artist_rank_matrix_view(request):
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    # region_id（他画面と揃える）
    if "region_id" not in request.GET:
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    # 4つの top_n を1クエリでまとめて取得（従来は top_n ごとに4回実行していた）
    matrix_rows = []
    for r in call_artist_top_n_multi(selected_user.id, region_id):
        if all(r[f"rank_{n}"] is None for n in TOP_NS):
            # どの top_n の条件も満たさない歌手は従来どおり表示しない
            continue
        aid = r["artist_id"]
        row = {
            "artist_id": aid,
            "display_name": r["artist_name"],
            "display_link": reverse("artist_songs", args=[aid]),
            "region_id": r.get("region_id"),
        }
        for n in TOP_NS:
            if r[f"rank_{n}"] is not None:
                row[f"rank_{n}"] = r[f"rank_{n}"]
                row[f"score_{n}"] = r[f"total_{n}"]
        matrix_rows.append(row)

    # 並び順：20→15→10→5 の順位がある順に昇順
    BIG = 10**9

    def sort_key(row):
        return (
            row.get("rank_5", BIG),
            row.get("rank_10", BIG),
            row.get("rank_15", BIG),
            row.get("rank_20", BIG),
            row.get("display_name", ""),
        )

    matrix_rows.sort(key=sort_key)

    return render(
        request,
        "songs/rank_matrix.html",
        {
            "kind": "artist",
            "kind_label": "歌手",
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "rows": matrix_rows,
        },
    )


# 全曲TOPの1回あたり表示件数。全件を一度に返すとHTMLが十数MBになるため分割する。
SONG_RANKING_PAGE_SIZE = 200


def _resolve_song_ranking_params(request):
    """全曲TOP系ビューの共通パラメータ解決（region_id / selected_user / karaoke_mode）"""
    if "region_id" not in request.GET:
        # region_id パラメータがまったくない場合の処理（例：初期値1を使う）
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    karaoke_mode = request.GET.get("karaoke") == "1"
    return region_id, selected_user, karaoke_mode


def _karaoke_ranking(selected_user, region_id):
    """カラオケ採点ランキング全件（同点は同順位、次は飛ばす）"""
    rating_qs = Rating.objects.filter(
        user=selected_user,
        karaoke_score__isnull=False,
    ).select_related("song", "song__artist")
    if region_id:
        rating_qs = rating_qs.filter(song__artist__region_id=region_id)
    rating_qs = rating_qs.order_by("-karaoke_score", Lower("song__title"))

    ranked = []
    prev_score = object()  # 必ず最初は一致しないようにセンチネル
    current_rank = 0
    next_rank = 1
    for r in rating_qs:
        ks = r.karaoke_score
        if ks != prev_score:
            current_rank = next_rank
        ranked.append(
            {
                "display_rank": current_rank,
                "artist_id": r.song.artist_id,
                "artist_name": r.song.artist.name,
                "song_id": r.song_id,
                "song_title": r.song.title,
                "score": ks,
            }
        )
        prev_score = ks
        next_rank += 1
    return ranked


def _song_ranking_slice(selected_user, region_id, karaoke_mode, offset, limit):
    """
    全曲TOPの一部（offset から limit 件）と総件数を返す。
    戻り値: (songs, total)
    """
    if karaoke_mode:
        # カラオケ採点分は件数が少ないため全件組み立ててから切り出す
        ranked = _karaoke_ranking(selected_user, region_id)
        return ranked[offset : offset + limit], len(ranked)

    # CTE 版（services.call_song_ranking）に置き換え。
    # 旧 rank_view は user_id でフィルタする前に全ユーザ分のウィンドウ計算を
    # 走らせていたため、ここでは user_id を先に絞った CTE を使う。
    songs = call_song_ranking(selected_user.id, region_id, offset=offset, limit=limit)
    total = count_song_ranking(selected_user.id, region_id)
    return songs, total


@login_required
def song_ranking_view(request):
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user, karaoke_mode = _resolve_song_ranking_params(request)

    songs, total = _song_ranking_slice(
        selected_user, region_id, karaoke_mode, 0, SONG_RANKING_PAGE_SIZE
    )
    loaded = len(songs)

    return render(
        request,
        "songs/song_ranking.html",
        {
            "regions": regions,
            "users": users,
            "selected_user": selected_user,
            "songs": songs,  # テンプレートは display_rank / score を表示
            "is_own_page": selected_user == request.user,
            "region_id": region_id,
            "karaoke_mode": karaoke_mode,
            "loaded_count": loaded,
            "total_count": total,
            "remaining_count": max(total - loaded, 0),
            "page_size": SONG_RANKING_PAGE_SIZE,
        },
    )


@login_required
def song_ranking_rows_view(request):
    """「もっと見る」用。続きの行をHTML断片として返す。"""
    region_id, selected_user, karaoke_mode = _resolve_song_ranking_params(request)

    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        offset = 0

    songs, total = _song_ranking_slice(
        selected_user, region_id, karaoke_mode, offset, SONG_RANKING_PAGE_SIZE
    )
    loaded = offset + len(songs)

    html = render_to_string(
        "songs/partials/_song_ranking_rows.html",
        {
            "songs": songs,
            "is_own_page": selected_user == request.user,
            "karaoke_mode": karaoke_mode,
        },
        request=request,
    )

    return JsonResponse(
        {
            "html": html,
            "loaded_count": loaded,
            "remaining_count": max(total - loaded, 0),
        }
    )


# 採点
@login_required
def song_list_view(request):
    query = request.GET.get("q", "")

    # サブクエリでログインユーザーの点数を取得
    user_rating_subquery = Rating.objects.filter(
        user=request.user, song=OuterRef("pk")
    ).values("score")[:1]

    song_qs = Song.objects.select_related("artist").annotate(
        user_score=Subquery(user_rating_subquery, output_field=IntegerField())
    )

    if query:
        song_qs = song_qs.filter(
            Q(title__icontains=query) | Q(artist__name__icontains=query)
        )

    # ✅ ソート：artist.name → user_score（降順）→ title
    song_qs = song_qs.order_by("artist__name", "-user_score", "title")

    paginator = Paginator(song_qs, 100)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # 点数は user_score として annotate 済みなので、
    # テンプレートに渡すためだけのフォーム生成は行わない
    return render(
        request,
        "songs/song_list.html",
        {
            "page_obj": page_obj,
            "query": query,
        },
    )


# 歌手の曲リスト
@login_required
# 歌手の曲リスト
@login_required
def artist_song_list_view(request, artist_id):

    artist = get_object_or_404(Artist, pk=artist_id)

    # ★追加：ユーザー選択（未指定ならログインユーザー）
    users = User.objects.all().order_by("username")
    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )
    is_own_page = selected_user == request.user

    # ★変更：selected_user の点数を取る
    user_score_subquery = Rating.objects.filter(
        user=selected_user,
        song=OuterRef("pk"),
    ).values("score")[:1]

    user_karaoke_subquery = Rating.objects.filter(
        user=selected_user,
        song=OuterRef("pk"),
    ).values("karaoke_score")[:1]

    songs = list(
        artist.songs.annotate(
            user_score=Subquery(user_score_subquery, output_field=IntegerField()),
            user_karaoke_score=Subquery(
                user_karaoke_subquery,
                output_field=DecimalField(max_digits=6, decimal_places=3),
            ),
        )
        .select_related("artist")
        .order_by("is_cover", "-user_score", Lower("title"))
    )

    ranked_songs = []
    prev_score = None
    current_rank = 0
    next_rank = 1

    for song in songs:
        if song.user_score != prev_score:
            current_rank = next_rank

        ranked_songs.append(
            {
                "song": {
                    "song_id": song.id,
                    "song_title": song.title,
                    "artist_id": song.artist_id,
                    "artist_name": song.artist.name,
                    "lyricist": song.lyricist,
                    "composer": song.composer,
                    "year": song.year,
                    "is_cover": song.is_cover,
                },
                "rank": current_rank,
                "user_score": song.user_score,
                "user_karaoke_score": song.user_karaoke_score,
            }
        )

        prev_score = song.user_score
        next_rank += 1

    # ★追加：datalist 用の既存クリエイター候補（このアーティストで入力済みのもの）
    # 取得済みの songs から組み立てる（以前は artist.songs.all() を3回引き直していた）
    lyricist_suggestions = sorted({s.lyricist for s in songs if s.lyricist})
    composer_suggestions = sorted({s.composer for s in songs if s.composer})
    year_suggestions = sorted({s.year for s in songs if s.year})

    return render(
        request,
        "songs/artist_song_list.html",
        {
            "artist": artist,
            "songs": ranked_songs,
            "all_users": users,  # ★追加
            "selected_user": selected_user,  # ★追加
            "is_own_page": is_own_page,  # ★追加
            "lyricist_suggestions": lyricist_suggestions,
            "composer_suggestions": composer_suggestions,
            "year_suggestions": year_suggestions,
        },
    )


# 作詞者・作曲者・年の曲一覧
@login_required
def creator_song_list_view(request):
    creator_type = request.GET.get("type", "")
    creator_name = request.GET.get("name", "").strip()

    if creator_type not in CREATOR_TYPE_LABELS or not creator_name:
        return redirect("lyricist_list")

    creator_label = CREATOR_TYPE_LABELS[creator_type]

    # year は数値カラム。文字列で来るので int に変換してフィルタ
    if creator_type == "year":
        try:
            filter_value = int(creator_name)
        except ValueError:
            return redirect("year_list")
    else:
        filter_value = creator_name

    # ユーザー選択（未指定ならログインユーザー）
    users = User.objects.all().order_by("username")
    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )
    is_own_page = selected_user == request.user

    # selected_user の点数を取る
    user_score_subquery = Rating.objects.filter(
        user=selected_user,
        song=OuterRef("pk"),
    ).values("score")[:1]

    songs = list(
        Song.objects.filter(**{creator_type: filter_value})
        .annotate(
            user_score=Subquery(user_score_subquery, output_field=IntegerField())
        )
        .select_related("artist")
        .order_by("is_cover", "-user_score", Lower("title"))
    )

    ranked_songs = []
    prev_score = object()  # 最初は必ず一致しないようにセンチネル
    current_rank = 0
    next_rank = 1

    for song in songs:
        if song.user_score != prev_score:
            current_rank = next_rank

        ranked_songs.append(
            {
                "song": {
                    "song_id": song.id,
                    "song_title": song.title,
                    "artist_id": song.artist_id,
                    "artist_name": song.artist.name,
                    "lyricist": song.lyricist,
                    "composer": song.composer,
                    "year": song.year,
                    "is_cover": song.is_cover,
                },
                "rank": current_rank,
                "user_score": song.user_score,
            }
        )

        prev_score = song.user_score
        next_rank += 1

    # 表示するメタ列（自分自身は除外）: 作詞別 → 作曲・年, 作曲別 → 作詞・年, 年別 → 作詞・作曲
    extra_columns = [c for c in ("lyricist", "composer", "year") if c != creator_type]

    return render(
        request,
        "songs/creator_song_list.html",
        {
            "creator_type": creator_type,
            "creator_name": creator_name,
            "creator_label": creator_label,
            "songs": ranked_songs,
            "all_users": users,
            "selected_user": selected_user,
            "is_own_page": is_own_page,
            "extra_columns": extra_columns,
        },
    )


@csrf_exempt  # 本番では CSRF トークンの使用を推奨
@require_POST
@login_required
def update_rating_view(request):
    user = request.user
    song_id = request.POST.get("song_id")
    score = request.POST.get("score")

    try:
        song = Song.objects.get(pk=song_id)

        if score is None or score.strip() == "":
            return JsonResponse({"error": "スコアが空です"}, status=400)

        try:
            score = int(score)
        except ValueError:
            return JsonResponse({"error": "スコアは整数で入力してください"}, status=400)

        if not (0 <= score <= 100):
            return JsonResponse(
                {"error": "スコアは0〜100で入力してください"}, status=400
            )

        rating, created = Rating.objects.get_or_create(
            user=user,
            song=song,
            defaults={"score": score},
        )
        if not created:
            rating.score = score
            rating.save()

        return JsonResponse({"success": True, "score": rating.score})

    except Song.DoesNotExist:
        return JsonResponse({"error": "指定された曲が存在しません"}, status=404)


@csrf_exempt  # 本番では CSRF トークンの使用を推奨
@require_POST
@login_required
def update_karaoke_score_view(request):
    user = request.user
    song_id = request.POST.get("song_id")
    karaoke_score_raw = request.POST.get("karaoke_score", "")

    try:
        song = Song.objects.get(pk=song_id)
    except Song.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "指定された曲が存在しません"}, status=404
        )

    if karaoke_score_raw is None or karaoke_score_raw.strip() == "":
        karaoke_score = None
    else:
        try:
            karaoke_score = Decimal(karaoke_score_raw)
        except (InvalidOperation, ValueError):
            return JsonResponse(
                {"success": False, "error": "カラオケ点数は数値で入力してください"},
                status=400,
            )
        if not (Decimal("0") <= karaoke_score <= Decimal("100")):
            return JsonResponse(
                {"success": False, "error": "カラオケ点数は0〜100で入力してください"},
                status=400,
            )

    rating, _ = Rating.objects.get_or_create(
        user=user,
        song=song,
        defaults={"karaoke_score": karaoke_score},
    )
    rating.karaoke_score = karaoke_score
    rating.save(update_fields=["karaoke_score", "updated_at"])

    return JsonResponse(
        {
            "success": True,
            "karaoke_score": str(karaoke_score) if karaoke_score is not None else None,
        }
    )


@require_POST
@login_required
def update_cover_view(request):
    song_id = request.POST.get("song_id")
    is_cover_str = request.POST.get("is_cover")
    is_cover = is_cover_str == "true"

    try:
        song = Song.objects.get(pk=song_id)
        song.is_cover = is_cover
        song.save()
        return JsonResponse({"success": True})
    except Song.DoesNotExist:
        return JsonResponse({"success": False, "error": "Song not found"})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


def _extract_indices(post_dict, prefix):
    """
    POSTのキー名から 'prefix' に続く連番（数字）をすべて抽出してソートして返す。
    例: song_title_3, song_title_12 → [3, 12]
    """
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    idxs = set()
    for k in post_dict.keys():
        m = pat.match(k)
        if m:
            idxs.add(int(m.group(1)))
    return sorted(idxs)


# 曲追加
@login_required
def bulk_add_view(request):
    regions = MusicRegion.objects.all()
    # テンプレートでは id / name / region_id しか使わないため、モデルを組み立てず値だけ取る
    artists = Artist.objects.order_by("name").values("id", "name", "region_id")

    selected_artist_id = request.GET.get("artist_id") or ""
    selected_region_id = ""
    mode = request.GET.get("mode") or request.POST.get("mode") or "single"
    done = request.GET.get("done")

    # 初期表示で出しておく行数（増減可能）
    initial_rows = 5

    # ★ 追加：必ず初期化しておく
    existing_songs = []
    existing_titles_json = "[]"

    if selected_artist_id:
        try:
            sel_artist = Artist.objects.select_related("region").get(
                id=selected_artist_id
            )
            selected_region_id = str(sel_artist.region_id)

            # この歌手の既存曲を取得
            existing_songs = list(
                Song.objects.filter(artist=sel_artist).order_by("title")
            )
            # タイトルだけを JSON にして JS に渡す
            existing_titles_json = json.dumps(
                [s.title for s in existing_songs],
                ensure_ascii=False,
            )
        except Artist.DoesNotExist:
            selected_artist_id = ""
            selected_region_id = ""
            existing_songs = []
            existing_titles_json = "[]"

    # ★ datalist 用：作詞・作曲・年 の既存候補（全曲から重複なしで集める）
    lyricist_suggestions = sorted(
        Song.objects.exclude(lyricist__isnull=True)
        .exclude(lyricist__exact="")
        .values_list("lyricist", flat=True)
        .distinct()
    )
    composer_suggestions = sorted(
        Song.objects.exclude(composer__isnull=True)
        .exclude(composer__exact="")
        .values_list("composer", flat=True)
        .distinct()
    )
    year_suggestions = sorted(
        Song.objects.exclude(year__isnull=True)
        .values_list("year", flat=True)
        .distinct()
    )

    def render_form(error=None):
        ctx = {
            "selected_artist_id": str(selected_artist_id),
            "selected_region_id": str(selected_region_id),
            "artists": artists,
            "regions": regions,
            "initial_rows": initial_rows,
            "range_initial": range(1, initial_rows + 1),
            "mode": mode,
            "done": done,
            "existing_songs": existing_songs,
            "existing_titles_json": existing_titles_json,  # ★追加
            "lyricist_suggestions": lyricist_suggestions,
            "composer_suggestions": composer_suggestions,
            "year_suggestions": year_suggestions,
        }
        if error:
            ctx["error"] = error
        return render(request, "songs/bulk_add.html", ctx)


    def _parse_year(raw):
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    if request.method == "POST":
        user = request.user

        if mode == "single":
            # 単一歌手の決定は従来通り
            region_id = request.POST.get("region_id")
            artist_id = request.POST.get("artist_id")
            new_artist_name = request.POST.get("new_artist_name", "").strip()

            if artist_id:
                try:
                    artist = Artist.objects.get(id=artist_id)
                except Artist.DoesNotExist:
                    return render_form("選択された歌手が存在しません。")
            elif new_artist_name:
                if not region_id:
                    return render_form(
                        "新しい歌手名を登録する場合、地域を選択してください。"
                    )
                try:
                    region = MusicRegion.objects.get(id=region_id)
                except MusicRegion.DoesNotExist:
                    return render_form("選択された地域が存在しません。")
                artist, _ = Artist.objects.get_or_create(
                    name=new_artist_name, defaults={"region": region}
                )
            else:
                return render_form("歌手を選択するか新規入力してください。")

            # ★ 可変行対応：POSTキーから実在する行番号だけを抽出して回す
            indices = _extract_indices(request.POST, "song_title_")
            for i in indices:
                title = request.POST.get(f"song_title_{i}", "").strip()
                score = request.POST.get(f"song_score_{i}", "").strip()
                is_cover = request.POST.get(f"song_is_cover_{i}") == "on"
                lyricist = request.POST.get(f"song_lyricist_{i}", "").strip()
                composer = request.POST.get(f"song_composer_{i}", "").strip()
                year_val = _parse_year(request.POST.get(f"song_year_{i}"))
                if not title:
                    continue

                song = Song.objects.filter(title=title, artist=artist).first()
                if not song:
                    try:
                        song = Song.objects.create(
                            title=title,
                            artist=artist,
                            is_cover=is_cover,
                            lyricist=lyricist or None,
                            composer=composer or None,
                            year=year_val,
                        )
                    except IntegrityError:
                        song = Song.objects.get(title=title, artist=artist)
                        song.is_cover = is_cover
                        if lyricist:
                            song.lyricist = lyricist
                        if composer:
                            song.composer = composer
                        if year_val is not None:
                            song.year = year_val
                        song.save()
                else:
                    song.is_cover = is_cover
                    if lyricist:
                        song.lyricist = lyricist
                    if composer:
                        song.composer = composer
                    if year_val is not None:
                        song.year = year_val
                    song.save()

                if score:
                    try:
                        s = int(score)
                        if 0 <= s <= 100:
                            Rating.objects.update_or_create(
                                user=user, song=song, defaults={"score": s}
                            )
                    except ValueError:
                        pass

            return redirect("artist_songs", artist_id=artist.id)

        else:
            # 複数歌手モード
            touched = set()
            indices = _extract_indices(request.POST, "song_title_")
            for i in indices:
                title = request.POST.get(f"song_title_{i}", "").strip()
                if not title:
                    continue

                score_str = request.POST.get(f"song_score_{i}", "").strip()
                is_cover = request.POST.get(f"song_is_cover_{i}") == "on"
                lyricist = request.POST.get(f"song_lyricist_{i}", "").strip()
                composer = request.POST.get(f"song_composer_{i}", "").strip()
                year_val = _parse_year(request.POST.get(f"song_year_{i}"))
                artist_id_i = request.POST.get(f"artist_id_{i}")
                new_artist_name_i = request.POST.get(f"new_artist_name_{i}", "").strip()
                region_id_i = request.POST.get(f"region_id_{i}")

                # 行ごとに歌手決定
                artist = None
                if artist_id_i:
                    try:
                        artist = Artist.objects.get(id=artist_id_i)
                    except Artist.DoesNotExist:
                        continue
                elif new_artist_name_i:
                    if not region_id_i:
                        continue
                    try:
                        region = MusicRegion.objects.get(id=region_id_i)
                    except MusicRegion.DoesNotExist:
                        continue
                    artist, _ = Artist.objects.get_or_create(
                        name=new_artist_name_i, defaults={"region": region}
                    )
                else:
                    continue

                # 曲登録/更新
                song = Song.objects.filter(title=title, artist=artist).first()
                if not song:
                    try:
                        song = Song.objects.create(
                            title=title,
                            artist=artist,
                            is_cover=is_cover,
                            lyricist=lyricist or None,
                            composer=composer or None,
                            year=year_val,
                        )
                    except IntegrityError:
                        song = Song.objects.get(title=title, artist=artist)
                        song.is_cover = is_cover
                        if lyricist:
                            song.lyricist = lyricist
                        if composer:
                            song.composer = composer
                        if year_val is not None:
                            song.year = year_val
                        song.save()
                else:
                    song.is_cover = is_cover
                    if lyricist:
                        song.lyricist = lyricist
                    if composer:
                        song.composer = composer
                    if year_val is not None:
                        song.year = year_val
                    song.save()

                if score_str:
                    try:
                        s = int(score_str)
                        if 0 <= s <= 100:
                            Rating.objects.update_or_create(
                                user=user, song=song, defaults={"score": s}
                            )
                    except ValueError:
                        pass

                touched.add(artist.id)

            if not touched:
                return render_form("有効な入力行がありません。")
            if len(touched) == 1:
                return redirect("artist_songs", artist_id=touched.pop())
            return redirect(f"{request.path}?mode=multi&done=1")

    return render_form()


def signup_view(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("login")  # ユーザー作成後ログイン画面へ
    else:
        form = UserCreationForm()
    return render(request, "registration/signup.html", {"form": form})


@staff_member_required
def missing_audio_files_view(request):
    tag_set = set()

    for root, dirs, files in os.walk(MUSIC_DIR):
        for file in files:
            if file.lower().endswith(".mp3"):
                try:
                    audio = EasyID3(os.path.join(root, file))
                    artist = audio.get("artist", [""])[0].strip()
                    title = audio.get("title", [""])[0].strip()
                    if artist and title:
                        tag_set.add((artist, title))
                except Exception:
                    continue

    songs = Song.objects.select_related("artist").all()
    missing_songs = []

    for song in songs:
        key = (song.artist.name.strip(), song.title.strip())
        if key not in tag_set:
            missing_songs.append(song)

    # region.id → artist.name → song.title でソート
    missing_songs = sorted(
        missing_songs,
        key=lambda s: (
            s.artist.region.id if s.artist.region else 0,
            s.artist.name,
            s.title,
        ),
    )

    # user=2の全Ratingを取得して辞書にする（キーはsong.id）
    user_id = 2
    ratings = Rating.objects.filter(user_id=user_id, song__in=missing_songs)
    rating_dict = {r.song_id: r.score for r in ratings}

    return render(
        request,
        "songs/missing_files.html",
        {
            "missing_songs": missing_songs,
            "rating_dict": rating_dict,
            "user_id": user_id,
        },
    )


def kana_to_hiragana(s: str) -> str:
    if not s:
        return s
    result = []
    for ch in s:
        code = ord(ch)
        # カタカナ → ひらがな
        if 0x30A1 <= code <= 0x30F6:  # ァ〜ヶ
            result.append(chr(code - 0x60))
        else:
            result.append(ch)
    return "".join(result)


# 歌手検索の1回あたり表示件数。
# 全件（邦楽で約1350件）を一度に返すとHTMLが400KB超になり、
# ブラウザ側のパースとDOM構築が重くなるため分割する。
ARTIST_SEARCH_PAGE_SIZE = 200


def _artist_search_results(request):
    """検索条件を解決し、絞り込み済みの歌手リストを返す。戻り値: (region_id, prefix, filter_top, artists)"""
    # region_id
    if "region_id" not in request.GET:
        region_id = "1"
    else:
        region_id = request.GET.get("region_id") or None

    prefix = request.GET.get("prefix", "")
    # 入力された接頭辞を ひらがな + 小文字 に正規化
    prefix_norm = kana_to_hiragana(prefix.lower()) if prefix else ""

    user = request.user
    filter_top = request.GET.get("top")

    # 曲数と採点数を1本のクエリでまとめて annotate すると
    # (歌手 × 曲 × 評価) の掛け算になった行を DISTINCT で数え直すことになり重い。
    # 曲側・評価側をそれぞれ1回の集計クエリで取り、Python側で突き合わせる。
    song_counts = dict(
        Song.objects.filter(is_cover=False)
        .values_list("artist_id")
        .annotate(c=Count("id"))
        .values_list("artist_id", "c")
    )
    rating_counts = dict(
        Rating.objects.filter(user=user, song__is_cover=False)
        .values_list("song__artist_id")
        .annotate(c=Count("id"))
        .values_list("song__artist_id", "c")
    )

    artist_qs = Artist.objects.all()
    if region_id:
        artist_qs = artist_qs.filter(region_id=region_id)

    # テンプレートで使うのは id / name / 2つの件数だけ
    artists = []
    for a in artist_qs.values("id", "name"):
        a["song_count"] = song_counts.get(a["id"], 0)
        a["rating_count"] = rating_counts.get(a["id"], 0)
        # アーティスト名を ひらがな + 小文字 に正規化して持たせる
        a["name_norm"] = kana_to_hiragana(a["name"].lower())
        artists.append(a)

    # プルダウンの条件（集計をPython側に移したので絞り込みもこちらで行う）
    if filter_top in ["5", "10", "15", "20"]:
        n = int(filter_top)
        artists = [a for a in artists if n - 5 <= a["song_count"] < n]
    elif filter_top == "0":
        artists = [a for a in artists if a["song_count"] > a["rating_count"]]
    elif filter_top == "20~":
        artists = [
            a
            for a in artists
            if a["song_count"] > a["rating_count"] and a["rating_count"] >= 20
        ]

    # prefix フィルタ（ひらがな/カタカナ無視）
    if prefix_norm:
        artists = [a for a in artists if a["name_norm"].startswith(prefix_norm)]

    # ソートも正規化した名前で
    artists.sort(key=lambda x: x["name_norm"])

    return region_id, prefix, filter_top, artists


# 歌手検索
@login_required
def artist_search_view(request):
    region_id, prefix, filter_top, artists = _artist_search_results(request)

    shown = artists[:ARTIST_SEARCH_PAGE_SIZE]

    return render(
        request,
        "songs/artist_search.html",
        {
            "artists": shown,
            "regions": MusicRegion.objects.all(),
            "region_id": region_id,
            "prefix": prefix,
            "top": filter_top,
            "loaded_count": len(shown),
            "remaining_count": len(artists) - len(shown),
        },
    )


@login_required
def artist_search_rows_view(request):
    """「もっと見る」用。続きの行をHTML断片として返す。"""
    _, _, _, artists = _artist_search_results(request)

    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        offset = 0

    shown = artists[offset : offset + ARTIST_SEARCH_PAGE_SIZE]
    loaded = offset + len(shown)

    html = render_to_string(
        "songs/partials/_artist_search_rows.html",
        {"artists": shown},
        request=request,
    )

    return JsonResponse(
        {
            "html": html,
            "loaded_count": loaded,
            "remaining_count": max(len(artists) - loaded, 0),
        }
    )


@login_required
def artist_year_heatmap_view(request):
    users = User.objects.all().order_by("username")

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    this_year = timezone.localdate().year

    # このユーザーの最小year（何もなければ fallback）
    agg = ArtistYearPreference.objects.filter(user=selected_user).aggregate(
        min_year=Min("year")
    )
    user_min_year = agg["min_year"] or 2000

    # GET指定があれば優先、無ければ「最小year～今年」
    def _get_int(name, default):
        v = request.GET.get(name)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except ValueError:
            return default

    year_from = _get_int("from", user_min_year)
    year_to = _get_int("to", this_year)

    if year_from > year_to:
        year_from, year_to = year_to, year_from

    years = list(range(year_from, year_to + 1))

    birth_year = (
        UserProfile.objects.filter(user=selected_user)
        .values_list("birth_year", flat=True)
        .first()
    )

    age_row = None
    if birth_year:
        age_row = [y - birth_year for y in years]

    # 表示対象アーティスト：このユーザーに1件でもprefがあるartistだけ
    # 並び順：そのartistの最小year昇順 → name
    # そのアーティストの最小year（=最初の行）
    first_year_sq = (
        ArtistYearPreference.objects.filter(user=selected_user, artist=OuterRef("pk"))
        .order_by("year")
        .values("year")[:1]
    )

    # その最小yearのscore（=最初の行のscore）
    first_year_score_sq = (
        ArtistYearPreference.objects.filter(user=selected_user, artist=OuterRef("pk"))
        .order_by("year")
        .values("score")[:1]
    )

    artists = (
        Artist.objects.filter(year_prefs__user=selected_user)
        .annotate(
            first_year=Subquery(first_year_sq, output_field=IntegerField()),
            first_year_score=Coalesce(
                Subquery(first_year_score_sq, output_field=IntegerField()), 0
            ),
        )
        .order_by("first_year", "-first_year_score", "name")
        .distinct()
    )

    artists = list(artists)

    # 追加候補（プルダウン用）：全アーティスト（全地域込み）
    all_artists_for_add = list(Artist.objects.all().order_by("name"))

    prefs = ArtistYearPreference.objects.filter(
        user=selected_user,
        year__gte=year_from,
        year__lte=year_to,
        artist__in=artists,
    ).values("artist_id", "year", "score")

    pref_map = {f'{p["artist_id"]}:{p["year"]}': p["score"] for p in prefs}

    return render(
        request,
        "songs/artist_year_heatmap.html",
        {
            "all_users": users,
            "selected_user": selected_user,
            "is_own_page": (selected_user == request.user),
            "artists": artists,
            "all_artists_for_add": all_artists_for_add,
            "years": years,
            "year_from": year_from,
            "year_to": year_to,
            "pref_map_json": json.dumps(pref_map, ensure_ascii=False),
            "birth_year": birth_year,
            "age_row": age_row,
        },
    )


@require_POST
@login_required
@transaction.atomic
def artist_year_heatmap_bulk_save(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "JSONが不正です"}, status=400)

    items = payload.get("items") or []
    if not isinstance(items, list):
        return JsonResponse({"error": "itemsが不正です"}, status=400)

    user_id = payload.get("user_id")
    if user_id:
        if (not request.user.is_staff) and (int(user_id) != request.user.id):
            return JsonResponse({"error": "権限がありません"}, status=403)
        target_user = get_object_or_404(User, id=user_id)
    else:
        target_user = request.user

    cleaned = []
    for it in items:
        try:
            aid = int(it.get("artist_id"))
            year = int(it.get("year"))
            score = int(it.get("score"))
        except Exception:
            continue
        if score < 0:
            score = 0
        if score > 4:
            score = 4
        cleaned.append((aid, year, score))

    if not cleaned:
        return JsonResponse({"success": True, "deleted": 0, "created": 0, "updated": 0})

    artist_ids = list({a for a, _, _ in cleaned})
    years = list({y for _, y, _ in cleaned})

    existing = ArtistYearPreference.objects.filter(
        user=target_user,
        artist_id__in=artist_ids,
        year__in=years,
    )
    existing_map = {(p.artist_id, p.year): p for p in existing}

    to_update = []
    to_create = []
    to_delete_ids = []

    for aid, year, score in cleaned:
        key = (aid, year)
        obj = existing_map.get(key)

        if score == 0:
            # 1以上→0 は削除 / 未登録→0 は何もしない
            if obj:
                to_delete_ids.append(obj.id)
            continue

        # score 1〜4 は upsert
        if obj:
            if obj.score != score:
                obj.score = score
                to_update.append(obj)
        else:
            to_create.append(
                ArtistYearPreference(
                    user=target_user, artist_id=aid, year=year, score=score
                )
            )

    deleted = 0
    if to_delete_ids:
        deleted = ArtistYearPreference.objects.filter(id__in=to_delete_ids).delete()[0]

    created = 0
    if to_create:
        ArtistYearPreference.objects.bulk_create(to_create, ignore_conflicts=True)
        created = len(to_create)

    updated = 0
    if to_update:
        ArtistYearPreference.objects.bulk_update(to_update, ["score"])
        updated = len(to_update)

    return JsonResponse(
        {"success": True, "deleted": deleted, "created": created, "updated": updated}
    )


@require_POST
@login_required
@transaction.atomic
def artist_year_heatmap_range_set(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "JSONが不正です"}, status=400)

    try:
        artist_id = int(payload.get("artist_id"))
        y_from = int(payload.get("from"))
        y_to = int(payload.get("to"))
        score = int(payload.get("score"))
    except Exception:
        return JsonResponse({"error": "パラメータが不正です"}, status=400)

    if y_from > y_to:
        y_from, y_to = y_to, y_from
    if score < 0:
        score = 0
    if score > 4:
        score = 4

    user_id = payload.get("user_id")
    if user_id:
        if (not request.user.is_staff) and (int(user_id) != request.user.id):
            return JsonResponse({"error": "権限がありません"}, status=403)
        target_user = get_object_or_404(User, id=user_id)
    else:
        target_user = request.user

    years = list(range(y_from, y_to + 1))

    if score == 0:
        # 範囲を削除（作成しない）
        deleted = ArtistYearPreference.objects.filter(
            user=target_user, artist_id=artist_id, year__in=years
        ).delete()[0]
        return JsonResponse(
            {"success": True, "count": len(years), "deleted": deleted, "mode": "delete"}
        )

    # score 1〜4 は upsert
    existing = ArtistYearPreference.objects.filter(
        user=target_user, artist_id=artist_id, year__in=years
    )
    existing_by_year = {p.year: p for p in existing}

    to_update = []
    to_create = []

    for y in years:
        obj = existing_by_year.get(y)
        if obj:
            if obj.score != score:
                obj.score = score
                to_update.append(obj)
        else:
            to_create.append(
                ArtistYearPreference(
                    user=target_user, artist_id=artist_id, year=y, score=score
                )
            )

    if to_create:
        ArtistYearPreference.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        ArtistYearPreference.objects.bulk_update(to_update, ["score"])

    return JsonResponse({"success": True, "count": len(years), "mode": "upsert"})


@require_POST
@login_required
@transaction.atomic
def artist_year_heatmap_add_artist(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        artist_id = int(payload.get("artist_id"))
    except Exception:
        return JsonResponse({"error": "パラメータが不正です"}, status=400)

    user_id = payload.get("user_id")
    if user_id:
        if (not request.user.is_staff) and (int(user_id) != request.user.id):
            return JsonResponse({"error": "権限がありません"}, status=403)
        target_user = get_object_or_404(User, id=user_id)
    else:
        target_user = request.user

    year = timezone.localdate().year

    obj, created = ArtistYearPreference.objects.get_or_create(
        user=target_user,
        artist_id=artist_id,
        year=year,
        defaults={"score": 1},  # ★ ここを1に
    )
    if not created and obj.score == 0:
        obj.score = 1
        obj.save(update_fields=["score"])

    return JsonResponse({"success": True, "year": year, "created": created})
