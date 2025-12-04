import requests

url = "https://sugar191.pythonanywhere.com/api/ratings/score/update"
headers = {"Authorization": "Token 9f1e7c44f98d4f0e8e8b8a3b1c8f8b9f0e3d2c1a"}
data = {"song_id": 117, "score": 98}
r = requests.post(url, headers=headers, data=data)
print(r.status_code, r.text)
