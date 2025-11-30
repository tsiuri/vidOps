import requests

headers = {"X-Worker-Token": "zz"}
resp = requests.get(
    "http://127.0.0.1:8443/v1/assets/download",
    headers=headers,
    params={"relative_path": "raw/nonexistent"},
    timeout=5,
)
print(resp.status_code, resp.text)
