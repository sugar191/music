from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Avg, OuterRef, Subquery, IntegerField
from .models import Artist, Song, Rating
from .forms import RatingForm, InlineRatingForm


@login_required
def artist_ranking_view(request, top_n=5):
    user = request.user
    rated_artist_ids = (
        Artist.objects.filter(songs__ratings__user=user)
        .distinct()
        .values_list("id", flat=True)
    )
    artists = Artist.objects.filter(id__in=rated_artist_ids)
    main_artists = []
    others_songs = []

    user_rating_subquery = Rating.objects.filter(
        user=user,
        song=OuterRef("pk"),
    ).values("score")[:1]

    for artist in artists:
        qs = artist.songs.annotate(
            user_score=Subquery(user_rating_subquery, output_field=IntegerField()),
        ).filter(user_score__isnull=False)

        avg_score = qs.aggregate(avg=Avg("user_score"))["avg"] or 0
        songs_with_user_score = qs.order_by("-user_score")[:top_n]
        count_songs = songs_with_user_score.count()

        if count_songs < 5:
            # 「その他」の曲リストに追加（曲をまとめる）
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

    # 「その他」の曲は user_score順でソートして渡す
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
        },
    )


@login_required
def song_detail(request, song_id):
    song = get_object_or_404(Song, pk=song_id)
    average_score = song.ratings.aggregate(Avg("score"))["score__avg"] or 0
    return render(
        request,
        "songs/song_detail.html",
        {
            "song": song,
            "average_score": average_score,
        },
    )


@login_required
def rate_song(request, song_id):
    song = get_object_or_404(Song, pk=song_id)
    try:
        rating = Rating.objects.get(user=request.user, song=song)
    except Rating.DoesNotExist:
        rating = None

    if request.method == "POST":
        form = RatingForm(request.POST, instance=rating)
        if form.is_valid():
            new_rating = form.save(commit=False)
            new_rating.user = request.user
            new_rating.song = song
            new_rating.save()
            return redirect("song_detail", song_id=song.id)
    else:
        form = RatingForm(instance=rating)

    return render(
        request,
        "songs/rate_song.html",
        {
            "song": song,
            "form": form,
        },
    )


@login_required
def song_list_view(request):
    query = request.GET.get("q", "")

    songs = Song.objects.select_related("artist").all()
    if query:
        songs = songs.filter(
            Q(title__icontains=query) | Q(artist__name__icontains=query)
        )

    # 全ての曲のフォームを用意
    rating_forms = {}
    for song in songs:
        try:
            rating = Rating.objects.get(user=request.user, song=song)
        except Rating.DoesNotExist:
            rating = None
        form = InlineRatingForm(instance=rating, prefix=str(song.id))
        rating_forms[song.id] = form

    # 点数のPOST処理（どの曲のフォームか判別して保存）
    if request.method == "POST":
        for song in songs:
            form = InlineRatingForm(request.POST, prefix=str(song.id))
            if form.is_valid():
                score = form.cleaned_data.get("score")
                if score is not None:
                    rating, created = Rating.objects.get_or_create(
                        user=request.user,
                        song=song,
                        defaults={"score": score},
                    )
                    if not created:
                        rating.score = score
                        rating.save()
        return redirect("song_list")  # ← ここをループ外に

    return render(
        request,
        "songs/song_list.html",
        {
            "songs": songs,
            "rating_forms": rating_forms,
            "query": query,
        },
    )
