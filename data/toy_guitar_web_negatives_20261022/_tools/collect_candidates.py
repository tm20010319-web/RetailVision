"""Collect attributed Commons photographs for a separate negative-sample review.

This utility never reads or changes the existing training splits or live model.
Images remain candidates until the explicit visual-review selection is applied.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import html
import io
import json
import random
import re
import time
from collections import Counter
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "RetailVisionNegativeSamples/1.0 (Commons dataset curation)"}
CATEGORIES = [
    ("white_clothing", "White shirts", 40),
    ("white_clothing", "White T-shirts", 50),
    ("white_clothing", "White jackets", 20),
    ("white_clothing", "White coats", 10),
    ("yellow_objects", "Yellow bags", 25),
    ("yellow_objects", "Yellow chairs", 25),
    ("yellow_objects", "Yellow cups", 18),
    ("yellow_objects", "Yellow towels", 12),
    ("yellow_objects", "Yellow boxes", 10),
    ("white_objects", "White bags", 15),
    ("white_objects", "White bottles", 15),
    ("white_objects", "White chairs", 10),
]
EXCLUDE = re.compile(
    r"\b(guitars?|bees?|toys?|teddy|plush|dolls?|statues?|drawings?|paintings?|"
    r"illustrations?|cartoons?|logos?|flags?|icons?|svg|diagrams?|sketch|"
    r"posters?|uniform[ _]kit|coat[ _]of[ _]arms)\b", re.I
)


def api(params):
    for attempt in range(4):
        try:
            response = requests.get(
                API, params={"format": "json", "formatversion": 2, "maxlag": 5, **params},
                headers=HEADERS, timeout=35,
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data
        except (requests.RequestException, RuntimeError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def plain(value):
    return html.unescape(re.sub(r"<[^>]*>", "", value or "")).strip()


def permitted(meta):
    # Preserve the actual license URL and terms for every included file.
    name = meta.get("LicenseShortName", {}).get("value", "")
    return name in {"CC0", "Public domain"} or bool(re.fullmatch(r"CC BY(?:-SA)? [1-4]\.0", name))


def fingerprint(im):
    im = im.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
    pixels = list(im.getdata())
    result = 0
    for y in range(16):
        for x in range(16):
            result = (result << 1) | int(pixels[y * 17 + x] > pixels[y * 17 + x + 1])
    return f"{result:064x}"


def download(record):
    destination = ROOT / "review" / "candidates" / (record["id"] + ".jpg")
    try:
        if not destination.exists():
            response = requests.get(record["download_url"], headers=HEADERS, timeout=45)
            response.raise_for_status()
            if len(response.content) > 15_000_000:
                raise ValueError("Image exceeds the 15 MB download limit")
            with Image.open(io.BytesIO(response.content)) as original:
                original.load()
                im = ImageOps.exif_transpose(original).convert("RGB")
            im.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            if min(im.size) < 256 or max(im.size) < 600:
                raise ValueError(f"Image too small for this collection: {im.size}")
            im.save(destination, quality=95, optimize=True)
        with Image.open(destination) as im:
            im.load()
            record.update(width=im.width, height=im.height, dhash=fingerprint(im))
        record.update(
            path=destination.relative_to(ROOT).as_posix(),
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
            status="pending_visual_review",
        )
    except Exception as exc:
        record.update(status="download_failed", error=f"{type(exc).__name__}: {exc}")
    return record


def sheets(records, destination, prefix="candidates", columns=5, per_page=30):
    destination.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    cell_w, cell_h = 280, 235
    for offset in range(0, len(records), per_page):
        chunk = records[offset:offset + per_page]
        rows = (len(chunk) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#eeeeee")
        draw = ImageDraw.Draw(sheet)
        for index, record in enumerate(chunk):
            x, y = (index % columns) * cell_w, (index // columns) * cell_h
            with Image.open(ROOT / record["path"]) as source:
                preview = ImageOps.contain(source, (cell_w - 10, cell_h - 50))
                sheet.paste(preview, (x + (cell_w - preview.width) // 2, y + 5))
            draw.text((x + 5, y + cell_h - 43), record["id"], fill="black", font=font)
            draw.text((x + 5, y + cell_h - 23), record["category"][:29], fill="black", font=font)
        sheet.save(destination / f"{prefix}_{offset // per_page + 1:02d}.jpg", quality=92)


def main():
    (ROOT / "review" / "candidates").mkdir(parents=True, exist_ok=True)
    metadata_file = ROOT / "review" / "candidate_metadata.json"
    if metadata_file.exists():
        records = json.loads(metadata_file.read_text(encoding="utf-8"))
        print(f"Resuming {len(records)} metadata entries", flush=True)
    else:
        chosen = {}
        exclusions = []
        for group, category, quota in CATEGORIES:
            members = api({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + category,
                           "cmtype": "file", "cmlimit": 500}).get("query", {}).get("categorymembers", [])
            members = [m for m in members if re.search(r"\.(jpe?g|png|webp)$", m["title"], re.I)
                       and not EXCLUDE.search(m["title"]) and m["pageid"] not in chosen]
            random.Random("20261022/" + category).shuffle(members)
            accepted = 0
            for start in range(0, len(members), 40):
                batch = members[start:start + 40]
                pages = api({"action": "query", "pageids": "|".join(str(m["pageid"]) for m in batch),
                             "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 960})["query"]["pages"]
                by_id = {p["pageid"]: p for p in pages}
                for member in batch:
                    page = by_id[member["pageid"]]
                    infos = page.get("imageinfo", [])
                    if not infos:
                        continue
                    info = infos[0]
                    meta = info.get("extmetadata", {})
                    if not permitted(meta) or min(info["width"], info["height"]) < 300 or max(info["width"], info["height"]) < 600:
                        exclusions.append({"title": page["title"], "reason": "license_or_dimensions"})
                        continue
                    chosen[page["pageid"]] = {
                        "id": f"neg_{page['pageid']}", "pageid": page["pageid"], "group": group,
                        "category": category, "title": page["title"], "source_page": info["descriptionurl"],
                        "original_url": info["url"], "download_url": info.get("thumburl", info["url"]),
                        "original_width": info["width"], "original_height": info["height"],
                        "license": plain(meta.get("LicenseShortName", {}).get("value")),
                        "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                        "author": plain(meta.get("Artist", {}).get("value")),
                        "credit": plain(meta.get("Credit", {}).get("value")),
                        "description": plain(meta.get("ImageDescription", {}).get("value")),
                        "metadata_original": meta, "retrieved_date": "2026-10-22",
                        "modifications": "EXIF orientation applied; resized to <=1280px; converted to JPEG quality 95; uncropped",
                    }
                    accepted += 1
                    if accepted >= quota:
                        break
                if accepted >= quota:
                    break
            print(f"Metadata {category}: {accepted}", flush=True)
        records = list(chosen.values())
        metadata_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "review" / "metadata_exclusions.json").write_text(json.dumps(exclusions, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for record in pool.map(download, records):
            results.append(record)
            if len(results) % 20 == 0:
                print(f"Downloaded {len(results)}/{len(records)}; statuses {dict(Counter(r['status'] for r in results))}", flush=True)
    (ROOT / "review" / "candidate_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    good = [r for r in results if r["status"] == "pending_visual_review"]
    # Flag near duplicates for review, but never automatically accept a candidate.
    duplicates = []
    for index, current in enumerate(good):
        for earlier in good[:index]:
            distance = (int(current["dhash"], 16) ^ int(earlier["dhash"], 16)).bit_count()
            if current["sha256"] == earlier["sha256"] or distance <= 14:
                duplicates.append({"a": earlier["id"], "b": current["id"], "dhash_distance": distance})
    (ROOT / "review" / "near_duplicates.json").write_text(json.dumps(duplicates, indent=2), encoding="utf-8")
    sheets(good, ROOT / "review" / "sheets")
    print(json.dumps({"downloaded": len(good), "groups": dict(Counter(r["group"] for r in good)),
                      "near_duplicate_pairs": len(duplicates), "failed": len(results) - len(good)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
