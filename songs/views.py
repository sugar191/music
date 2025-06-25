from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q, Avg, OuterRef, Subquery, IntegerField
from .models import Artist, Song, Rating
from .forms import InlineRatingForm
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError


@login_required
def artist_ranking_view(request, top_n=5):
    selected_user_id = request.GET.get("user")
    if selected_user_id:
        selected_user = get_object_or_404(User, id=selected_user_id)
    else:
        selected_user = request.user

    rated_artist_ids = (
        Artist.objects.filter(songs__ratings__user=selected_user)
        .distinct()
        .values_list("id", flat=True)
    )
    artists = Artist.objects.filter(id__in=rated_artist_ids)
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
        avg_score = songs_with_user_score.aggregate(avg=Avg("user_score"))["avg"] or 0
        count_songs = songs_with_user_score.count()

        if count_songs < 5:
            others_songs.extend(list(songs_with_user_score[:10]))
        else:
            main_artists.append(
                {
                    "artist": artist,
                    "avg_score": avg_score,
                    "top_songs": songs_with_user_score,
                }
            )

    main_artists.sort(key=lambda x: x["avg_score"], reverse=True)
    others_songs.sort(
        key=lambda s: s.user_score if s.user_score is not None else 0, reverse=True
    )

    return render(
        request,
        "songs/artist_ranking.html",
        {
            "main_artists": main_artists,
            "others_songs": others_songs,
            "top_n": top_n,
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
    if request.method == "POST":
        user = request.user

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
                        "artists": Artist.objects.all(),
                        "error": "選択された歌手が存在しません。",
                    },
                )
        elif new_artist_name:
            artist, _ = Artist.objects.get_or_create(name=new_artist_name)
        else:
            return render(
                request,
                "songs/bulk_add.html",
                {
                    "artists": Artist.objects.all(),
                    "error": "歌手を選択するか新規入力してください。",
                },
            )

        # --- 曲と点数を処理（最大10曲） ---
        for i in range(1, 11):
            title = request.POST.get(f"song_title_{i}", "").strip()
            score = request.POST.get(f"song_score_{i}", "").strip()

            if not title or not score:
                continue  # 入力なしはスキップ

            try:
                score = int(score)
                if not (0 <= score <= 100):
                    continue  # 範囲外はスキップ
            except ValueError:
                continue  # 無効な数値はスキップ

            # 曲の取得または新規作成（重複チェック込み）
            song = Song.objects.filter(title=title, artist=artist).first()
            if not song:
                try:
                    song = Song.objects.create(title=title, artist=artist)
                except IntegrityError:
                    # 万が一 race condition で重複が発生したら再取得
                    song = Song.objects.get(title=title, artist=artist)

            # 評価の作成・更新
            Rating.objects.update_or_create(
                user=user, song=song, defaults={"score": score}
            )

        return redirect("song_list")  # ✅ 適宜変更可

    return render(request, "songs/bulk_add.html", {"artists": Artist.objects.all()})
