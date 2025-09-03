from django.db.models import Avg, Count
from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Song, Rating
from .serializers import SongSerializer, RatingSerializer, SongLookupCreateSerializer


class AuthRequired(permissions.IsAuthenticated):
    """記述短縮用（必要ならカスタム権限へ拡張）"""

    pass


class SongViewSet(viewsets.ReadOnlyModelViewSet):
    """
    曲の参照（一覧・詳細）。
    既存の画面はそのまま使い、外部クライアント用にRESTも提供。
    """

    queryset = Song.objects.select_related("artist__region").all()
    serializer_class = SongSerializer
    permission_classes = [AuthRequired]


class RatingViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.CreateModelMixin,
):
    """
    自分の採点を扱うViewSet。

    - GET /api/ratings/?song=<id>       … 自分のその曲の採点（存在すれば1件）
    - POST /api/ratings/ {song, score}   … アップサート（user×songでupdate_or_create）
    - PATCH /api/ratings/<id>/ {score}   … 自分の採点を更新
    - GET /api/ratings/stats/?song=<id>  … 曲の平均/件数/10点刻み分布＋自分のスコア
    - POST /api/ratings/batch_get/ {song_ids:[...]} … 複数曲の自分スコア
    """

    serializer_class = RatingSerializer
    permission_classes = [AuthRequired]

    def get_queryset(self):
        qs = Rating.objects.select_related(
            "song", "song__artist", "song__artist__region"
        ).filter(user=self.request.user)
        song_id = self.request.query_params.get("song")
        if song_id:
            qs = qs.filter(song_id=song_id)
        return qs

    def perform_create(self, serializer):
        """
        user×song の一意制約に合わせてアップサート動作にする。
        """
        user = self.request.user
        song = serializer.validated_data["song"]
        score = serializer.validated_data["score"]
        obj, _created = Rating.objects.update_or_create(
            user=user, song=song, defaults={"score": score}
        )
        serializer.instance = obj

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """
        曲ごとの集計＋自分のスコア
        GET /api/ratings/stats/?song=<id>
        """
        song_id = request.query_params.get("song")
        if not song_id:
            return Response({"detail": "song query param required"}, status=400)

        song = Song.objects.select_related("artist__region").filter(id=song_id).first()
        if not song:
            return Response({"detail": "song not found"}, status=404)

        agg = Rating.objects.filter(song_id=song_id).aggregate(
            avg=Avg("score"), cnt=Count("id")
        )
        my = Rating.objects.filter(user=request.user, song_id=song_id).first()

        # 10点刻み分布（0-9,10-19,...,90-100）
        bins = {f"{b}-{b+9 if b < 100 else 100}": 0 for b in range(0, 100, 10)}
        for s in Rating.objects.filter(song_id=song_id).values_list("score", flat=True):
            b = (s // 10) * 10
            key = f"{b}-{b+9 if b < 100 else 100}"
            bins[key] = bins.get(key, 0) + 1

        return Response(
            {
                "song": SongSerializer(song).data,
                "avg": round(agg["avg"], 1) if agg["avg"] is not None else None,
                "count": agg["cnt"],
                "my_score": my.score if my else None,
                "hist10": bins,
            }
        )

    @action(detail=False, methods=["post"])
    def batch_get(self, request):
        """
        複数曲の自分スコアを一括取得
        POST { "song_ids": [1,2,3] }
        """
        ids = request.data.get("song_ids") or []
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "song_ids required"}, status=400)

        rows = Rating.objects.filter(user=request.user, song_id__in=ids).values_list(
            "song_id", "score"
        )
        result = {sid: score for sid, score in rows}
        return Response({"results": result}, status=200)


class SongLookupOrCreateApi(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = SongLookupCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        result = ser.save()  # create() が辞書を返す実装
        return Response(result, status=status.HTTP_200_OK)
