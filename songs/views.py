import os
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q, Sum, OuterRef, Subquery, IntegerField
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from mutagen.easyid3 import EasyID3
from .models import Artist, Song, Rating, MusicRegion
from .forms import InlineRatingForm

MUSIC_DIR = r"C:\Users\pawab\Music"


@login_required
def ranking_view(request):
    ranking_options = [5, 7, 10]
    regions = MusicRegion.objects.all()

    region_id = request.GET.get("region_id") or "1"
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

    rated_artist_ids = (
        Artist.objects.filter(songs__ratings__user=selected_user, songs__is_cover=0)
        .distinct()
        .values_list("id", flat=True)
    )

    artists = Artist.objects.filter(id__in=rated_artist_ids)
    if region_id:
        try:
            region_id_int = int(region_id)
            artists = artists.filter(region_id=region_id_int)
        except ValueError:
            pass

    main_artists = []
    others_songs = []

    user_rating_subquery = Rating.objects.filter(
        user=selected_user, song=OuterRef("pk"), song__is_cover=0
    ).values("score")[:1]

    for artist in artists:
        qs = artist.songs.annotate(
            user_score=Subquery(user_rating_subquery, output_field=IntegerField())
        ).filter(user_score__isnull=False)

        songs_with_user_score = list(qs.order_by("-user_score")[:top_n])
        total_score = sum(song.user_score for song in songs_with_user_score)
        count_songs = len(songs_with_user_score)

        if count_songs < top_n:
            others_songs.extend(songs_with_user_score[:10])
        else:
            # 曲順位付け（スキップあり、同点同順位）
            songs_with_rank = []
            prev_score = None
            current_rank = 0
            next_rank = 1

            for song in songs_with_user_score:
                if song.user_score != prev_score:
                    current_rank = next_rank
                songs_with_rank.append(
                    {
                        "song": song,
                        "rank": current_rank,
                        "user_score": song.user_score,
                    }
                )
                prev_score = song.user_score
                next_rank += 1

            main_artists.append(
                {
                    "artist": artist,
                    "total_score": total_score,
                    "top_songs": songs_with_rank,  # 曲の順位付き
                }
            )

    # アーティスト順位付け（スキップあり）
    main_artists.sort(key=lambda x: x["total_score"], reverse=True)
    ranked_main_artists = []
    prev_score = None
    current_rank = 0
    next_rank = 1

    for artist_dict in main_artists:
        if artist_dict["total_score"] != prev_score:
            current_rank = next_rank
        artist_dict["rank"] = current_rank
        ranked_main_artists.append(artist_dict)
        prev_score = artist_dict["total_score"]
        next_rank += 1

    # その他の曲
    others_songs.sort(
        key=lambda s: s.user_score if s.user_score is not None else 0, reverse=True
    )

    ranked_others_songs = []
    prev_score = None
    current_rank = 0
    next_rank = 1

    for song in others_songs:
        if song.user_score != prev_score:
            current_rank = next_rank
        ranked_others_songs.append(
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
        "songs/ranking.html",
        {
            "regions": regions,
            "main_artists": ranked_main_artists,
            "others_songs": ranked_others_songs,
            "top_n": top_n,
            "ranking_options": ranking_options,
            "region_id": region_id,
            "selected_user": selected_user,
            "is_own_page": selected_user == request.user,
            "all_users": User.objects.all().order_by("username"),
        },
    )


def get_artist_list(selected_user, region_id, top_n):
    rated_artist_ids = (
        Artist.objects.filter(songs__ratings__user=selected_user, songs__is_cover=0)
        .distinct()
        .values_list("id", flat=True)
    )
    artists = Artist.objects.filter(id__in=rated_artist_ids)

    # region_idが指定されていれば絞り込む
    if region_id:
        try:
            region_id_int = int(region_id)
            artists = artists.filter(region_id=region_id_int)
        except ValueError:
            pass  # region_idが不正な場合は無視

    main_artists = []
    other_artists = []

    user_rating_subquery = Rating.objects.filter(
        user=selected_user,
        song=OuterRef("pk"),
        song__is_cover=0,
    ).values("score")[:1]

    for artist in artists:
        qs = artist.songs.annotate(
            user_score=Subquery(user_rating_subquery, output_field=IntegerField()),
        ).filter(user_score__isnull=False)

        songs_with_user_score = qs.order_by("-user_score")[:top_n]
        total_score = (
            songs_with_user_score.aggregate(total=Sum("user_score"))["total"] or 0
        )
        count_songs = songs_with_user_score.count()

        if count_songs >= top_n:
            main_artists.append(
                {
                    "artist": artist,
                    "total_score": total_score,
                }
            )
        else:
            other_artists.append(
                {
                    "artist": artist,
                    "rated_count": count_songs,
                    "total_songs": artist.songs.count(),
                }
            )

    main_artists.sort(key=lambda x: x["total_score"], reverse=True)

    ranked_artists = []
    prev_score = None
    current_rank = 0
    next_rank = 1  # 次に割り当てるべき順位

    for artist_dict in main_artists:
        score = artist_dict["total_score"]
        if score != prev_score:
            current_rank = next_rank  # 順位を更新
        artist_dict["rank"] = current_rank
        ranked_artists.append(artist_dict)

        prev_score = score
        next_rank += 1  # 次のループで使う順位（スキップあり）

    other_artists.sort(key=lambda x: (x["rated_count"], x["total_songs"]), reverse=True)

    return ranked_artists, other_artists


@login_required
def artist_list_view(request):
    regions = MusicRegion.objects.all()
    region_id = request.GET.get("region_id")
    if region_id is None:
        region_id = "1"  # 初期値として邦楽 (id=1)
    selected_user_id = request.GET.get("user")
    if selected_user_id:
        selected_user = get_object_or_404(User, id=selected_user_id)
    else:
        selected_user = request.user

    top5, other_artists = get_artist_list(selected_user, region_id, 5)
    top7, _ = get_artist_list(selected_user, region_id, 7)
    top10, _ = get_artist_list(selected_user, region_id, 10)

    return render(
        request,
        "songs/artist_list.html",
        {
            "top5": top5,
            "top7": top7,
            "top10": top10,
            "other_artists": other_artists,
            "regions": regions,
            "region_id": region_id,
            "selected_user": selected_user,
            "all_users": User.objects.all().order_by("username"),
        },
    )


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


@login_required
def bulk_add_view(request):
    regions = MusicRegion.objects.all()
    artists = Artist.objects.all().order_by("name")
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
                        "artists": artists,
                        "regions": regions,
                        "range10": range(1, 11),  # ★ 忘れずに渡す
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
                        "artists": artists,
                        "regions": regions,
                        "range10": range(1, 11),  # ← ここを必ず追加
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
                    "artists": artists,
                    "regions": regions,
                    "range10": range(1, 11),  # ← ここを必ず追加
                    "error": "歌手を選択するか新規入力してください。",
                },
            )

        # --- 曲と点数を処理（最大10曲） ---
        for i in range(1, 11):
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
            "artists": artists,
            "regions": regions,
            "range10": range(1, 11),
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
