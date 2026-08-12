"""
本番APIの疎通確認用スクリプト（手動実行、Djangoのテストではない）。

以前はトークンをソースに直書きしていたため、環境変数から読むように変更した。

使い方（PowerShell）:
    $env:PA_AUTH_TOKEN = "<トークン>"
    python music/test.py
"""

import os
import sys

import requests

BASE_URL = os.environ.get("PA_BASE_URL", "https://sugar191.pythonanywhere.com")
TOKEN = os.environ.get("PA_AUTH_TOKEN")

if not TOKEN:
    sys.exit("環境変数 PA_AUTH_TOKEN を設定してください")

url = f"{BASE_URL}/api/ratings/score/update"
headers = {"Authorization": f"Token {TOKEN}"}
data = {"song_id": 117, "score": 98}

r = requests.post(url, headers=headers, data=data, timeout=30)
print(r.status_code, r.text)
