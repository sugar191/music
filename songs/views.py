import re
import csv
import io
import json
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
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
from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from urllib.parse import urlencode
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import (
    Artist,
    ArtistCredit,
    Song,
    Rating,
    MusicRegion,
    ArtistYearPreference,
    UserProfile,
    parse_top_ns,
    format_top_ns,
    top_ns_for,
)
from .utils import normalize
from .services import (
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

def _matrix_rank_cells(row, top_ns):
    """
    ランク表（rank_matrix.html）の1行ぶんのセルを top_ns の順に並べて返す。
    テンプレート側で rank_5 / rank_10 … と固定で書かずに済むようにする。
    """
    return [
        {"n": n, "rank": row.get(f"rank_{n}"), "score": row.get(f"score_{n}")}
        for n in top_ns
    ]

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

# region_id パラメータ未指定時の既定地域（1 = 邦楽）。
DEFAULT_REGION_ID = "1"


def _resolve_region_and_user(request):
    """
    全画面共通のパラメータ解決。
      region_id: 未指定なら DEFAULT_REGION_ID、空文字なら None（＝全地域）
      selected_user: user パラメータ指定時はそのユーザー、無ければログインユーザー
    戻り値: (region_id, selected_user)
    """
    if "region_id" not in request.GET:
        region_id = DEFAULT_REGION_ID
    else:
        # 空文字は「地域で絞らない」の意味なので None に落とす
        region_id = request.GET.get("region_id") or None

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    return region_id, selected_user


def _resolve_karaoke_mode(request):
    """
    全ランキング画面共通の「🎤（カラオケ採点で集計する）」切替の解決。
    karaoke=1 のときだけ True。
    """
    return request.GET.get("karaoke") == "1"


def _resolve_ranking_params(request):
    """
    ランキング系4画面の共通パラメータ解決
    （region_id / selected_user / top_n / karaoke_mode / ranking_options）

    ranking_options は「ログイン中のユーザー自身の表示設定」。
    他人のランキングを見ているときも、列の構成は見ている側の設定に従う。
    """
    region_id, selected_user = _resolve_region_and_user(request)

    options = list(top_ns_for(request.user))

    try:
        top_n = int(request.GET.get("top_n", options[0]))
    except (TypeError, ValueError):
        top_n = options[0]
    if top_n not in options:
        # 設定から消された値がURLに残っていても落ちないようにする
        top_n = options[0]

    return region_id, selected_user, top_n, _resolve_karaoke_mode(request), options


def _ranking_dataset(kind, user_id, top_n, region_id, karaoke_mode=False):
    """
    ランキング系4画面のデータを共通形式で組み立てる。
    kind: 'artist' | 'lyricist' | 'composer' | 'year'
    karaoke_mode: True なら点数ではなくカラオケ採点で集計する
    戻り値: (rankings, insufficient_songs)
      rankings: [{parent_rank, parent_name, parent_link, total_score, songs:[...]}, ...]
    """
    if kind == "artist":
        top_n_data = call_artist_song_top_n(
            user_id, top_n, region_id, karaoke_mode=karaoke_mode
        )
        insufficient_data = call_artist_insufficient_songs(
            user_id, top_n, region_id, karaoke_mode=karaoke_mode
        )

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
        top_n_data = call_creator_song_top_n(
            user_id, top_n, region_id, kind, karaoke_mode=karaoke_mode
        )
        insufficient_data = call_creator_insufficient_songs(
            user_id, top_n, region_id, kind, karaoke_mode=karaoke_mode
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
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user, top_n, karaoke_mode, ranking_options = (
        _resolve_ranking_params(request)
    )

    rankings, insufficient_data = _ranking_dataset(
        "artist", selected_user.id, top_n, region_id, karaoke_mode
    )

    context = {
        "ranking_options": ranking_options,
        "regions": regions,
        "all_users": users,
        "top_n": top_n,
        "region_id": region_id,
        "selected_user": selected_user,
        "karaoke_mode": karaoke_mode,
        "is_own_page": selected_user == request.user,
    }
    context.update(_ranking_page_context("artist", rankings, insufficient_data))

    return render(request, "songs/artist_ranking.html", context)


# 歌手ランキング
@login_required
def artist_list_view(request):
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user = _resolve_region_and_user(request)
    karaoke_mode = _resolve_karaoke_mode(request)
    top_ns = top_ns_for(request.user)

    # 設定ぶんの top_n を1クエリでまとめて取得し、共通形式
    # (display_name/display_link/display_rank/total_score) に正規化する
    rows = call_artist_top_n_multi(
        selected_user.id, region_id, top_ns=top_ns, karaoke_mode=karaoke_mode
    )

    top_lists = []
    for n in top_ns:
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
            "karaoke_mode": karaoke_mode,
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

    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user, top_n, karaoke_mode, ranking_options = (
        _resolve_ranking_params(request)
    )

    rankings, insufficient_data = _ranking_dataset(
        creator_type, selected_user.id, top_n, region_id, karaoke_mode
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
        "karaoke_mode": karaoke_mode,
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

    region_id, selected_user, top_n, karaoke_mode, ranking_options = (
        _resolve_ranking_params(request)
    )

    def _offset(name):
        try:
            return max(int(request.GET.get(name, 0)), 0)
        except ValueError:
            return 0

    parent_offset = _offset("parent_offset")
    others_offset = _offset("others_offset")

    rankings, insufficient_songs = _ranking_dataset(
        kind, selected_user.id, top_n, region_id, karaoke_mode
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
        card_context = {
            "rankings": parents,
            "is_own_page": is_own_page,
            "karaoke_mode": karaoke_mode,
        }
        card_context.update(RANKING_KIND_FLAGS[kind])
        parents_html = render_to_string(
            "songs/partials/_ranking_cards.html", card_context, request=request
        )

    others_html = ""
    if others:
        others_html = render_to_string(
            "songs/partials/_others_rows.html",
            {
                "insufficient_songs": others,
                "is_own_page": is_own_page,
                "karaoke_mode": karaoke_mode,
            },
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

    region_id, selected_user = _resolve_region_and_user(request)
    karaoke_mode = _resolve_karaoke_mode(request)
    top_ns = top_ns_for(request.user)

    # 設定ぶんの top_n を1クエリでまとめて取得し、共通形式
    # (display_name/display_link/display_rank/total_score) に正規化する
    creator_songs_url = reverse("creator_songs")
    rows = call_creator_top_n_multi(
        selected_user.id, region_id, creator_type, top_ns=top_ns,
        karaoke_mode=karaoke_mode
    )

    top_lists = []
    for n in top_ns:
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
            "karaoke_mode": karaoke_mode,
            "kind": creator_type,
            "kind_label": CREATOR_TYPE_LABELS[creator_type],
            "top_lists": top_lists,
        },
    )


# ランク表（rank_matrix.html）の並び順に使う番兵。
# 「その top_n では圏外」の行を必ず後ろへ送るための十分に大きい値。
_RANK_OUT_OF_RANGE = 10**9


def _matrix_sort_key(row, top_ns):
    """
    ランク表の行の並び順キー。
    top_ns の先頭（最小N）の順位を最優先し、同順（または圏外）なら
    次のNへ順に見ていき、最後は表示名で安定させる。
    """
    return tuple(
        row.get(f"rank_{n}", _RANK_OUT_OF_RANGE) for n in top_ns
    ) + (row.get("display_name", ""),)


# 作詞ランク / 作曲ランク / 年ランク（artist_rank_matrix と共通の rank_matrix.html を使用）
@login_required
def creator_matrix_view(request, creator_type):
    if creator_type not in CREATOR_TYPE_LABELS:
        return redirect("artist_rank_matrix")

    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user = _resolve_region_and_user(request)
    karaoke_mode = _resolve_karaoke_mode(request)
    top_ns = top_ns_for(request.user)

    # 設定ぶんの top_n を1クエリでまとめて取得（従来は top_n ごとに4回実行していた）
    creator_songs_url = reverse("creator_songs")
    rows_by_creator = {}
    for r in call_creator_top_n_multi(
        selected_user.id, region_id, creator_type, top_ns=top_ns,
        karaoke_mode=karaoke_mode
    ):
        if all(r[f"rank_{n}"] is None for n in top_ns):
            # どの top_n の条件も満たさないクリエイターは従来どおり表示しない
            continue
        creator = r["creator"]
        qs = urlencode({"type": creator_type, "name": creator})
        row = {
            "display_name": creator,
            "display_link": f"{creator_songs_url}?{qs}",
        }
        for n in top_ns:
            if r[f"rank_{n}"] is not None:
                row[f"rank_{n}"] = r[f"rank_{n}"]
                row[f"score_{n}"] = r[f"total_{n}"]
        row["cells"] = _matrix_rank_cells(row, top_ns)
        rows_by_creator[creator] = row

    matrix_rows = sorted(
        rows_by_creator.values(), key=lambda row: _matrix_sort_key(row, top_ns)
    )

    return render(
        request,
        "songs/rank_matrix.html",
        {
            "kind": creator_type,
            "kind_label": CREATOR_TYPE_LABELS[creator_type],
            "top_ns": top_ns,
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "karaoke_mode": karaoke_mode,
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


# 曲の名義（Song.credit）を切り替えるAJAXエンドポイント。
#
# 注意: すぐ上の update_song_credits_view とは別物。名前が紛らわしいので要注意。
#   update_song_credits_view … 作詞・作曲・年（誰が作ったか）
#   この関数                 … Song.credit（どの名義でリリースされたか）
@require_POST
@login_required
def update_song_credit_view(request):
    song_id = request.POST.get("song_id")
    credit_id = request.POST.get("credit_id")

    try:
        song = Song.objects.only("id", "artist_id", "credit_id").get(pk=song_id)
    except (Song.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "曲が存在しません"}, status=404)

    try:
        credit = ArtistCredit.objects.get(pk=credit_id)
    except (ArtistCredit.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "名義が存在しません"}, status=404)

    # 別の歌手の名義を付けられないようにする。
    # Song.clean() の同じチェックは ModelForm 経由でしか走らないので、
    # AJAX から直接叩かれるここでも見る必要がある。
    if credit.artist_id != song.artist_id:
        return JsonResponse(
            {"success": False, "error": "別の歌手の名義は指定できません"}, status=400
        )

    song.credit = credit
    song.save(update_fields=["credit"])
    return JsonResponse(
        {
            "success": True,
            "credit_id": credit.id,
            "credit_name": credit.name,
            "is_primary": credit.is_primary,
        }
    )


# 名義を1件追加するAJAXエンドポイント（曲一覧の「名義追加」ポップアップ用）。
# 追加後のドロップダウンを作り直せるよう、その歌手の名義一覧を返す。
@require_POST
@login_required
def add_artist_credit_view(request):
    artist_id = request.POST.get("artist_id")
    name = (request.POST.get("name") or "").strip()

    if not name:
        return JsonResponse(
            {"success": False, "error": "名義を入力してください"}, status=400
        )

    try:
        artist = Artist.objects.get(pk=artist_id)
    except (Artist.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "歌手が存在しません"}, status=404)

    # resolve_credit は表記ゆれ（正規化名一致）でも既存を返すので、
    # 「作られたかどうか」は件数の増減で見る。
    before = artist.credits.count()
    credit = artist.resolve_credit(name)
    created = artist.credits.count() > before

    # 同じ表記が別表記（ArtistAlias）にもあると二重管理になる。
    # どちらを消すかは判断できないので、画面に出して気づけるようにするだけ。
    warning = ""
    if artist.aliases.filter(name=name).exists():
        warning = f"「{name}」は別表記にも登録されています。どちらかに寄せてください。"

    return JsonResponse(
        {
            "success": True,
            "created": created,
            "warning": warning,
            "credit_id": credit.id,
            "credits": [
                {"id": c.id, "name": c.name, "is_primary": c.is_primary}
                for c in artist.credits.all()
            ],
        }
    )


@login_required
def artist_rank_matrix_view(request):
    regions = MusicRegion.objects.all()
    users = User.objects.all().order_by("username")

    region_id, selected_user = _resolve_region_and_user(request)
    karaoke_mode = _resolve_karaoke_mode(request)
    top_ns = top_ns_for(request.user)

    # 設定ぶんの top_n を1クエリでまとめて取得（従来は top_n ごとに4回実行していた）
    matrix_rows = []
    for r in call_artist_top_n_multi(
        selected_user.id, region_id, top_ns=top_ns, karaoke_mode=karaoke_mode
    ):
        if all(r[f"rank_{n}"] is None for n in top_ns):
            # どの top_n の条件も満たさない歌手は従来どおり表示しない
            continue
        aid = r["artist_id"]
        row = {
            "artist_id": aid,
            "display_name": r["artist_name"],
            "display_link": reverse("artist_songs", args=[aid]),
            "region_id": r.get("region_id"),
        }
        for n in top_ns:
            if r[f"rank_{n}"] is not None:
                row[f"rank_{n}"] = r[f"rank_{n}"]
                row[f"score_{n}"] = r[f"total_{n}"]
        row["cells"] = _matrix_rank_cells(row, top_ns)
        matrix_rows.append(row)

    # 並び順：top_ns の先頭（小さいN）の順位を優先して昇順
    matrix_rows.sort(key=lambda row: _matrix_sort_key(row, top_ns))

    return render(
        request,
        "songs/rank_matrix.html",
        {
            "kind": "artist",
            "kind_label": "歌手",
            "top_ns": top_ns,
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "karaoke_mode": karaoke_mode,
            "rows": matrix_rows,
        },
    )


# 全曲TOPの1回あたり表示件数。全件を一度に返すとHTMLが十数MBになるため分割する。
SONG_RANKING_PAGE_SIZE = 200


def _resolve_song_ranking_params(request):
    """全曲TOP系ビューの共通パラメータ解決（region_id / selected_user / karaoke_mode）"""
    region_id, selected_user = _resolve_region_and_user(request)

    return region_id, selected_user, _resolve_karaoke_mode(request)


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

    song_qs = Song.objects.select_related("artist", "credit").annotate(
        user_score=Subquery(user_rating_subquery, output_field=IntegerField())
    )

    if query:
        # 名義（例: 桑田佳祐）でも別表記（例: ザ・ハイロウズ）でも引けるようにする。
        # 1歌手が別表記を複数持てるので、別表記の JOIN で同じ曲が複数行になる。
        # distinct() が必要（名義だけなら曲は1件しか指さないので不要だった）。
        song_qs = song_qs.filter(
            Q(title__icontains=query)
            | Q(artist__name__icontains=query)
            | Q(credit__name__icontains=query)
            | Q(artist__aliases__name__icontains=query)
        ).distinct()

    # ソート：歌手名 → 自分の点数（降順）→ 曲名
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
def artist_song_list_view(request, artist_id):
    artist = get_object_or_404(Artist, pk=artist_id)

    # ユーザー選択（未指定ならログインユーザー）
    users = User.objects.all().order_by("username")
    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )
    is_own_page = selected_user == request.user

    # 表示対象は selected_user の点数（他人のページも見られる）
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
        .select_related("artist", "credit")
        .order_by("is_cover", "-user_score", Lower("title"))
    )

    # 同点は同順位、次は飛ばす（1,1,3…）。
    # prev_score は「未評価(None)の曲が先頭に来ても正しく1位から始まる」よう
    # スコアとして絶対に一致しないセンチネルで初期化する。
    ranked_songs = []
    prev_score = object()
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
                    "credit_id": song.credit_id,
                    "credit_name": song.credit.name,
                },
                "rank": current_rank,
                "user_score": song.user_score,
                "user_karaoke_score": song.user_karaoke_score,
            }
        )

        prev_score = song.user_score
        next_rank += 1

    # datalist 用の既存クリエイター候補（このアーティストで入力済みのもの）
    # 取得済みの songs から組み立てる（以前は artist.songs.all() を3回引き直していた）
    lyricist_suggestions = sorted({s.lyricist for s in songs if s.lyricist})
    composer_suggestions = sorted({s.composer for s in songs if s.composer})
    year_suggestions = sorted({s.year for s in songs if s.year})

    # 名義ドロップダウンの選択肢。Meta.ordering により主名義が先頭に来る。
    credits = list(artist.credits.all())

    return render(
        request,
        "songs/artist_song_list.html",
        {
            "artist": artist,
            "songs": ranked_songs,
            "all_users": users,
            "selected_user": selected_user,
            "is_own_page": is_own_page,
            "lyricist_suggestions": lyricist_suggestions,
            "composer_suggestions": composer_suggestions,
            "year_suggestions": year_suggestions,
            "credits": credits,
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
        .select_related("artist", "credit")
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
                    "credit_id": song.credit_id,
                    "credit_name": song.credit.name,
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


# 好み度（0〜100）の更新。呼び出し元テンプレートは X-CSRFToken を付けて fetch するため
# csrf_exempt は不要（付けると CSRF 保護が外れるので付けないこと）。
@require_POST
@login_required
def update_rating_view(request):
    user = request.user
    song_id = request.POST.get("song_id")
    score = request.POST.get("score")

    try:
        song = Song.objects.get(pk=song_id)
    except Song.DoesNotExist:
        return JsonResponse({"error": "指定された曲が存在しません"}, status=404)

    if score is None or score.strip() == "":
        return JsonResponse({"error": "スコアが空です"}, status=400)

    try:
        score = int(score)
    except ValueError:
        return JsonResponse({"error": "スコアは整数で入力してください"}, status=400)

    if not (0 <= score <= 100):
        return JsonResponse({"error": "スコアは0〜100で入力してください"}, status=400)

    rating, created = Rating.objects.get_or_create(
        user=user,
        song=song,
        defaults={"score": score},
    )
    if not created:
        rating.score = score
        # karaoke_score を巻き込まないよう更新列を限定する
        rating.save(update_fields=["score", "updated_at"])

    return JsonResponse({"success": True, "score": rating.score})


# カラオケ採点（0〜100、小数3桁）の更新。CSRF については update_rating_view と同じ。
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
    """カバー曲フラグの切り替え。is_cover は "true" のときだけ True。"""
    song_id = request.POST.get("song_id")
    is_cover = request.POST.get("is_cover") == "true"

    try:
        song = Song.objects.get(pk=song_id)
    except Song.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "指定された曲が存在しません"}, status=404
        )

    song.is_cover = is_cover
    song.save(update_fields=["is_cover"])
    return JsonResponse({"success": True, "is_cover": is_cover})


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


def _parse_year(raw):
    """フォームの年入力を int か None に変換する（空欄・数値以外は None）。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_or_create_artist(name, region):
    """
    歌手を name+region で取得 or 作成する（曲追加画面の単一・複数モード共通）。

    Artist.format_name（正規化名）と主名義の ArtistCredit を必ず用意する。
    素の get_or_create だと format_name が NULL のままになり、
    api_views 側の正規化名での突き合わせに引っかからない歌手ができてしまう。
    """
    artist, created = Artist.objects.get_or_create(
        name=name,
        region=region,
        defaults={"format_name": normalize(name)},
    )
    if not artist.format_name:
        artist.format_name = normalize(name)
        artist.save(update_fields=["format_name"])
    artist.ensure_primary_credit()
    return artist, created


def _upsert_song(artist, title, *, is_cover, lyricist, composer, year, credit=None):
    """
    曲を1件登録／更新する（曲追加画面の単一・複数モード共通）。

    - 既存曲があれば is_cover は常に上書き、
      作詞/作曲/年は「入力があったときだけ」上書きする（空欄で既存値を消さない）。
    - 同時実行で UNIQUE(title, artist) に衝突した場合も取り直して更新に回す。
    - credit（名義）は新規作成のときだけ設定する。未指定なら主名義。
      既存曲の名義を上書きしないのは、admin で振り分けた結果を
      曲追加画面からの再送信で潰さないため。
    """
    song = Song.objects.filter(title=title, artist=artist).first()

    if song is None:
        if credit is None:
            credit = artist.ensure_primary_credit()
        try:
            return Song.objects.create(
                title=title,
                format_title=normalize(title),
                artist=artist,
                credit=credit,
                is_cover=is_cover,
                lyricist=lyricist or None,
                composer=composer or None,
                year=year,
            )
        except IntegrityError:
            # 並行リクエストなどで先に作られていた場合は既存曲の更新に切り替える
            song = Song.objects.get(title=title, artist=artist)

    song.is_cover = is_cover
    if lyricist:
        song.lyricist = lyricist
    if composer:
        song.composer = composer
    if year is not None:
        song.year = year
    song.save(update_fields=["is_cover", "lyricist", "composer", "year"])
    return song


def _save_bulk_score(user, song, raw_score):
    """曲追加画面の点数欄を保存する。空欄・数値以外・範囲外は無視する。"""
    raw_score = (raw_score or "").strip()
    if not raw_score:
        return
    try:
        score = int(raw_score)
    except ValueError:
        return
    if 0 <= score <= 100:
        Rating.objects.update_or_create(user=user, song=song, defaults={"score": score})


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

    # 歌手未選択でもテンプレートが参照するので必ず初期化しておく
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

    # datalist 用：作詞・作曲・年 の既存候補（全曲から重複なしで集める）
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
            "existing_titles_json": existing_titles_json,
            "lyricist_suggestions": lyricist_suggestions,
            "composer_suggestions": composer_suggestions,
            "year_suggestions": year_suggestions,
        }
        if error:
            ctx["error"] = error
        return render(request, "songs/bulk_add.html", ctx)

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
                # Artist の一意制約は (name, region) なので、検索条件にも region を含める
                artist, _ = _get_or_create_artist(new_artist_name, region)
            else:
                return render_form("歌手を選択するか新規入力してください。")

            # 可変行対応：POSTキーから実在する行番号だけを抽出して回す
            indices = _extract_indices(request.POST, "song_title_")
            for i in indices:
                title = request.POST.get(f"song_title_{i}", "").strip()
                if not title:
                    continue

                song = _upsert_song(
                    artist,
                    title,
                    is_cover=request.POST.get(f"song_is_cover_{i}") == "on",
                    lyricist=request.POST.get(f"song_lyricist_{i}", "").strip(),
                    composer=request.POST.get(f"song_composer_{i}", "").strip(),
                    year=_parse_year(request.POST.get(f"song_year_{i}")),
                )
                _save_bulk_score(user, song, request.POST.get(f"song_score_{i}"))

            return redirect("artist_songs", artist_id=artist.id)

        else:
            # 複数歌手モード：行ごとに歌手が違う。歌手を決められない行は黙って飛ばす。
            touched = set()
            indices = _extract_indices(request.POST, "song_title_")
            for i in indices:
                title = request.POST.get(f"song_title_{i}", "").strip()
                if not title:
                    continue

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
                    # Artist の一意制約は (name, region) なので region も検索条件に含める
                    artist, _ = _get_or_create_artist(new_artist_name_i, region)
                else:
                    continue

                song = _upsert_song(
                    artist,
                    title,
                    is_cover=request.POST.get(f"song_is_cover_{i}") == "on",
                    lyricist=request.POST.get(f"song_lyricist_{i}", "").strip(),
                    composer=request.POST.get(f"song_composer_{i}", "").strip(),
                    year=_parse_year(request.POST.get(f"song_year_{i}")),
                )
                _save_bulk_score(user, song, request.POST.get(f"song_score_{i}"))

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


def _parse_json_body(request):
    """
    リクエストボディの JSON を dict として読む。
    戻り値: (payload, error_response) — 失敗時は payload が None。
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, JsonResponse({"error": "JSONが不正です"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "JSONが不正です"}, status=400)
    return payload, None


def _resolve_heatmap_target_user(request, payload):
    """
    ヒートマップ更新APIの保存先ユーザーを決める。
    user_id 未指定ならログインユーザー。他人を指定できるのは staff のみ。
    戻り値: (target_user, error_response) — エラー時は target_user が None。
    """
    raw_user_id = payload.get("user_id")
    if not raw_user_id:
        return request.user, None

    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return None, JsonResponse({"error": "user_idが不正です"}, status=400)

    if (not request.user.is_staff) and (user_id != request.user.id):
        return None, JsonResponse({"error": "権限がありません"}, status=403)
    return get_object_or_404(User, id=user_id), None


@require_POST
@login_required
@transaction.atomic
def artist_year_heatmap_bulk_save(request):
    payload, error = _parse_json_body(request)
    if error:
        return error

    items = payload.get("items") or []
    if not isinstance(items, list):
        return JsonResponse({"error": "itemsが不正です"}, status=400)

    target_user, error = _resolve_heatmap_target_user(request, payload)
    if error:
        return error

    # 入力を (artist_id, year, score) に正規化。score は 0〜4 にクランプし、
    # 数値化できない行は捨てる（画面側の一括送信に壊れた行が混ざっても止めない）。
    cleaned = []
    for it in items:
        try:
            aid = int(it.get("artist_id"))
            year = int(it.get("year"))
            score = int(it.get("score"))
        except (AttributeError, TypeError, ValueError):
            continue
        cleaned.append((aid, year, min(max(score, 0), 4)))

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
    payload, error = _parse_json_body(request)
    if error:
        return error

    try:
        artist_id = int(payload.get("artist_id"))
        y_from = int(payload.get("from"))
        y_to = int(payload.get("to"))
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "パラメータが不正です"}, status=400)

    if y_from > y_to:
        y_from, y_to = y_to, y_from
    score = min(max(score, 0), 4)

    target_user, error = _resolve_heatmap_target_user(request, payload)
    if error:
        return error

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
    payload, error = _parse_json_body(request)
    if error:
        return error

    try:
        artist_id = int(payload.get("artist_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "パラメータが不正です"}, status=400)

    target_user, error = _resolve_heatmap_target_user(request, payload)
    if error:
        return error

    year = timezone.localdate().year

    # 追加直後に行が消えないよう、初期値は 0 ではなく 1（score=0 は削除扱いのため）
    obj, created = ArtistYearPreference.objects.get_or_create(
        user=target_user,
        artist_id=artist_id,
        year=year,
        defaults={"score": 1},
    )
    if not created and obj.score == 0:
        obj.score = 1
        obj.save(update_fields=["score"])

    return JsonResponse({"success": True, "year": year, "created": created})


# ---------------------------------------------------------------------------
# 表示設定（ヘッダー右上のアイコンから開くポップアップ）
# ---------------------------------------------------------------------------


@login_required
def user_settings_view(request):
    """
    ログイン中のユーザー自身の表示設定を読み書きする。
      GET : 現在の設定を返す（ポップアップを開いたときに読みにくる）
      POST: top_ns を保存する
    管理画面に入れない一般ユーザーでも、自分の設定だけはここから変えられる。
    """
    if request.method == "POST":
        try:
            ns = parse_top_ns(request.POST.get("top_ns", ""))
        except ValidationError as e:
            return JsonResponse(
                {"success": False, "error": " / ".join(e.messages)}, status=400
            )

        UserProfile.objects.update_or_create(
            user=request.user, defaults={"top_ns_csv": format_top_ns(ns)}
        )
        return JsonResponse({"success": True, "top_ns": format_top_ns(ns)})

    return JsonResponse(
        {"success": True, "top_ns": format_top_ns(top_ns_for(request.user))}
    )


# ---------------------------------------------------------------------------
# 管理者用：作詞・作曲・年の未入力曲の一括入力
# ---------------------------------------------------------------------------

MISSING_CREDITS_PAGE_SIZE = 100


def _is_superuser(user):
    return user.is_active and user.is_superuser


@login_required
@user_passes_test(_is_superuser)
def missing_credits_view(request):
    """
    作詞・作曲・年のいずれかが未入力の曲を一覧し、まとめて保存する。

    未入力は NULL のほかに空文字もヒットさせる（現状データは NULL のみだが念のため）。
    保存は「表示時から値が変わった項目」だけ更新する。
    """
    if request.method == "POST":
        page = request.POST.get("page", "1")
        filter_qs = request.POST.get("filter_qs", "")
        ids = [int(i) for i in request.POST.getlist("song_id") if i.isdigit()]

        errors = []
        saved = 0
        try:
            with transaction.atomic():
                saved = _save_missing_credits(request.POST, ids, errors)
                if errors:
                    # 1件でも失敗したら全部取り消す
                    raise _MissingCreditsRollback
        except _MissingCreditsRollback:
            for e in errors:
                messages.error(request, e)
            return _render_missing_credits(
                request, page, QueryDict(filter_qs), posted=request.POST
            )

        messages.success(request, f"{saved}曲を保存しました。")
        params = QueryDict(filter_qs, mutable=True)
        params["page"] = page
        return redirect(f"{reverse('missing_credits')}?{params.urlencode()}")

    return _render_missing_credits(request, request.GET.get("page", "1"), request.GET)


class _MissingCreditsRollback(Exception):
    pass


def _rename_artist(artist, new_name):
    """Artist の名前を変える。主名義（ArtistCredit）の表記も合わせる。"""
    old_name = artist.name
    artist.name = new_name
    artist.format_name = normalize(new_name)
    artist.save(update_fields=["name", "format_name"])

    credit = artist.credits.filter(is_primary=True).first()
    if credit and credit.name == old_name:
        # 同じ歌手に新しい名前の別名義が既にあるときは unique に当たるので触らない
        if not artist.credits.filter(name=new_name).exclude(pk=credit.pk).exists():
            credit.name = new_name
            credit.format_name = normalize(new_name)
            credit.save(update_fields=["name", "format_name"])


def _move_song_to_artist(song, target):
    """曲を別の Artist に付け替える。名義も付け替え先のものにする。"""
    old_credit = song.credit
    if old_credit.is_primary:
        new_credit = target.ensure_primary_credit()
    else:
        # 別名義（例: 桑田佳祐）の曲なら、付け替え先にも同じ名義を用意する
        new_credit = target.resolve_credit(old_credit.name)
    song.artist = target
    song.credit = new_credit


def _save_missing_credits(post, ids, errors):
    """
    一覧の入力内容を保存する。保存した曲数を返し、問題は errors に積む。
    呼び出し側の transaction.atomic() の中で動かす前提。

    歌手名の変更:
      - 同じ地域に同名の Artist が既にある → この曲だけその Artist に付け替える
        （付け替えで曲が0件になった元の Artist は削除する）
      - 無い → Artist 自体の名前を変える（その歌手の他の曲にも反映される）
    """
    songs = Song.objects.select_related("artist", "credit").in_bulk(ids)
    # 比較は「表示時の値」と行う。同じ歌手の別の行で先に改名されても、
    # 触っていない行が古い名前で上書きしに来ないようにするため。
    orig_artist_names = {sid: s.artist.name for sid, s in songs.items()}

    saved = 0
    moved_from = set()  # 付け替えで曲が抜けた Artist（最後に0曲なら削除する）
    for song_id in ids:
        song = songs.get(song_id)
        if song is None:
            continue
        label = song.title
        changed = []

        # --- 歌手名 ---
        new_artist_name = post.get(f"artist_{song_id}", "").strip()
        if not new_artist_name:
            errors.append(f"{label}：歌手名が空です")
            continue
        if new_artist_name != orig_artist_names[song_id]:
            artist = Artist.objects.get(pk=song.artist_id)  # 先の行で改名済みかもしれない
            if artist.name != new_artist_name:
                existing = (
                    Artist.objects.filter(name=new_artist_name, region_id=artist.region_id)
                    .exclude(pk=artist.pk)
                    .first()
                )
                if existing:
                    moved_from.add(song.artist_id)
                    _move_song_to_artist(song, existing)
                    changed += ["artist", "credit"]
                else:
                    _rename_artist(artist, new_artist_name)
                    song.artist = artist

        # --- 曲名 ---
        new_title = post.get(f"title_{song_id}", "").strip()
        if not new_title:
            errors.append(f"{label}：曲名が空です")
            continue
        if new_title != song.title or "artist" in changed:
            dup = (
                Song.objects.filter(artist_id=song.artist_id, title=new_title)
                .exclude(pk=song.pk)
                .exists()
            )
            if dup:
                errors.append(
                    f"{label}：「{song.artist.name}」に「{new_title}」は既に登録されています"
                )
                continue
            if new_title != song.title:
                song.title = new_title
                song.format_title = normalize(new_title)
                changed += ["title", "format_title"]

        # --- 作詞・作曲 ---
        for field in ("lyricist", "composer"):
            value = post.get(f"{field}_{song_id}", "").strip() or None
            if value != getattr(song, field):
                setattr(song, field, value)
                changed.append(field)

        # --- 年 ---
        raw_year = post.get(f"year_{song_id}", "").strip()
        if raw_year:
            try:
                year = int(raw_year)
            except ValueError:
                errors.append(f"{label}：年は数値で入力してください（{raw_year}）")
                continue
        else:
            year = None
        if year != song.year:
            song.year = year
            changed.append("year")

        if changed:
            try:
                with transaction.atomic():
                    song.save(update_fields=changed)
            except IntegrityError:
                errors.append(f"{label}：保存できませんでした（重複の可能性があります）")
                continue
            saved += 1

    if not errors and moved_from:
        # 曲が0件になった歌手を消す（名義・別表記・年表の好き度は CASCADE で一緒に消える）
        Artist.objects.filter(pk__in=moved_from, songs__isnull=True).delete()

    return saved


def _missing_credits_filters(params):
    """
    GET パラメータからフィルタ値を取り出す。
    パラメータ自体が無い（初回表示）ときは 邦楽 / オリジナル を既定にし、
    「全て」は "all" で明示的に区別する。
    """
    return {
        "region": params.get("region", "1"),
        "cover": params.get("cover", "0"),
        "artist": params.get("artist", "").strip(),
        "title": params.get("title", "").strip(),
    }


def _missing_credits_queryset(filters):
    """未入力の曲ページの一覧（フィルタ適用後）。画面表示と CSV エクスポートで共用する。"""
    qs = (
        Song.objects.filter(
            Q(lyricist__isnull=True)
            | Q(lyricist="")
            | Q(composer__isnull=True)
            | Q(composer="")
            | Q(year__isnull=True)
        )
        .select_related("artist", "credit")
        .order_by("artist__name", "title", "id")
    )
    if filters["region"] != "all" and filters["region"].isdigit():
        qs = qs.filter(artist__region_id=int(filters["region"]))
    if filters["cover"] == "0":
        qs = qs.filter(is_cover=False)
    elif filters["cover"] == "1":
        qs = qs.filter(is_cover=True)
    if filters["artist"]:
        # 別名義（桑田佳祐 など）でも引っかかるように名義名も見る
        qs = qs.filter(
            Q(artist__name__icontains=filters["artist"])
            | Q(credit__name__icontains=filters["artist"])
        )
    if filters["title"]:
        qs = qs.filter(title__icontains=filters["title"])
    return qs


def _render_missing_credits(request, page_number, params, posted=None):
    filters = _missing_credits_filters(params)
    qs = _missing_credits_queryset(filters)
    page = Paginator(qs, MISSING_CREDITS_PAGE_SIZE).get_page(page_number)

    rows = []
    for song in page.object_list:
        if posted is not None:
            # バリデーションエラー時は入力途中の値を戻す
            title = posted.get(f"title_{song.id}", song.title)
            artist_name = posted.get(f"artist_{song.id}", song.artist.name)
            lyricist = posted.get(f"lyricist_{song.id}", "")
            composer = posted.get(f"composer_{song.id}", "")
            year = posted.get(f"year_{song.id}", "")
        else:
            title = song.title
            artist_name = song.artist.name
            lyricist = song.lyricist or ""
            composer = song.composer or ""
            year = "" if song.year is None else song.year
        rows.append(
            {
                "song": song,
                "title": title,
                "artist_name": artist_name,
                "lyricist": lyricist,
                "composer": composer,
                "year": year,
                "google_q": f"{song.title} {song.credit.name} 作詞 作曲",
            }
        )

    return render(
        request,
        "songs/missing_credits.html",
        {
            "page": page,
            "rows": rows,
            "total": page.paginator.count,
            "filters": filters,
            "regions": MusicRegion.objects.order_by("id"),
            # ページ送り・保存後のリダイレクトでフィルタを引き継ぐためのクエリ文字列
            "filter_qs": urlencode(filters),
        },
    )


def _csv_cell(row, key):
    """CSV のセル値。空欄と "NULL"（大文字小文字問わず）は None とみなす。"""
    value = (row.get(key) or "").strip()
    if not value or value.upper() == "NULL":
        return None
    return value


def _decode_csv(raw):
    """UTF-8（BOM 有無）→ Shift_JIS(cp932) の順に試す。Excel 保存の CSV 対策。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


@require_POST
@login_required
@user_passes_test(_is_superuser)
def missing_credits_import_view(request):
    """
    CSV（列: Artist, Song, lyricist, composer, year）から作詞・作曲・年を埋める。

    - Artist / Song が空の行、歌手名・曲名が完全一致する曲が無い行はスキップ
    - 曲側で既に入力済みの項目は上書きしない（未入力の項目だけ埋める）
    - CSV 側が空の項目は更新しない
    完全一致は Python 側で比較する（MySQL の照合順序は大文字小文字などを区別しないため）。
    """
    filter_qs = request.POST.get("filter_qs", "")
    back = f"{reverse('missing_credits')}?{filter_qs}"

    upload = request.FILES.get("csv_file")
    if not upload:
        messages.error(request, "CSVファイルを選択してください。")
        return redirect(back)

    text = _decode_csv(upload.read())
    if text is None:
        messages.error(request, "CSVの文字コードを判別できませんでした（UTF-8 か Shift_JIS で保存してください）。")
        return redirect(back)

    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(f or "").strip() for f in (reader.fieldnames or [])]
    if not {"Artist", "Song"} <= set(reader.fieldnames):
        messages.error(request, "CSVの1行目に Artist, Song 列が必要です。")
        return redirect(back)

    rows = []
    skipped_blank = 0
    for line_no, row in enumerate(reader, start=2):
        artist_name = _csv_cell(row, "Artist")
        title = _csv_cell(row, "Song")
        if not artist_name or not title:
            skipped_blank += 1
            continue
        rows.append((line_no, artist_name, title, row))

    # 曲名で候補をまとめて取ってきて、(歌手名, 曲名) の完全一致で引けるようにする
    songs_by_key = defaultdict(list)
    titles = list({t for _, _, t, _ in rows})
    for i in range(0, len(titles), 500):
        for song in Song.objects.filter(title__in=titles[i:i + 500]).select_related("artist"):
            songs_by_key[(song.artist.name, song.title)].append(song)

    not_found = []
    bad_years = []
    to_update = {}
    for line_no, artist_name, title, row in rows:
        songs = songs_by_key.get((artist_name, title))
        if not songs:
            not_found.append(f"{line_no}行目 {artist_name} / {title}")
            continue

        values = {
            "lyricist": _csv_cell(row, "lyricist"),
            "composer": _csv_cell(row, "composer"),
        }
        raw_year = _csv_cell(row, "year")
        if raw_year is not None:
            try:
                values["year"] = int(raw_year)
            except ValueError:
                bad_years.append(f"{line_no}行目 {artist_name} / {title}：{raw_year}")

        # 同名の歌手が地域違いで複数いる場合は、一致した曲すべてに入れる
        for song in songs:
            for field, value in values.items():
                if value is None:
                    continue
                current = getattr(song, field)
                if current is None or current == "":
                    setattr(song, field, value)
                    to_update[song.pk] = song

    with transaction.atomic():
        Song.objects.bulk_update(list(to_update.values()), ["lyricist", "composer", "year"], batch_size=500)

    messages.success(
        request,
        f"CSVインポート：{len(to_update)}曲を更新しました"
        f"（対象 {len(rows)}行 / 歌手名・曲名が空でスキップ {skipped_blank}行 / 該当なし {len(not_found)}行）",
    )
    if bad_years:
        messages.error(request, "年が数値でないため年だけスキップ：" + "、".join(bad_years[:20])
                       + (f" ほか{len(bad_years) - 20}件" if len(bad_years) > 20 else ""))
    if not_found:
        messages.warning(request, "該当なし：" + "、".join(not_found[:20])
                         + (f" ほか{len(not_found) - 20}件" if len(not_found) > 20 else ""))
    return redirect(back)


@require_POST
@login_required
@user_passes_test(_is_superuser)
def missing_credits_fetch_view(request):
    """
    1曲ぶん、外部API（MusicBrainz → VocaDB → iTunes）から作詞・作曲・年を取得して返す。
    DB には保存しない（画面の入力欄に入れるだけ。保存はいつもの「保存」ボタンで）。

    歌手名・曲名・作詞・作曲・年は画面上の現在値（未保存の編集を含む）を受け取り、
    空の項目だけを取りに行く。
    """
    # 重い依存（musicbrainzngs / jaconv）はこの画面を使うときだけ読む
    from .credit_fetch import fetch_credits_for_song

    try:
        song = Song.objects.select_related("artist", "credit").get(
            pk=int(request.POST.get("song_id", ""))
        )
    except (Song.DoesNotExist, ValueError):
        return JsonResponse({"success": False, "error": "曲が存在しません"}, status=404)

    current = {f: request.POST.get(f, "").strip() for f in ("lyricist", "composer", "year")}
    try:
        result = fetch_credits_for_song(
            song,
            artist_name=request.POST.get("artist"),
            title=request.POST.get("title"),
            current=current,
        )
    except Exception as e:  # 外部API起因の想定外エラーは画面に出して終わる
        return JsonResponse({"success": False, "error": f"取得に失敗しました: {e}"}, status=502)

    if result.get("_skipped"):
        return JsonResponse({"success": True, "fields": {}, "message": "未入力の項目がありません"})
    if "_error" in result:
        return JsonResponse({"success": False, "error": result["_error"]}, status=502)

    fields = {
        f: {"value": value, "source": source}
        for f, (value, source) in result.get("fields", {}).items()
    }
    diag_lines = [
        "試した表記: " + " / ".join(f"{a}｜{t}" for a, t in result.get("pairs", [])),
    ]
    for api, meta in (result.get("diagnostics") or {}).items():
        others = list(dict.fromkeys(o for o in meta.get("other_artists", []) if o))[:5]
        line = (
            f"{api}: 曲名一致={int(bool(meta.get('title_hit')))} "
            f"歌手一致={int(bool(meta.get('artist_hit')))} "
            f"両方一致={int(bool(meta.get('both_hit')))}"
        )
        if others:
            line += "（曲名一致の別歌手: " + " | ".join(others) + "）"
        diag_lines.append(line)
    if result.get("skipped_apis"):
        diag_lines.append("設定で呼ばないAPI: " + ", ".join(result["skipped_apis"]))
    if result.get("note"):
        diag_lines.append("note: " + result["note"])

    return JsonResponse(
        {
            "success": True,
            "fields": fields,
            "still_missing": result.get("still_missing", []),
            "diagnostics": "\n".join(diag_lines),
        }
    )


@login_required
@user_passes_test(_is_superuser)
def missing_credits_export_view(request):
    """
    未入力の曲ページの一覧（フィルタ適用後・全ページ分）を CSV で返す。
    形式は CSV インポートと同じ（Artist, Song, lyricist, composer, year）。
    全項目を "" で囲う。Excel でそのまま開けるよう UTF-8（BOM付き）にする。
    """
    filters = _missing_credits_filters(request.GET)
    qs = _missing_credits_queryset(filters)

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(["Artist", "Song", "lyricist", "composer", "year"])
    for song in qs.iterator():
        writer.writerow([
            song.artist.name,
            song.title,
            song.lyricist or "",
            song.composer or "",
            "" if song.year is None else song.year,
        ])

    filename = f"missing_credits_{timezone.localtime():%Y%m%d_%H%M%S}.csv"
    response = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_POST
@login_required
@user_passes_test(_is_superuser)
def missing_credits_delete_view(request):
    """
    曲を1件削除する。採点（Rating）は Rating.song の on_delete=CASCADE で一緒に消える。
    （Django の ORM が消すので、DB 側の FK 制約の設定には依存しない）
    その歌手の曲（カバー曲を含む）が0件になったら歌手も削除する。
    """
    try:
        song = Song.objects.get(pk=int(request.POST.get("song_id", "")))
    except (Song.DoesNotExist, ValueError):
        return JsonResponse({"success": False, "error": "曲が存在しません"}, status=404)

    artist_id = song.artist_id
    with transaction.atomic():
        _total, per_model = song.delete()
        # 曲が0件になった歌手は消す（付け替え時と同じ扱い）。
        # カバー曲も Song なので、カバー曲が残っていれば歌手は消えない。
        artist_deleted, _ = Artist.objects.filter(pk=artist_id, songs__isnull=True).delete()
    return JsonResponse(
        {
            "success": True,
            "deleted_ratings": per_model.get("songs.Rating", 0),
            "artist_deleted": bool(artist_deleted),
        }
    )
