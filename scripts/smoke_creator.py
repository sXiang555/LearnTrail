#!/usr/bin/env python3
"""Creator-platform integration checks against an explicitly selected local gateway."""
import argparse
import base64
import getpass
import json
import os
import urllib.error
import urllib.request

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:10010")
    parser.add_argument("--username", required=True)
    parser.add_argument("--write-tests", action="store_true",
                        help="Test reversible saves/follows and ownership rejection; restore original state.")
    args = parser.parse_args()
    password = os.environ.get("LEARNTRAIL_SMOKE_PASSWORD") or getpass.getpass("Test account password: ")
    token = ""
    count = 0

    def request(path, method="GET", body=None, auth=True, expected=200):
        nonlocal count
        headers = {"Content-Type": "application/json"}
        if token and auth:
            headers["authorization"] = token
        req = urllib.request.Request(args.base_url.rstrip("/") + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = json.load(response)
            code = payload.get("code", response.status)
            if code != expected or response.status not in {200, expected}:
                raise RuntimeError(method + " " + path.split("?")[0] + ": unexpected response status " +
                                   str(response.status) + "/" + str(code))
        count += 1
        return payload.get("data")

    def check(condition, message):
        nonlocal count
        if not condition:
            raise RuntimeError(message)
        count += 1

    def check_playback(playback, minutes):
        if playback.get("url"):
            path = playback["url"].removeprefix("/api")
            ticket = path.rsplit("/", 1)[1].split(".")
            check(ticket[1] == str(minutes or 0), "Local playback ticket scope mismatch")
            req = urllib.request.Request(args.base_url.rstrip("/") + path, headers={"Range": "bytes=0-31"})
            with urllib.request.urlopen(req, timeout=20) as response:
                check(response.status == 206 and b"ftyp" in response.read(32), "Local video range request failed")
        else:
            encoded = playback["signature"].split(".")[1]
            policy = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            expected = minutes * 60 if minutes is not None else None
            check(policy["urlAccessInfo"].get("exper") == expected, "Cloud playback signature scope mismatch")

    login = {"type": 1, "password": password, "rememberMe": False}
    login["cellPhone" if args.username.isdigit() and len(args.username) == 11 else "username"] = args.username
    token = request("/as/accounts/login", "POST", login, auth=False)
    password = None
    prefix = "/cs/studio"
    home = request(prefix + "/home", auth=False)
    check(bool(home["series"]) and bool(home["creators"]), "Demo content missing")
    all_series = request(prefix + "/series")
    owned = request(prefix + "/library")
    check(all(item["owned"] for item in owned), "Library returned unowned content")
    for item in all_series:
        detail = request(prefix + "/series/" + item["slug"])
        check(len(detail["episodes"]) == detail["episodeCount"], "Episode count mismatch")
    request(prefix + "/library", auth=False, expected=401)
    request(prefix + "/workspace", auth=False, expected=401)
    request(prefix + "/series/101/watchlist", "POST", auth=False, expected=401)
    workspace = request(prefix + "/workspace")
    owned_ids = {item["id"] for item in workspace["series"]}
    other = next((item for item in all_series if item["id"] not in owned_ids), None)
    if args.write_tests:
        item = all_series[0]
        path = prefix + "/series/" + item["id"] + "/watchlist"
        originally_saved = item["saved"]
        try:
            request(path, "POST")
            request(path, "POST")
            saved = request(prefix + "/series?saved=true")
            check(sum(row["id"] == item["id"] for row in saved) == 1, "Duplicate watchlist entry")
        finally:
            request(path, "POST" if originally_saved else "DELETE")
        creator = next(c for c in home["creators"] if c["slug"] != workspace["creator"]["slug"])
        path = prefix + "/creators/" + creator["slug"]
        before = request(path)["creator"]
        try:
            request(path + "/follow", "POST")
            request(path + "/follow", "POST")
            after = request(path)["creator"]
            check(after["following"] and after["followerCount"] == before["followerCount"] +
                  (0 if before["following"] else 1), "Follow idempotency failed")
        finally:
            request(path + "/follow", "POST" if before["following"] else "DELETE")
        if other:
            payload = {key: other[key] for key in ("title", "subtitle", "description", "topic", "coverTone")}
            request(prefix + "/series/" + other["id"], "PUT", payload, expected=403)
        mine = next((item for item in workspace["series"] if item["status"] == "published"), None)
        if mine:
            payload = {key: mine[key] for key in ("title", "subtitle", "description", "topic", "coverTone")}
            request(prefix + "/series/" + mine["id"], "PUT", payload)
            check(request(prefix + "/series/" + mine["slug"])["title"] == mine["title"], "Own edit failed")
    locked = next((item for item in all_series if not item["owned"] and item["free"] != 1), None)
    if locked:
        detail = request(prefix + "/series/" + locked["slug"])
        preview = next((episode for episode in detail["episodes"] if episode["preview"]), None)
        if preview:
            playback = request("/ms/medias/signature/play?sectionId=" + preview["id"])
            check_playback(playback, min(3, detail["previewMinutes"]))
        inaccessible = next((episode for episode in detail["episodes"] if not episode["preview"]), None)
        if inaccessible:
            request("/ms/medias/signature/play?sectionId=" + inaccessible["id"], expected=403)
    if owned:
        detail = request(prefix + "/series/" + owned[0]["slug"])
        playback = request("/ms/medias/signature/play?sectionId=" + detail["episodes"][0]["id"])
        check_playback(playback, None)
        record = request("/ls/learning-records/course/" + detail["courseId"])
        check(record["id"] == detail["lessonId"], "Learning entitlement mismatch")
    print("PASS: " + str(count) + " creator-platform requests/assertions; credentials and signatures omitted.")

if __name__ == "__main__":
    main()
