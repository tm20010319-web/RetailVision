"""Apply recorded visual exclusions and create an attributed, unsplit staging set."""
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image

from collect_candidates import ROOT, plain, sheets


def main():
    candidates = json.loads((ROOT / "review/candidate_manifest.json").read_text(encoding="utf-8"))
    decisions = json.loads((ROOT / "review/visual_review.json").read_text(encoding="utf-8"))
    excluded = decisions["excluded"]
    assert set(excluded) <= {row["id"] for row in candidates}, "Unknown review decision ID"
    selected = []
    review_rows = []
    for row in candidates:
        row = dict(row)
        reason = excluded.get(row["id"])
        if row["status"] != "pending_visual_review":
            reason = row["status"]
        review_rows.append({"id": row["id"], "decision": "exclude" if reason else "include",
                            "reason": reason or "Photo reviewed: relevant distractor; no visible guitar or bee toy"})
        if reason:
            # Preserve a withdrawn delivery copy if the explicit review changed.
            for kind, suffix in (("images", ".jpg"), ("labels", ".txt")):
                stale = ROOT / kind / row["group"] / (row["id"] + suffix)
                if stale.exists():
                    archived = ROOT / "review" / "withdrawn" / (row["id"] + suffix)
                    archived.parent.mkdir(parents=True, exist_ok=True)
                    stale.replace(archived)
            continue
        row["attribution"] = plain(row["metadata_original"].get("Attribution", {}).get("value")) or row["author"]
        if row["license"] not in {"CC0", "Public domain"}:
            assert row["attribution"] and row["license_url"], f"Missing required credit: {row['id']}"
        image_path = ROOT / "images" / row["group"] / (row["id"] + ".jpg")
        label_path = ROOT / "labels" / row["group"] / (row["id"] + ".txt")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / row["path"], image_path)
        label_path.write_bytes(b"")
        row.update(path=image_path.relative_to(ROOT).as_posix(),
                   label_path=label_path.relative_to(ROOT).as_posix(),
                   status="visually_reviewed_negative", split="unassigned_train_only_candidate",
                   target_box_count=0, review_method=decisions["review_method"])
        selected.append(row)
    with (ROOT / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["id", "group", "category", "path", "label_path", "width", "height", "sha256",
                  "source_page", "title", "original_url", "download_url", "author", "attribution", "license", "license_url",
                  "modifications", "retrieved_date", "split", "target_box_count", "review_method"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)
    (ROOT / "manifest.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "review/review_decisions.json").write_text(json.dumps(review_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    sheets(selected, ROOT / "previews", prefix="selected")
    overview_ids = ["74167102", "126812946", "81431082", "70924688", "18898111", "73592440",
                    "82874222", "36622598", "91285902", "143885572", "17437804", "61825839",
                    "43937652", "1690485", "80305117", "18186637", "80954939", "178093323",
                    "109347467", "39228713", "27704330", "97185517", "159071858", "150444102"]
    by_id = {row["id"]: row for row in selected}
    sheets([by_id["neg_" + value] for value in overview_ids], ROOT, prefix="overview", columns=6, per_page=24)
    counts = dict(Counter(row["group"] for row in selected))
    summary = {
        "selected_images": len(selected), "groups": counts,
        "licenses": dict(Counter(row["license"] for row in selected)),
        "downloaded_candidates": sum(row["status"] == "pending_visual_review" for row in candidates),
        "excluded_after_visual_review": len(excluded),
        "failed_downloads": sum(row["status"] != "pending_visual_review" for row in candidates),
        "duplicates_sha256": len(selected) - len({row["sha256"] for row in selected}),
        "training_started": False, "live_configuration_changed": False,
        "contains_independent_camera_test_set": False,
    }
    # Check the actual delivered bytes, decoded dimensions, and empty labels.
    for row in selected:
        assert hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"]
        with Image.open(ROOT / row["path"]) as im:
            im.verify()
        assert (ROOT / row["label_path"]).read_bytes() == b""
    assert summary["duplicates_sha256"] == 0
    assert len(list((ROOT / "images").rglob("*.jpg"))) == len(selected)
    assert len(list((ROOT / "labels").rglob("*.txt"))) == len(selected)
    (ROOT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    credits = ["# Image attributions / 图片来源", "",
               "Individual images retain their original licenses. See each file's source and license link.",
               "Resized and JPEG converted; no semantic image edits. Original metadata is in manifest.json.", ""]
    for row in selected:
        credits.extend([f"## {row['id']}", "", f"- Image: `{row['path']}`",
                        f"- Title: {row['title']}", f"- Creator: {row['author'] or 'Not specified in Commons Artist metadata; see source page'}",
                        f"- Attribution credit: {row['attribution'] or 'See original source page (public-domain/CC0 file)'}",
                        f"- Original credit/source: {row['credit']}",
                        f"- Source: {row['source_page']}",
                        f"- License: {row['license']} {row['license_url']}",
                        f"- Changes: {row['modifications']}", ""])
    (ROOT / "ATTRIBUTIONS.md").write_text("\n".join(credits), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
