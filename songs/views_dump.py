"""
本番DBのテーブルを mysqldump でダンプし、ダウンロードさせるための管理用API。

認証は settings.EXPORT_API_TOKEN との突き合わせのみ（Djangoのログインとは独立）。
トークンは X-Export-Token ヘッダ / POST / GET のいずれかで渡す。
"""

import os
import datetime
import subprocess
from django.conf import settings
from django.http import (
    JsonResponse,
    FileResponse,
    HttpResponseBadRequest,
    HttpResponse,
    Http404,
)
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt

EXPORT_API_TOKEN = getattr(settings, "EXPORT_API_TOKEN", "")
DB = settings.DATABASES["default"]
PA_DB_HOST = DB.get("HOST") or "127.0.0.1"
PA_DB_PORT = str(DB.get("PORT") or "3306")
PA_DB_NAME = DB.get("NAME")
PA_DB_USER = DB.get("USER")
PA_DB_PASS = DB.get("PASSWORD")  # 未設定（None）なら -p を渡さず ~/.my.cnf に任せる

DUMP_DIR = os.path.expanduser("~/dumps/music")


def _auth(request):
    """
    共有トークンによる認証。
    トークンが未設定（空）のときは常に不許可にして、事故で全公開になるのを防ぐ。
    """
    if not EXPORT_API_TOKEN:
        return False
    tok = (
        request.headers.get("X-Export-Token")
        or request.POST.get("token")
        or request.GET.get("token")
    )
    return tok == EXPORT_API_TOKEN


def _dump_one(table: str, outfile: str):
    """1テーブルを「データのみ」でダンプして outfile に書き出す。"""
    os.makedirs(DUMP_DIR, exist_ok=True)
    cmd = [
        "mysqldump",
        f"--host={PA_DB_HOST}",
        f"--port={PA_DB_PORT}",
        "--single-transaction",
        "--quick",
        "--skip-lock-tables",
        "--no-tablespaces",
        "--set-gtid-purged=OFF",
        "--no-create-info",
        "--skip-triggers",
    ]
    if PA_DB_USER:
        cmd.append(f"--user={PA_DB_USER}")
    if PA_DB_PASS:
        # mysqldump は --password=... を要求する（-p と値の間に空白を入れられない）
        cmd.append(f"--password={PA_DB_PASS}")
    cmd += [PA_DB_NAME, table]

    with open(outfile, "wb") as f:
        subprocess.run(cmd, check=True, stdout=f, stderr=subprocess.PIPE, timeout=600)


@csrf_exempt
@require_POST
def dump_tables(request):
    if not _auth(request):
        return HttpResponse(status=401)

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(DUMP_DIR, exist_ok=True)

    # 依存元 → 依存先の順で “作成” しておくと扱いやすい（インポートは逆順）
    reg_path = os.path.join(DUMP_DIR, f"pa_musicregion_{ts}.sql")
    artist_path = os.path.join(DUMP_DIR, f"pa_artist_{ts}.sql")
    song_path = os.path.join(DUMP_DIR, f"pa_song_{ts}.sql")
    rating_path = os.path.join(DUMP_DIR, f"pa_rating_{ts}.sql")

    try:
        # すべて “データのみ” ダンプ
        _dump_one("songs_musicregion", reg_path)
        _dump_one("songs_artist", artist_path)
        _dump_one("songs_song", song_path)
        _dump_one("songs_rating", rating_path)
    except subprocess.CalledProcessError as e:
        return JsonResponse(
            {"ok": False, "stderr": e.stderr.decode("utf-8", "ignore")}, status=500
        )

    return JsonResponse(
        {"ok": True, "files": [reg_path, artist_path, song_path, rating_path]}
    )


@require_GET
def list_dumps(request):
    if not _auth(request):
        return HttpResponse(status=401)
    if not os.path.isdir(DUMP_DIR):
        return JsonResponse({"files": []})
    files = sorted([f for f in os.listdir(DUMP_DIR) if f.endswith(".sql")])
    return JsonResponse({"files": files})


@require_GET
def download_dump(request):
    if not _auth(request):
        return HttpResponse(status=401)
    name = request.GET.get("name")
    if not name or "/" in name or "\\" in name:
        return HttpResponseBadRequest("bad name")
    path = os.path.join(DUMP_DIR, name)
    if not os.path.isfile(path):
        raise Http404()
    resp = FileResponse(open(path, "rb"), content_type="application/sql")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
