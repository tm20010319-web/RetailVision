"""Quick test: set device to face recognition mode."""
import requests

url = "http://192.0.2.8/Face/1/FaceSnapshotMode"
auth = ("admin", "")
headers = {"Content-Type": "application/xml"}

# GET current mode
r = requests.get(url, auth=auth, timeout=5)
print(f"GET current mode: {r.status_code}")
print(r.text)

# PUT set to RecoMode
body = '<?xml version="1.0" encoding="UTF-8"?><FaceSnapshotMode><Mode>RecoMode</Mode></FaceSnapshotMode>'
r = requests.put(url, data=body.encode("utf-8"), auth=auth, headers=headers, timeout=5)
print(f"\nPUT RecoMode: {r.status_code}")
print(r.text)

# GET verify
r = requests.get(url, auth=auth, timeout=5)
print(f"\nGET after set: {r.status_code}")
print(r.text)

# Test face group query
r = requests.put("http://192.0.2.8/FaceGroups/QueryAll", auth=auth, headers=headers, timeout=5)
print(f"\nQueryAll face groups: {r.status_code}")
print(r.text[:500])
