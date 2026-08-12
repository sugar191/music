"""
指定したURLのサーバー処理時間をプロファイルする開発用コマンド。

ブラウザや回線を挟まずビューだけを実行するので、
「サーバー側の何に時間がかかっているか」を切り分けられる。

使い方:
    python manage.py profile_page /artist_search/
    python manage.py profile_page /artist_search/ --user pawaburo --top 30
"""

import cProfile
import io
import pstats
import time

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.test import Client


class Command(BaseCommand):
    help = "指定URLのサーバー処理時間をプロファイルする（開発用）"

    def add_arguments(self, parser):
        parser.add_argument("path", help="対象URL（例: /artist_search/）")
        parser.add_argument(
            "--user",
            default=None,
            help="ログインするユーザー名（省略時は最初のユーザー）",
        )
        parser.add_argument(
            "--top", type=int, default=25, help="表示する関数の件数（既定25）"
        )

    def handle(self, *args, **options):
        path = options["path"]
        username = options["user"]

        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"ユーザーが見つかりません: {username}")
        else:
            user = User.objects.order_by("id").first()
            if user is None:
                raise CommandError("ユーザーが1人も登録されていません")

        # ALLOWED_HOSTS に testserver を足さずに済むよう localhost として実行する
        client = Client(SERVER_NAME="localhost")
        client.force_login(user)

        # 1回目はテンプレートの読み込みなどが混ざるので捨てる
        first = client.get(path)
        if first.status_code != 200:
            raise CommandError(f"status={first.status_code} が返りました: {path}")

        started = time.perf_counter()
        response = client.get(path)
        elapsed = time.perf_counter() - started

        self.stdout.write("")
        self.stdout.write(
            f"{path}  user={user.username}  status={response.status_code}  "
            f"{len(response.content):,} bytes  {elapsed:.3f} 秒"
        )
        self.stdout.write("")

        profiler = cProfile.Profile()
        profiler.enable()
        client.get(path)
        profiler.disable()

        buffer = io.StringIO()
        pstats.Stats(profiler, stream=buffer).sort_stats("cumulative").print_stats(
            options["top"]
        )
        self.stdout.write(buffer.getvalue())
