import os
from collections import defaultdict
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q, OuterRef, Subquery, IntegerField, Count
from django.db.models.functions import Lower, Substr
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from mutagen.easyid3 import EasyID3
from .models import Artist, Song, Rating, MusicRegion, RankView
from .forms import InlineRatingForm
from .services import (
    call_artist_song_top_n,
    call_artist_insufficient_songs,
    call_artist_top_n,
    call_artist_insufficient,
)

MUSIC_DIR = r"C:\Users\pawab\Music"


# 歌手別TOP
@login_required
def ranking_view(request):
    # プルダウンの選択肢を取得
    ranking_options = [5, 7, 10, 15]
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
    other_artists = call_artist_insufficient(selected_user.id, 5, region_id)

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
            "other_artists": other_artists,
        },
    )


@login_required
def song_ranking_view(request):
    users = User.objects.all().order_by("username")

    selected_user_id = request.GET.get("user")
    selected_user = (
        get_object_or_404(User, id=selected_user_id)
        if selected_user_id
        else request.user
    )

    songs = RankView.objects.filter(user_id=selected_user.id)

    return render(
        request,
        "songs/song_ranking.html",
        {
            "users": users,
            "selected_user": selected_user,
            "songs": songs,
            "is_own_page": selected_user == request.user,
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
def artist_song_list_view(request, artist_id):
    from .forms import InlineRatingForm  # 必要に応じて

    artist = get_object_or_404(Artist, pk=artist_id)
    user = request.user

    user_score_subquery = Rating.objects.filter(user=user, song=OuterRef("pk")).values(
        "score"
    )[:1]

    songs = list(
        artist.songs.annotate(
            user_score=Subquery(user_score_subquery, output_field=IntegerField())
        ).select_related("artist")
    )

    # スコア降順 → タイトル昇順
    songs.sort(key=lambda s: (-(s.user_score or -1), s.title))

    # 順位付け（スキップあり）
    ranked_songs = []
    prev_score = None
    current_rank = 0
    next_rank = 1

    rating_forms = {}

    for song in songs:
        if song.user_score != prev_score:
            current_rank = next_rank
        try:
            rating = Rating.objects.get(user=user, song=song)
        except Rating.DoesNotExist:
            rating = None
        form = InlineRatingForm(instance=rating, prefix=str(song.id))
        rating_forms[song.id] = form

        ranked_songs.append(
            {
                "song": song,
                "rank": current_rank,
                "user_score": song.user_score,
            }
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


# 曲追加
@login_required
def bulk_add_view(request):
    regions = MusicRegion.objects.all()
    artists = Artist.objects.all().order_by("name")

    # ① GETのartist_idを受け取り
    selected_artist_id = request.GET.get("artist_id") or ""
    selected_region_id = ""

    # ② artist_id から region も決めておく（あれば）
    if selected_artist_id:
        try:
            sel_artist = Artist.objects.select_related("region").get(
                id=selected_artist_id
            )
            selected_region_id = str(sel_artist.region_id)
        except Artist.DoesNotExist:
            selected_artist_id = ""  # 変なIDなら無視

    if request.method == "POST":
        user = request.user

        region_id = request.POST.get("region_id")
        artist_id = request.POST.get("artist_id")
        new_artist_name = request.POST.get("new_artist_name", "").strip()

        # --- 歌手の取得または作成 ---
        if artist_id:
            try:
                artist = Artist.objects.get(id=artist_id)
            except Artist.DoesNotExist:
                return render(
                    request,
                    "songs/bulk_add.html",
                    {
                        "artist_id": artist_id,
                        "artists": artists,
                        "regions": regions,
                        "error": "選択された歌手が存在しません。",
                    },
                )
        elif new_artist_name:
            if not region_id:
                return render(
                    request,
                    "songs/bulk_add.html",
                    {
                        "artist_id": artist_id,
                        "artists": artists,
                        "regions": regions,
                        "range20": range(1, 21),  # ★ 忘れずに渡す
                        "error": "新しい歌手名を登録する場合、地域を選択してください。",
                    },
                )
            try:
                region = MusicRegion.objects.get(id=region_id)
            except MusicRegion.DoesNotExist:
                return render(
                    request,
                    "songs/bulk_add.html",
                    {
                        "artist_id": artist_id,
                        "artists": artists,
                        "regions": regions,
                        "range20": range(1, 21),  # ← ここを必ず追加
                        "error": "選択された地域が存在しません。",
                    },
                )
            artist, _ = Artist.objects.get_or_create(
                name=new_artist_name, defaults={"region": region}
            )
        else:
            return render(
                request,
                "songs/bulk_add.html",
                {
                    "artist_id": artist_id,
                    "artists": artists,
                    "regions": regions,
                    "range20": range(1, 21),  # ← ここを必ず追加
                    "error": "歌手を選択するか新規入力してください。",
                },
            )

        # --- 曲と点数を処理（最大20曲） ---
        for i in range(1, 21):
            title = request.POST.get(f"song_title_{i}", "").strip()
            score = request.POST.get(f"song_score_{i}", "").strip()
            is_cover = (
                request.POST.get(f"song_is_cover_{i}") == "on"
            )  # ← チェックされていれば True

            if not title:
                continue  # 入力なしはスキップ

            song = Song.objects.filter(title=title, artist=artist).first()
            if not song:
                try:
                    song = Song.objects.create(
                        title=title, artist=artist, is_cover=is_cover
                    )
                except IntegrityError:
                    song = Song.objects.get(title=title, artist=artist)
            else:
                # 既存曲ならフラグを更新しても良い（任意）
                song.is_cover = is_cover
                song.save()

            if score:
                try:
                    score = int(score)
                    if 0 <= score <= 100:
                        Rating.objects.update_or_create(
                            user=user, song=song, defaults={"score": score}
                        )
                except ValueError:
                    pass

        return redirect("song_list")

    return render(
        request,
        "songs/bulk_add.html",
        {
            # テンプレで使う
            "selected_artist_id": str(selected_artist_id),
            "selected_region_id": str(selected_region_id),
            "artists": artists,
            "regions": regions,
            "range20": range(1, 21),
        },
    )


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


# 歌手検索
@login_required
def artist_search_view(request):
    regions = MusicRegion.objects.all()

    # 入力したパラメータを取得
    if "region_id" not in request.GET:
        # region_id パラメータがまったくない場合の処理（例：初期値1を使う）
        region_id = "1"
    else:
        region_id = request.GET.get("region_id")
        if region_id == "":
            region_id = None
    # 動的条件（例：?region=1&prefix=A）
    prefix = request.GET.get("prefix")  # 先頭文字（A など）
    # user_id は現在ログインユーザーを利用（固定の1ではない）
    user = request.user

    filter_top = request.GET.get("top")  # '5', '10', '15' のいずれか

    qs = Artist.objects.select_related("region").annotate(
        # COUNT(1) song_count
        song_count=Count("songs", filter=Q(songs__is_cover=False), distinct=True),
        # SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) rating_count
        # => ユーザーでフィルタした Rating を Count
        rating_count=Count(
            "songs__ratings",
            filter=Q(songs__is_cover=False, songs__ratings__user=user),
            distinct=True,
        ),
    )

    if region_id:
        qs = qs.filter(region_id=region_id)

    if prefix:
        # a.name LIKE 'A%'（大文字小文字を吸収するなら istartswith）
        qs = qs.filter(name__istartswith=prefix)
    else:
        prefix = ""

    # ORDER BY UPPER(a.name) 相当 → Lower()でケース無視ソート
    qs = qs.order_by(Lower("name"))

    # プルダウンの条件
    if filter_top in ["5", "10", "15"]:
        n = int(filter_top)
        qs = qs.filter(song_count__gte=n, rating_count__lt=n)
    # ページング（任意）
    # from django.core.paginator import Paginator
    # page_obj = Paginator(qs, 50).get_page(request.GET.get("page"))

    return render(
        request,
        "songs/artist_search.html",
        {
            "artists": qs,  # or "page_obj": page_obj
            "regions": regions,
            "region_id": region_id,
            "prefix": prefix,
            "top": filter_top,
        },
    )
