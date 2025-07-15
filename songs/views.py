from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q, Sum, OuterRef, Subquery, IntegerField
from .models import Artist, Song, Rating, MusicRegion
from .forms import InlineRatingForm
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError
from django.contrib.auth.forms import UserCreationForm


@login_required
def artist_ranking_view(request):
    ranking_options = [5, 7, 10]
    regions = MusicRegion.objects.all()
    region_id = request.GET.get("region_id")
    if region_id is None:
        region_id = "1"  # 初期値として邦楽 (id=1)
    selected_user_id = request.GET.get("user")
    if selected_user_id:
        selected_user = get_object_or_404(User, id=selected_user_id)
    else:
        selected_user = request.user

    try:
        top_n = int(request.GET.get("top_n", 5))
    except ValueError:
        top_n = 5  # 不正な値だった場合のフォールバック

    rated_artist_ids = (
        Artist.objects.filter(songs__ratings__user=selected_user)
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
        user=selected_user,
        song=OuterRef("pk"),
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

        if count_songs < top_n:
            others_songs.extend(list(songs_with_user_score[:10]))
        else:
            main_artists.append(
                {
                    "artist": artist,
                    "total_score": total_score,
                    "top_songs": songs_with_user_score,
                }
            )

    main_artists.sort(key=lambda x: x["total_score"], reverse=True)
    others_songs.sort(
        key=lambda s: s.user_score if s.user_score is not None else 0, reverse=True
    )

    return render(
        request,
        "songs/artist_ranking.html",
        {
            "regions": regions,
            "main_artists": main_artists,
            "others_songs": others_songs,
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
        Artist.objects.filter(songs__ratings__user=selected_user)
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
    other_artists.sort(key=lambda x: (x["rated_count"], x["total_songs"]), reverse=True)

    return main_artists, other_artists


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

            if not title:
                continue  # 入力なしはスキップ

            # 曲の取得または新規作成（重複チェック込み）
            song = Song.objects.filter(title=title, artist=artist).first()
            if not song:
                try:
                    song = Song.objects.create(title=title, artist=artist)
                except IntegrityError:
                    song = Song.objects.get(title=title, artist=artist)
            # 点数がある場合のみ評価を登録・更新
            if score:
                try:
                    score = int(score)
                    if 0 <= score <= 100:
                        Rating.objects.update_or_create(
                            user=user, song=song, defaults={"score": score}
                        )
                except ValueError:
                    pass  # 無効な数値はスキップ

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
