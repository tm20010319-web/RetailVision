"""Create YOLO labels from user-confirmed VLX seeds and SAM3.1 tracking.

Stages run in separate environments so VLX/SAM GPU lifetimes never overlap:
prepare extracts source-PTS samples, track follows one fixed instance, and
finalize builds the reviewed annotation package used by the trainer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import yaml


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def frame_times(video: Path) -> list[float]:
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
               "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(video)]
    payload = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    values = [float(row["best_effort_timestamp_time"]) for row in payload["frames"]]
    if not values or any(not math.isfinite(v) for v in values):
        raise ValueError("video has no finite presentation timestamps")
    if any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError("presentation timestamps must be strictly increasing")
    return values


def mask_geometry(mask):
    mask = np.asarray(mask).squeeze().astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, width, height, area = (int(v) for v in stats[index])
    if area < 16:
        return None
    h, w = mask.shape
    return {"bbox": [x/w, y/h, (x+width)/w, (y+height)/h], "area": area,
            "centroid": [float(centers[index, 0])/w, float(centers[index, 1])/h]}


def iou(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return area/max(1e-9, (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area)


def select_candidate(candidates, seed, threshold):
    ranked = sorted(((iou(value["bbox"], seed), oid, value) for oid, value in candidates.items()), reverse=True)
    if not ranked or ranked[0][0] < threshold:
        return None, {"seed": seed, "candidates": candidates, "ranked_iou": [r[0] for r in ranked]}
    if len(ranked) > 1 and ranked[0][0]-ranked[1][0] < .10:
        return None, {"seed": seed, "candidates": candidates, "ranked_iou": [r[0] for r in ranked], "ambiguous": True}
    return ranked[0][1], {"seed": seed, "candidates": candidates, "selected_iou": ranked[0][0]}


def prepare(config):
    video = Path(config["source_video"]).resolve(); output = Path(config["output"]).resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.mkdir(parents=True); (output/"frames_original").mkdir(); (output/"frames_sam").mkdir()
    times = frame_times(video); interval = 1/float(config["sam_fps"])
    selected=[]; next_time=0.0
    for index,timestamp in enumerate(times):
        if timestamp+1e-9 >= next_time:
            selected.append((index,timestamp)); next_time += interval
    lookup=dict(selected); rows=[]; capture=cv2.VideoCapture(str(video))
    try:
        for index in range(len(times)):
            ok,frame=capture.read()
            if not ok:raise RuntimeError(f"decode ended before frame {index}")
            if index not in lookup:continue
            sample=len(rows); original=output/"frames_original"/f"{sample:06d}.jpg"; sam=output/"frames_sam"/f"{sample:06d}.jpg"
            if not cv2.imwrite(str(original),frame,[cv2.IMWRITE_JPEG_QUALITY,95]):raise RuntimeError("cannot save source sample")
            resized=cv2.resize(frame,(1280,720),interpolation=cv2.INTER_CUBIC)
            if not cv2.imwrite(str(sam),resized,[cv2.IMWRITE_JPEG_QUALITY,95]):raise RuntimeError("cannot save SAM sample")
            rows.append({"sample":sample,"source_frame_index":index,"source_pts_seconds":lookup[index],
                         "width":frame.shape[1],"height":frame.shape[0],"image_sha256":sha256(original)})
    finally:capture.release()
    targets=[]
    for target in config["targets"]:
        x1,y1,x2,y2=target["vlx_seed_bbox_xyxy"]
        targets.append(dict(target,seed_normalized=[x1/rows[0]["width"],y1/rows[0]["height"],x2/rows[0]["width"],y2/rows[0]["height"]]))
    write_json(output/"input.json",{"source_video":str(video),"source_sha256":sha256(video),"rows":rows,"targets":targets,
               "sam_fps":config["sam_fps"],"vlx_evidence":config["vlx_evidence"]})
    write_json(output/"status.json",{"status":"prepared","samples":len(rows)})
    print(json.dumps({"status":"prepared","samples":len(rows),"output":str(output)},indent=2))


def track(config, class_id):
    output=Path(config["output"]).resolve(); data=read(output/"input.json"); rows=data["rows"]
    stored_target=next(t for t in data["targets"] if t["class_id"]==class_id)
    configured_target=next(t for t in config["targets"] if t["class_id"]==class_id)
    # Prompts/mode may be refined after preparation without changing the
    # immutable VLX seed or extracted source frames.
    target={**stored_target,**configured_target,"seed_normalized":stored_target["seed_normalized"]}
    repository=Path(config["sam_repository"]); checkpoint=Path(config["sam_checkpoint"])
    os.environ["HF_HUB_OFFLINE"]="1";sys.path.insert(0,str(repository))
    import torch
    from sam3.model_builder import build_sam3_predictor
    # The multiplex checkpoint was trained with 16 object slots even when a
    # run tracks one fixed instance; keep its exact tensor geometry.
    predictor=build_sam3_predictor(checkpoint_path=str(checkpoint),version="sam3.1",max_num_objects=16,
        multiplex_count=16,use_fa3=False,use_rope_real=False,compile=False,warm_up=False,async_loading_frames=False)
    model=predictor.model;model.batched_grounding_batch_size=model.postprocess_batch_size=1
    core=model.tracker.model;core.forward_backbone_per_frame_for_eval=True;core.offload_output_to_cpu_for_eval=True
    chunk=int(config["chunk_size"]);overlap=int(config["chunk_overlap"]);step=chunk-overlap;results={};attempts=[]
    def array(value):return value.detach().cpu().numpy() if hasattr(value,"detach") else np.asarray(value)
    with torch.inference_mode(),torch.autocast(device_type="cuda",dtype=torch.bfloat16):
        for start in range(0,len(rows),step):
            end=min(start+chunk,len(rows)); frame_dir=output/f"track_{class_id}"/f"chunk_{start:06d}";frame_dir.mkdir(parents=True)
            for index in range(start,end):os.link(output/"frames_sam"/f"{index:06d}.jpg",frame_dir/f"{index-start:06d}.jpg")
            model.new_det_thresh=float(config.get("sam_detection_threshold", .30))
            sid=predictor.handle_request({"type":"start_session","resource_path":str(frame_dir),"offload_video_to_cpu":True})["session_id"]
            try:
                initialized=None
                for local_start in range(1 if start==0 else min(overlap,end-start)):
                    index=start+local_start;seed=target["seed_normalized"] if index==0 else (results.get(index) or {}).get("bbox")
                    if seed is None:continue
                    predictor.handle_request({"type":"reset_session","session_id":sid})
                    request={"type":"add_prompt","session_id":sid,"frame_index":local_start}
                    if target.get("initialization_mode")=="box":
                        x1,y1,x2,y2=seed
                        request.update(bounding_boxes=[[x1,y1,x2-x1,y2-y1]],bounding_box_labels=[1])
                    else:
                        request["text"]=target["sam_prompt"]
                    initial=predictor.handle_request(request)["outputs"]
                    candidates={int(oid):geometry for oid,mask in zip(array(initial["out_obj_ids"]),array(initial["out_binary_masks"]))
                                if (geometry:=mask_geometry(mask)) is not None}
                    oid,diagnostic=select_candidate(candidates,seed,float(config["initialization_iou"]))
                    attempts.append({"chunk_start":start,"sample":index,"source_pts_seconds":rows[index]["source_pts_seconds"],**diagnostic})
                    if oid is not None:initialized=(local_start,oid);break
                if initialized is None:
                    write_json(output/f"track_{class_id}_initialization_failure_{start:06d}.json",{"target":target,"attempts":attempts})
                    raise RuntimeError(f"cannot initialize class {class_id} at chunk {start}")
                local_start,oid=initialized;model.new_det_thresh=2.0
                for response in predictor.handle_stream_request({"type":"propagate_in_video","session_id":sid,
                        "propagation_direction":"forward","start_frame_index":local_start}):
                    index=start+int(response["frame_index"]);raw=response["outputs"]
                    found=None
                    for result_id,mask in zip(array(raw["out_obj_ids"]),array(raw["out_binary_masks"])):
                        if int(result_id)==oid:found=mask_geometry(mask);break
                    if index not in results:results[index]=found
                    if index%100==0:print(json.dumps({"target":target["key"],"sample":index,"samples":len(rows)}),flush=True)
            finally:predictor.handle_request({"type":"close_session","session_id":sid})
            write_json(output/f"geometry_{class_id}.json",{"target":target,"completed_through":end-1,
                       "rows":[results.get(i) for i in range(end)],"initialization_attempts":attempts})
            if end==len(rows):break
    write_json(output/f"track_{class_id}_status.json",{"status":"completed","samples":len(rows),"observed":sum(v is not None for v in results.values())})


def csrt(config):
    output=Path(config["output"]).resolve();data=read(output/"input.json");rows=data["rows"];video=Path(data["source_video"])
    by_source={row["source_frame_index"]:row["sample"] for row in rows};targets=data["targets"]
    start=float(config["tracker_start_seconds"]);visible_until=float(config["objects_visible_until_seconds"])
    capture=cv2.VideoCapture(str(video));trackers=None;results={t["class_id"]:[None]*len(rows) for t in targets}
    try:
        index=0
        while True:
            ok,frame=capture.read()
            if not ok:break
            timestamp=capture.get(cv2.CAP_PROP_POS_MSEC)/1000.0
            if trackers is None and timestamp+1e-6>=start:
                trackers={}
                for target in targets:
                    x1,y1,x2,y2=[float(v) for v in target["vlx_seed_bbox_xyxy"]]
                    tracker=cv2.TrackerCSRT_create();tracker.init(frame,tuple(int(round(v)) for v in (x1,y1,x2-x1,y2-y1)));trackers[target["class_id"]]=tracker
            boxes={}
            if trackers is not None and timestamp<=visible_until+0.25:
                h,w=frame.shape[:2]
                for class_id,tracker in trackers.items():
                    success,box=tracker.update(frame)
                    if success:
                        x,y,bw,bh=box;x1=max(0.0,x);y1=max(0.0,y);x2=min(float(w),x+bw);y2=min(float(h),y+bh)
                        if x2>x1 and y2>y1:boxes[class_id]={"bbox":[x1/w,y1/h,x2/w,y2/h],"source":"csrt_full_source_fps"}
            if index in by_source:
                sample=by_source[index];row=rows[sample];t=row["source_pts_seconds"]
                for target in targets:
                    class_id=target["class_id"]
                    if t<start:results[class_id][sample]={"bbox":target["seed_normalized"],"source":"vlx_static_seed"}
                    elif t<=visible_until:results[class_id][sample]=boxes.get(class_id)
                    else:results[class_id][sample]=None
            index+=1
    finally:capture.release()
    write_json(output/"geometry_csrt.json",{"tracker_start_seconds":start,"objects_visible_until_seconds":visible_until,
               "rows":{str(k):v for k,v in results.items()}})
    write_json(output/"csrt_status.json",{"status":"completed","samples":len(rows),
               "observed":{str(k):sum(v is not None for v in values) for k,values in results.items()}})
    print(json.dumps(read(output/"csrt_status.json"),indent=2))


def render_sheet(rows,image_dir,output,targets):
    columns,tw,th,caption=4,400,224,28;canvas=Image.new("RGB",(columns*tw,math.ceil(len(rows)/columns)*(th+caption)),"#111827")
    font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",16);colors=("#ffe45c","#44e3ff")
    for i,row in enumerate(rows):
        image=Image.open(image_dir/row["image"]).convert("RGB");draw=ImageDraw.Draw(image)
        for item in row["annotations"]:
            box=[item["bbox_normalized"][j]*(image.width if j%2==0 else image.height) for j in range(4)];color=colors[item["class_id"]]
            draw.rectangle(box,outline=color,width=3);draw.text((box[0],max(0,box[1]-22)),targets[item["class_id"]]["label_zh"],font=font,fill=color,stroke_width=2,stroke_fill="black")
        image.thumbnail((tw,th),Image.Resampling.LANCZOS);x,y=(i%columns)*tw,(i//columns)*(th+caption);canvas.paste(image,(x,y))
        ImageDraw.Draw(canvas).text((x+4,y+th+3),f'{row["source_pts_seconds"]:.3f}s',font=font,fill="white")
    canvas.save(output,quality=92)


def finalize(config):
    output=Path(config["output"]).resolve();data=read(output/"input.json");rows=data["rows"]
    targets=[]
    for stored in data["targets"]:
        configured=next(t for t in config["targets"] if t["class_id"]==stored["class_id"])
        targets.append({**stored,**configured,"seed_normalized":stored["seed_normalized"]})
    csrt_rows=read(output/"geometry_csrt.json")["rows"]
    geometries={};sources={}
    for target in targets:
        class_id=target["class_id"];sam_path=output/f"geometry_{class_id}.json"
        geometries[class_id]=read(sam_path)["rows"] if sam_path.exists() else [None]*len(rows)
        sources[class_id]=csrt_rows[str(class_id)]
    manual_overrides={str(key):value for key,value in config.get("manual_overrides",{}).items()}
    matched_manual_overrides=set()
    package=Path(config["annotation_output"]).resolve()
    if package.exists():raise ValueError(f"annotation output already exists: {package}")
    image_dir,label_dir,sheet_dir=package/"images",package/"labels",package/"review_sheets"
    image_dir.mkdir(parents=True);label_dir.mkdir();sheet_dir.mkdir();selected=[];next_sparse=0.0
    for index,row in enumerate(rows):
        timestamp=row["source_pts_seconds"];dense=timestamp>=float(config["dense_after_seconds"])
        if not dense and timestamp+1e-9<next_sparse:continue
        if not dense:next_sparse+=float(config["sparse_interval_seconds"])
        annotations=[]
        for target in targets:
            class_id=target["class_id"]
            if timestamp>float(config["objects_visible_until_seconds"]):geometry=None;source=None
            elif target.get("prefer_sam",False) and geometries[class_id][index] is not None:
                geometry=geometries[class_id][index];source="sam31_mask"
            else:geometry=sources[class_id][index];source=geometry.get("source") if geometry else None
            timestamp_key=f"{timestamp:.2f}"
            if timestamp_key in manual_overrides and str(class_id) in manual_overrides[timestamp_key]:
                matched_manual_overrides.add(timestamp_key)
                box=manual_overrides[timestamp_key][str(class_id)]
                if box is None:
                    geometry=None;source=None
                else:
                    if len(box)!=4 or not all(isinstance(value,(int,float)) for value in box):
                        raise ValueError(f"invalid manual override for {timestamp_key} class {class_id}: {box}")
                    x1,y1,x2,y2=(float(value) for value in box)
                    width,height=float(row["width"]),float(row["height"])
                    if not (0<=x1<x2<=width and 0<=y1<y2<=height):
                        raise ValueError(f"out-of-bounds manual override for {timestamp_key} class {class_id}: {box}")
                    geometry={"bbox":[x1/width,y1/height,x2/width,y2/height]};source="manual_review"
            if geometry is not None:annotations.append({"class_id":class_id,"key":target["key"],
                                                        "bbox_normalized":geometry["bbox"],"geometry_source":source,
                                                        **({"mask_area":geometry["area"]} if "area" in geometry else {})})
        if config.get("require_all_targets",True) and len(annotations)!=len(targets):continue
        name=f"frame_{len(selected)+1:04d}.jpg";shutil.copy2(output/"frames_original"/f"{index:06d}.jpg",image_dir/name)
        label=label_dir/Path(name).with_suffix(".txt")
        label.write_text("".join(f'{a["class_id"]} {(a["bbox_normalized"][0]+a["bbox_normalized"][2])/2:.8f} {(a["bbox_normalized"][1]+a["bbox_normalized"][3])/2:.8f} {a["bbox_normalized"][2]-a["bbox_normalized"][0]:.8f} {a["bbox_normalized"][3]-a["bbox_normalized"][1]:.8f}\n' for a in annotations),encoding="utf-8")
        selected.append({"image":name,"source_pts_seconds":timestamp,"source_frame_index":row["source_frame_index"],
                         "image_sha256":sha256(image_dir/name),"label_sha256":sha256(label),"annotations":annotations})
    unmatched=set(manual_overrides)-matched_manual_overrides
    if unmatched:
        raise ValueError(f"manual override timestamps did not match selected frames: {sorted(unmatched)}")
    page=24
    for start in range(0,len(selected),page):render_sheet(selected[start:start+page],image_dir,sheet_dir/f"review_{start//page+1:02d}.jpg",targets)
    manifest={"source_video":data["source_video"],"source_sha256":data["source_sha256"],"created_at_utc":datetime.now(timezone.utc).isoformat(),
              "annotation_method":"user_confirmed_vlx_seed_with_sam31_csrt_and_manual_action_review","vlx_evidence":data["vlx_evidence"],
              "classes":{str(t["class_id"]):t["key"] for t in targets},"class_labels_zh":{str(t["class_id"]):t["label_zh"] for t in targets},
              "sam_fps":data["sam_fps"],"sparse_interval_seconds":config["sparse_interval_seconds"],"dense_after_seconds":config["dense_after_seconds"],"frames":selected}
    write_json(package/"annotation_manifest.json",manifest)
    patterns={}
    for row in selected:
        key=",".join(str(a["class_id"]) for a in row["annotations"]);patterns[key]=patterns.get(key,0)+1
    write_json(package/"validation_report.json",{"valid":True,"sampled_frames":len(selected),"total_boxes":sum(len(r["annotations"]) for r in selected),
        "review_sheets":math.ceil(len(selected)/page),"source_tracking_samples":len(rows),
        "source_samples_not_selected_by_sparse_dense_policy":len(rows)-len(selected),"class_presence_patterns":patterns,
        "limitations":["One camera and one recording; relocation/general-instance accuracy is not qualified.",
                       "Uncertain tracker frames are not forced into a positive label; post-removal frames are retained as explicit negatives."]})
    print(json.dumps({"annotation_output":str(package),"frames":len(selected),"boxes":sum(len(r["annotations"]) for r in selected)},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("stage",choices=["prepare","track","csrt","finalize"])
    parser.add_argument("--config",type=Path,required=True);parser.add_argument("--class-id",type=int)
    args=parser.parse_args();config=yaml.safe_load(args.config.read_text(encoding="utf-8"));cv2.setNumThreads(2)
    if args.stage=="prepare":prepare(config)
    elif args.stage=="track":
        if args.class_id is None:parser.error("track requires --class-id")
        track(config,args.class_id)
    elif args.stage=="csrt":csrt(config)
    else:finalize(config)


if __name__=="__main__":main()
