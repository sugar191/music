import os
import re
import json
from collections import defaultdict
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q, OuterRef, Subquery, IntegerField, Count, Min
from django.db.models.functions import Coalesce, Lower
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from mutagen.easyid3 import EasyID3
from .models import (
    Artist,
    Song,
    Rating,
    MusicRegion,
    RankView,
    ArtistYearPreference,
    UserProfile,
)
from .forms import InlineRatingForm
from .services import (
    call_artist_song_top_n,
    call_artist_insufficient_songs,
    call_artist_top_n,
)

MUSIC_DIR = r"C:\Users\pawab\Music"


# 歌手別TOP
@login_required
def ranking_view(request):
    # プルダウンの選択肢を取得
    ranking_options = [5, 10, 15, 20]
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
    try:
        top_n = int(request.GET.get("top_n", 5))
    except ValueError:
        top_n = 5

    # 歌手別TOPを取得
    top_n_data = call_artist_song_top_n(selected_user.id, top_n, region_id)
    insufficient_data = call_artist_insufficient_songs(
        selected_user.id, top_n, region_id
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
        grouped[key].append(row)

    # ソートしてリスト化
    sorted_artists = sorted(grouped.items(), key=lambda x: x[0][0])

    # 辞書リストに変換（テンプレートでわかりやすくアクセスできるように）
    rankings = []
    for key, songs in sorted_artists:
        artist_rank, artist_id, artist_name, total_score = key
        rankings.append(
            {
                "artist_rank": artist_rank,
                "artist_id": artist_id,
                "artist_name": artist_name,
                "total_score": total_score,
                "songs": songs,
            }
        )

    return render(
        request,
        "songs/ranking.html",
        {
            "ranking_options": ranking_options,
            "regions": regions,
            "all_users": users,
            "top_n": top_n,
            "region_id": region_id,
            "selected_user": selected_user,
            "is_own_page": selected_user == request.user,
            "rankings": rankings,
            "insufficient_songs": insufficient_data,
        },
    )


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

    top5 = call_artist_top_n(selected_user.id, 5, region_id)
    top10 = call_artist_top_n(selected_user.id, 10, region_id)
    top15 = call_artist_top_n(selected_user.id, 15, region_id)
    top20 = call_artist_top_n(selected_user.id, 20, region_id)
    #    other_artists = call_artist_insufficient(selected_user.id, 5, region_id)

    return render(
        request,
        "songs/artist_list.html",
        {
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "top5": top5,
            "top10": top10,
            "top15": top15,
            "top20": top20,
            #            "other_artists": other_artists,
        },
    )


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

    Ns = [5, 10, 15, 20]

    # artist_id -> row(dict)
    rows_by_artist = {}

    for n in Ns:
        data = call_artist_top_n(selected_user.id, n, region_id)
        for r in data:
            aid = r["artist_id"]
            row = rows_by_artist.setdefault(
                aid,
                {
                    "artist_id": aid,
                    "artist_name": r["artist_name"],
                    "region_id": r.get("region_id"),
                },
            )
            row[f"rank_{n}"] = r["artist_rank"]
            row[f"score_{n}"] = r["total_score"]  # ついでに合計点も使いたければ

    # 並び順：20→15→10→5 の順位がある順に昇順
    BIG = 10**9

    def sort_key(row):
        return (
            row.get("rank_5", BIG),
            row.get("rank_10", BIG),
            row.get("rank_15", BIG),
            row.get("rank_20", BIG),
            row.get("artist_name", ""),
        )

    matrix_rows = sorted(rows_by_artist.values(), key=sort_key)

    return render(
        request,
        "songs/artist_rank_matrix.html",
        {
            "regions": regions,
            "all_users": users,
            "region_id": region_id,
            "selected_user": selected_user,
            "rows": matrix_rows,
        },
    )


@login_required
def song_ranking_view(request):
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

    qs = RankView.objects.filter(user_id=selected_user.id)

    if region_id:
        qs = qs.filter(region_id=region_id).annotate(
            display_rank=F("rank_region"),
            display_order=F("order_region"),
        )
    else:
        qs = qs.annotate(
            display_rank=F("rank_all"),
            display_order=F("order_all"),
        )
    qs = qs.order_by("display_order")

    return render(
        request,
        "songs/song_ranking.html",
        {
            "regions": regions,
            "users": users,
            "selected_user": selected_user,
            "songs": qs,  # テンプレートは display_rank / display_order を表示
            "is_own_page": selected_user == request.user,
            "region_id": region_id,
        },
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

    rating_forms = {}
    for song in page_obj:
        try:
            rating = Rating.objects.get(user=request.user, song=song)
        except Rating.DoesNotExist:
            rating = None
        form = InlineRatingForm(instance=rating, prefix=str(song.id))
        rating_forms[song.id] = form

    return render(
        request,
        "songs/song_list.html",
        {
            "page_obj": page_obj,
            "rating_forms": rating_forms,
            "query": query,
        },
    )


# 歌手の曲リスト
@login_required
# 歌手の曲リスト
@login_required
def artist_song_list_view(request, artist_id):
    from .forms import InlineRatingForm

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

    songs = list(
        artist.songs.annotate(
            user_score=Subquery(user_score_subquery, output_field=IntegerField())
        )
        .select_related("artist")
        .order_by("is_cover", "-user_score", Lower("title"))
    )

    ranked_songs = []
    prev_score = None
    current_rank = 0
    next_rank = 1

    rating_forms = {}

    for song in songs:
        if song.user_score != prev_score:
            current_rank = next_rank

        # ★変更：フォームも selected_user の Rating を参照
        try:
            rating = Rating.objects.get(user=selected_user, song=song)
        except Rating.DoesNotExist:
            rating = None
        form = InlineRatingForm(instance=rating, prefix=str(song.id))
        rating_forms[song.id] = form

        ranked_songs.append(
            {"song": song, "rank": current_rank, "user_score": song.user_score}
        )

        prev_score = song.user_score
        next_rank += 1

    return render(
        request,
        "songs/artist_song_list.html",
        {
            "artist": artist,
            "songs": ranked_songs,
            "rating_forms": rating_forms,
            "all_users": users,  # ★追加
            "selected_user": selected_user,  # ★追加
            "is_own_page": is_own_page,  # ★追加
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
    artists = Artist.objects.select_related("region").all().order_by("name")

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
                if not title:
                    continue

                song = Song.objects.filter(title=title, artist=artist).first()
                if not song:
                    try:
                        song = Song.objects.create(
                            title=title, artist=artist, is_cover=is_cover
                        )
                    except IntegrityError:
                        song = Song.objects.get(title=title, artist=artist)
                else:
                    song.is_cover = is_cover
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
                            title=title, artist=artist, is_cover=is_cover
                        )
                    except IntegrityError:
                        song = Song.objects.get(title=title, artist=artist)
                else:
                    song.is_cover = is_cover
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


# 歌手検索
@login_required
def artist_search_view(request):
    regions = MusicRegion.objects.all()

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

    # まずは今まで通り DB 側で集計
    qs = Artist.objects.select_related("region").annotate(
        song_count=Count("songs", filter=Q(songs__is_cover=False), distinct=True),
        rating_count=Count(
            "songs__ratings",
            filter=Q(songs__is_cover=False, songs__ratings__user=user),
            distinct=True,
        ),
    )

    if region_id:
        qs = qs.filter(region_id=region_id)

    # プルダウンの条件（ここも DB 側で）
    if filter_top in ["5", "10", "15", "20"]:
        n = int(filter_top)
        qs = qs.filter(song_count__gte=n - 5, song_count__lt=n)
    elif filter_top == "0":
        qs = qs.filter(song_count__gt=F("rating_count"))
    elif filter_top == "20~":
        qs = qs.filter(song_count__gt=F("rating_count"), rating_count__gte=20)

    # ここから Python 側で ひらがな/カタカナ無視のフィルタ & ソート
    artists = list(qs)  # クエリを評価してリスト化（annotation はそのまま乗ってる）

    # アーティスト名を ひらがな + 小文字 に正規化して持たせる
    for a in artists:
        a.name_norm = kana_to_hiragana(a.name.lower())

    # prefix フィルタ（ひらがな/カタカナ無視）
    if prefix_norm:
        artists = [a for a in artists if a.name_norm.startswith(prefix_norm)]

    # ソートも正規化した名前で
    artists.sort(key=lambda x: x.name_norm)

    return render(
        request,
        "songs/artist_search.html",
        {
            "artists": artists,
            "regions": regions,
            "region_id": region_id,
            "prefix": prefix,
            "top": filter_top,
        },
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
