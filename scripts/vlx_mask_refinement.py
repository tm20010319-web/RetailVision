"""Offline VLX-labelled instance refinement using installed SAM3.1 tracking.

Stages deliberately run in separate Python environments/GPU lifetimes. Existing
VLX/pose observations are immutable inputs, not represented as fresh inference.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from agent.vlx_interaction import InteractionAnalyzer, InteractionConfig, ObjectEvidence, PersonEvidence, TARGETS, INTERACTION_POLICY
from agent.vlx_person_tracking import AnonymousUsers, PersonDetection
from scripts.run_vlx_interaction_video import Overlay, PreviewWriter, write_json
from scripts.vlx_grounded_instance import GroundedInstanceSelector


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def mask_geometry(mask):
    """Ignore disconnected segmentation specks; never infer invisible extent."""
    mask = np.asarray(mask).squeeze().astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, width, height, area = (int(v) for v in stats[index])
    if area < 16:
        return None
    h, w = mask.shape
    return {"bbox": [x/w, y/h, (x+width)/w, (y+height)/h],
            "area": area, "centroid": [float(centers[index, 0])/w, float(centers[index, 1])/h]}


def overlap(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return area/max(1e-9, (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area)


def match_instances(candidates, seeds):
    costs = {(oid, key): overlap(g["bbox"], box) for oid, g in candidates.items() for key, box in seeds.items()}
    result = {}
    for (oid, key), score in costs.items():
        rivals = [s for (i, k), s in costs.items() if (i == oid and k != key) or (k == key and i != oid)]
        if score >= .35 and all(score-s >= .15 for s in rivals):
            result[oid] = key
    return result


def prepare(recorded, output, classes):
    from scripts.vlx_interaction_vision import clothing_signature
    original = read(recorded/"manifest.json")
    source = Path(original["source_video"])
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != original["source_sha256"]:
        raise ValueError("source hash differs from recorded model evidence")
    trace = recorded/"observations.jsonl"
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    output.mkdir(parents=True, exist_ok=False)
    (output/"frames").mkdir()
    lookup = {r["source_frame"]: (i, r) for i, r in enumerate(rows)}
    users = AnonymousUsers(); prepared = []
    capture = cv2.VideoCapture(str(source))
    try:
        for seq in range(1, max(lookup)+1):
            if not capture.grab():
                raise RuntimeError(f"source ended before frame {seq}")
            if seq not in lookup:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                raise RuntimeError(f"source frame {seq} cannot be decoded")
            index, row = lookup[seq]; h, w = frame.shape[:2]
            detections = [PersonDetection(tuple(p["bbox"]), tuple(p["wrists"]), clothing_signature(frame, p["bbox"])) for p in row["people"]]
            people = users.update(row["timestamp_seconds"], detections)
            recognition = row["diagnostics"].get("recognition")
            seeds = {TARGETS[d["class_id"]].key: [v/s for v, s in zip(d["bbox_xyxy"], (w,h,w,h))]
                     for d in (recognition or {}).get("selected", []) if d["class_id"] in classes}
            prepared.append({"source_frame": seq, "timestamp_seconds": row["timestamp_seconds"],
                             "people": [asdict(p) for p in people], "vlx_seeds": seeds})
            resized = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA if w>1280 else cv2.INTER_CUBIC)
            if not cv2.imwrite(str(output/"frames"/f"{index:06d}.jpg"), resized, [cv2.IMWRITE_JPEG_QUALITY,95]):
                raise RuntimeError("cannot save sampled source frame")
    finally:
        capture.release()
    write_json(output/"input.json", {"original_manifest": original, "recorded_run": str(recorded.resolve()),
               "recorded_trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
               "classes": classes, "rows": prepared, "users": users.report()})
    write_json(output/"status.json", {"status":"prepared", "samples":len(prepared)})


def track(output, checkpoint, repository, prompt, classes, chunk_size=150):
    if isinstance(chunk_size,bool) or not isinstance(chunk_size,int) or chunk_size<2:
        raise ValueError("chunk_size must be an integer >= 2")
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.path.insert(0,str(repository))
    import torch
    from sam3.model_builder import build_sam3_predictor
    data = read(output/"input.json"); rows = data["rows"]
    keys = {TARGETS[i].key for i in classes}
    group = output/("masks_"+"_".join(map(str,classes)))
    group.mkdir(exist_ok=False)
    predictor = build_sam3_predictor(checkpoint_path=str(checkpoint),version="sam3.1",max_num_objects=16,
        multiplex_count=16,use_fa3=False,use_rope_real=False,compile=False,warm_up=False,async_loading_frames=False)
    model = predictor.model
    model.batched_grounding_batch_size = model.postprocess_batch_size = 1
    core = model.tracker.model
    core.forward_backbone_per_frame_for_eval=True
    core.offload_output_to_cpu_for_eval=True
    core.trim_past_non_cond_mem_for_eval=False
    def array(v):
        return v.detach().cpu().numpy() if hasattr(v,"detach") else np.asarray(v)
    results = {}; overlap_frames = min(50,chunk_size//3)
    with torch.inference_mode(), torch.autocast(device_type="cuda",dtype=torch.bfloat16):
        for start in range(0,len(rows),chunk_size-overlap_frames):
            end = min(start+chunk_size,len(rows))
            frame_dir = group/f"chunk_{start:06d}"; frame_dir.mkdir()
            for index in range(start,end):
                os.link(output/"frames"/f"{index:06d}.jpg",frame_dir/f"{index-start:06d}.jpg")
            model.new_det_thresh=.65
            sid = predictor.handle_request({"type":"start_session","resource_path":str(frame_dir),"offload_video_to_cpu":True})["session_id"]
            try:
                attempts=[]
                # A target can be missed on frame zero but detected moments
                # later. Allow two media seconds to establish all instances;
                # leave earlier rows empty rather than backfilling evidence.
                # Later windows must still initialize inside their overlap.
                offsets = (range(min(overlap_frames,end-start)) if start else
                    (i for i in range(end-start)
                     if rows[i]["timestamp_seconds"]-rows[0]["timestamp_seconds"] <= 2.0))
                for local_start in offsets:
                    index=start+local_start
                    seeds={k:v for k,v in rows[index]["vlx_seeds"].items() if k in keys}
                    # Restart only at a frame covered by the preceding window;
                    # a mid-throw blur at the nominal boundary need not erase
                    # an otherwise continuous instance for the next window.
                    for key,geometry in results.get(index,{}).items():seeds[key]=geometry["bbox"]
                    if set(seeds)!=keys:continue
                    predictor.handle_request({"type":"reset_session","session_id":sid})
                    initial=predictor.handle_request({"type":"add_prompt","session_id":sid,"frame_index":local_start,"text":prompt})["outputs"]
                    candidates={int(oid):g for oid,mask in zip(array(initial["out_obj_ids"]),array(initial["out_binary_masks"])) if (g:=mask_geometry(mask)) is not None}
                    mapping=match_instances(candidates,seeds)
                    attempts.append({"source_frame":rows[index]["source_frame"],"seeds":seeds,"candidates":candidates,"mapping":mapping})
                    if set(mapping.values())==keys:break
                else:
                    write_json(group/f"initialization_{start:06d}.json",{"attempts":attempts,"status":"failed"})
                    raise RuntimeError(f"no unambiguous overlapping initialization for {keys} at window {start}")
                write_json(group/f"initialization_{start:06d}.json",{"attempts":attempts,"source_frame":rows[index]["source_frame"],"mapping":mapping})
                # Fixed instances: later detections may recondition existing tracks,
                # but unrelated scene objects must not grow the GPU tracking state.
                model.new_det_thresh=2.0
                for response in predictor.handle_stream_request({"type":"propagate_in_video","session_id":sid,
                        "propagation_direction":"forward","start_frame_index":local_start}):
                    index=start+int(response["frame_index"]); raw=response["outputs"]; found={}
                    for oid,mask in zip(array(raw["out_obj_ids"]),array(raw["out_binary_masks"])):
                        key=mapping.get(int(oid)); geometry=mask_geometry(mask)
                        if key is not None and geometry is not None:
                            found[key]=dict(geometry,mask_id=int(oid),chunk_start=start)
                    if index not in results:
                        results[index]=found
                    # Keep archival full-resolution masks on CPU. The tracker
                    # retains its own memory; do not change its storage device.
                    state=predictor._get_session(sid)["state"]
                    prior=state.get("cached_frame_outputs",{}).get(int(response["frame_index"])-1,{})
                    for oid,mask in prior.items():
                        if getattr(mask,"is_cuda",False):prior[oid]=mask.cpu()
                    if index%50==0:
                        print(json.dumps({"stage":"tracking","prompt":prompt,"sample":index,"samples":len(rows),"source_seconds":rows[index]["timestamp_seconds"]}),flush=True)
            finally:
                predictor.handle_request({"type":"close_session","session_id":sid})
            write_json(group/"geometry.json",{"prompt":prompt,"classes":classes,"chunk_size":chunk_size,"completed_through":end-1,"rows":[results.get(i,{}) for i in range(end)]})
            if end==len(rows):break
    write_json(group/"status.json",{"status":"completed","samples":len(rows)})


def select_grounded_evidence(output, classes):
    data=read(output/"input.json"); rows=data["rows"]
    original={r["source_frame"]:r for r in map(json.loads,(Path(data["recorded_run"])/"observations.jsonl").read_text(encoding="utf-8").splitlines())}
    for class_id in classes:
        key=TARGETS[class_id].key; group=output/f"grounding_{class_id}"
        status=read(group/"status.json")
        samples=[json.loads(line) for line in (group/"candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        if status["status"]!="completed" or len(samples)!=len(rows):
            raise ValueError("incomplete independent grounding")
        selector=GroundedInstanceSelector(rows[0]["vlx_seeds"][key],data["original_manifest"]["config"]["interaction"]["max_observation_gap_seconds"])
        refined=[]
        for i,(sample,row) in enumerate(zip(samples,rows)):
            if sample["index"]!=i or sample["time"]!=row["timestamp_seconds"]:
                raise ValueError("grounding/source sample mismatch")
            vlx=next((o for o in original[row["source_frame"]]["objects"] if o["key"]==key),None)
            geometry=selector.update(sample["time"],sample["candidates"],vlx)
            if i==0 and geometry is None:
                raise ValueError("initial fixed instance is ambiguous")
            refined.append({key:geometry} if geometry else {})
        write_json(group/"geometry.json",{"classes":[class_id],"prompt":status["prompt"],
            "candidate_sha256":hashlib.sha256((group/"candidates.jsonl").read_bytes()).hexdigest(),"rows":refined})


def ground(output, checkpoint, repository, classes):
    """Independent semantic checks for objects vulnerable to support drift."""
    os.environ["HF_HUB_OFFLINE"]="1";sys.path.insert(0,str(repository))
    import torch
    from PIL import Image
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
    # The 3.1 detector has three FPN levels. The generic image adapter's fourth
    # level is discarded by scalp=1; remove it before strict weight validation.
    model=build_sam3_image_model(device="cpu",load_from_HF=False)
    model.backbone.vision_backbone.convs=model.backbone.vision_backbone.convs[:3]
    model.backbone.vision_backbone.scale_factors=model.backbone.vision_backbone.scale_factors[:3]
    model.backbone.scalp=0
    state=torch.load(str(checkpoint),map_location="cpu",weights_only=True)
    state=state.get("model",state)
    detector={k.removeprefix("detector."):v for k,v in state.items() if k.startswith("detector.")}
    incompatible=model.load_state_dict(detector,strict=False)
    if incompatible.missing_keys:
        raise ValueError(f"missing detector weights: {incompatible.missing_keys}")
    del state,detector
    model=model.cuda().eval();processor=Sam3Processor(model,confidence_threshold=.3)
    rows=read(output/"input.json")["rows"]
    for class_id in classes:
        group=output/f"grounding_{class_id}";group.mkdir(exist_ok=False)
        prompt=TARGETS[class_id].phrase
        with torch.inference_mode(),torch.autocast("cuda",dtype=torch.bfloat16),(group/"candidates.jsonl").open("w",encoding="utf-8",buffering=1) as stream:
            for i,row in enumerate(rows):
                with Image.open(output/"frames"/f"{i:06d}.jpg") as image:
                    state=processor.set_image(image.convert("RGB"))
                prediction=processor.set_text_prompt(prompt,state);found=[]
                for mask,score in zip(prediction["masks"].cpu().numpy(),prediction["scores"].cpu().float().numpy()):
                    g=mask_geometry(mask)
                    if g is not None:found.append(dict(g,score=float(score)))
                stream.write(json.dumps({"index":i,"time":row["timestamp_seconds"],"candidates":found})+"\n")
                if i%50==0:print(json.dumps({"stage":"grounding","target":TARGETS[class_id].key,"sample":i,"samples":len(rows)}),flush=True)
        write_json(group/"status.json",{"status":"completed","samples":len(rows),"prompt":prompt,
                   "model":"sam31_detector_image","missing_weights":[],"confidence_threshold":.3})
    select_grounded_evidence(output,classes)


def load_refined_evidence(output, rows):
    refined={};groups=[];grounded=set()
    grounding_paths=sorted(output.glob("grounding_*/geometry.json"))
    for path in grounding_paths:grounded.update(read(path)["classes"])
    for path in sorted(output.glob("masks_*/geometry.json"))+grounding_paths:
        group=read(path)
        if path.parent.name.startswith("masks_") and set(group["classes"]) & grounded:
            if not set(group["classes"])<=grounded:raise ValueError("partially superseded tracking group")
            continue
        if read(path.parent/"status.json")["status"]!="completed" or len(group["rows"])!=len(rows):
            raise ValueError("incomplete refinement group")
        for index,objects in enumerate(group["rows"]):
            if set(refined.get(index,{})) & set(objects):raise ValueError("overlapping target groups")
            refined.setdefault(index,{}).update(objects)
        groups.append({"file":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"prompt":group["prompt"],
                       "tracking_chunk_size":group.get("chunk_size"),
                       "mode":"independent_semantic_validation" if path in grounding_paths else "mask_tracking"})
    if not groups:raise ValueError("no completed refinement groups")
    return refined,groups


def render(output, user_missing_grace_seconds=4, reuse_refinement_from=None):
    if reuse_refinement_from is not None:
        source=Path(reuse_refinement_from).resolve()
        if source==output.resolve():raise ValueError("replay output must be a new directory")
        source_data=read(source/"input.json")
        _,source_groups=load_refined_evidence(source,source_data["rows"])
        output.mkdir(parents=True,exist_ok=False)
        shutil.copy2(source/"input.json",output/"input.json")
        (output/"frames").mkdir()
        for index in range(len(source_data["rows"])):
            name=f"{index:06d}.jpg"
            shutil.copyfile(source/"frames"/name,output/"frames"/name)
        for item in source_groups:
            geometry=Path(item["file"])
            target=output/geometry.parent.name;target.mkdir()
            shutil.copy2(geometry,target/"geometry.json")
            shutil.copy2(geometry.parent/"status.json",target/"status.json")
    data=read(output/"input.json"); original=data["original_manifest"]; config=original["config"]; rows=data["rows"]
    config["interaction"]["user_missing_grace_seconds"]=user_missing_grace_seconds
    config["interaction"]=asdict(InteractionConfig(**config["interaction"]))
    refined,groups=load_refined_evidence(output,rows)
    provenance={"mode":"recorded_vlx_pose_fresh_sam31_refinement","fresh_vlx_inference":False,"fresh_sam31_tracking":True,
        "recorded_run":data["recorded_run"],"recorded_trace_sha256":data["recorded_trace_sha256"],"groups":groups,
        "fresh_sam31_grounding":any(g["mode"]=="independent_semantic_validation" for g in groups),
        "recomputed":["anonymous_user_association","mask_tracking","interaction_decisions"],"time_basis":"source_video_pts_seconds"}
    if reuse_refinement_from is not None:
        provenance.update(mode="recorded_vlx_pose_sam31_behavior_replay",fresh_sam31_tracking=False,
            fresh_sam31_grounding=False,recorded_refinement=str(source),
            recorded_refinement_input_sha256=hashlib.sha256((source/"input.json").read_bytes()).hexdigest(),
            recomputed=["interaction_decisions","video_overlay"])
    targets=tuple(TARGETS[i] for i in data["classes"])
    write_json(output/"manifest.json",dict(original,targets=[asdict(t) for t in targets],
        evidence_provenance=provenance,interaction_policy=INTERACTION_POLICY))
    a=InteractionAnalyzer(InteractionConfig(**config["interaction"]),targets=targets); overlay=Overlay(config["font_path"])
    overlay.evidence_label=" | VLX定位 + SAM3.1跟踪"
    writer=None;last={}
    try:
        with (output/"observations.jsonl").open("w",encoding="utf-8") as trace:
            for index,row in enumerate(rows):
                t=row["timestamp_seconds"]; objects=[]
                for key,g in refined[index].items():
                    continuous=g.get("continuous",key in last and t-last[key]<=config["interaction"]["max_observation_gap_seconds"])
                    objects.append(ObjectEvidence(key,tuple(g["bbox"]),continuous));last[key]=t
                people=[PersonEvidence(**p) for p in row["people"]];decision=a.process(t,objects,people);report=a.report()
                trace.write(json.dumps(dict(row,objects=[asdict(o) for o in objects],decision=decision,mask_geometry=refined[index]),ensure_ascii=False)+"\n")
                frame=cv2.imread(str(output/"frames"/f"{index:06d}.jpg"))
                image=overlay.render(frame,t,objects,people,decision,report["total"],report=report)
                if writer is None:writer=PreviewWriter(output,config["preview_fps"],image)
                writer.push(t,image)
            result=a.finish()
            result.update(evidence_provenance=provenance,source_sha256=original["source_sha256"],users=data["users"],duration_seconds=original["timeline"]["duration_seconds"],qualification="implementation_output_requires_visual_review")
            writer.finish(result["duration_seconds"])
            write_json(output/"result.json",result)
            (output/"events.jsonl").write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in result['events']),encoding='utf-8')
            write_json(output/"status.json",{"status":"completed","total":result["total"]})
            print(json.dumps(result["total"]),flush=True)
    except BaseException:
        if writer:writer.abort()
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage",choices=["prepare","track","ground","select","render"])
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--recorded-run",type=Path)
    parser.add_argument("--reuse-refinement-from",type=Path,
        help="Render only: copy completed evidence into a new output and recompute behavior without model inference")
    parser.add_argument("--classes",default="0,1,2")
    parser.add_argument("--prompt",default="stuffed toy")
    parser.add_argument("--chunk-size",type=int,default=150,help="Tracking window samples; larger windows require more GPU memory")
    parser.add_argument("--checkpoint",type=Path)
    parser.add_argument("--sam-repository",type=Path)
    parser.add_argument("--user-missing-grace-seconds",type=float,default=4)
    args=parser.parse_args();cv2.setNumThreads(2)
    classes=[int(v) for v in args.classes.split(',')]
    if any(i<0 or i>=len(TARGETS) for i in classes):parser.error('unknown target class')
    if args.stage=="prepare":prepare(args.recorded_run,args.output_dir,classes)
    elif args.stage=="track":track(args.output_dir,args.checkpoint,args.sam_repository,args.prompt,classes,args.chunk_size)
    elif args.stage=="ground":ground(args.output_dir,args.checkpoint,args.sam_repository,classes)
    elif args.stage=="select":select_grounded_evidence(args.output_dir,classes)
    else:render(args.output_dir,args.user_missing_grace_seconds,args.reuse_refinement_from)


if __name__=="__main__":main()
