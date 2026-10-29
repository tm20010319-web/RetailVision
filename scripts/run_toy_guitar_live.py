"""Standalone camera-1 live YOLO preview for yellow bee toy and white guitar.

Reads existing camera streams/settings only. No business database, identity,
SAM3 or staff-vest runtime integration. Q/Esc/window-close stops this preview.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import hashlib
import logging
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import yaml

ROOT = Path(__file__).resolve().parents[1]
WINDOW_TITLE = "Behavior-1 | Yellow Bee & White Guitar | Q to close"
COLORS = {0: "#FFD54A", 1: "#48D7FF"}


def resolve_path(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def camera_password(camera: dict, environ=None) -> str:
    environ = os.environ if environ is None else environ
    value = environ.get(camera["password_env"])
    if value is None:
        if not camera.get("allow_empty_password", False):
            raise ValueError("Camera password environment variable is not set")
        value = ""
    return value


def make_source(camera: dict, password: str) -> str:
    user = urllib.parse.quote(camera["username"], safe="")
    password = urllib.parse.quote(password, safe="")
    path = camera["rtsp_path"]
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("RTSP path must be an absolute device path")
    return f"rtsp://{user}:{password}@{camera['host']}:{int(camera['rtsp_port'])}{path}"


def verify_p2p(body: bytes, expected_prefix: str) -> None:
    root = ET.fromstring(body)
    if not any((element.text or "").strip().startswith(expected_prefix) for element in root.iter()):
        raise ValueError("Camera P2P identity does not match the selected behavior-detection-1 device")


def verify_camera(camera: dict, password: str) -> dict:
    base = f"http://{camera['host']}:{int(camera['http_port'])}"
    credentials = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    credentials.add_password(None, base, camera["username"], password)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPBasicAuthHandler(credentials))
    with opener.open(base + "/Network/P2PV2", timeout=6) as response:
        verify_p2p(response.read(), camera["expected_p2p_prefix"])
    with opener.open(base + "/System/DeviceInfo", timeout=6) as response:
        root = ET.fromstring(response.read())
    safe_keys = {"DeviceName", "DeviceTypeName", "SerialNum", "SoftWareVersion"}
    metadata = {e.tag.split("}")[-1]:e.text for e in root.iter() if e.tag.split("}")[-1] in safe_keys}
    return {"camera_id":camera["id"],"camera_name":camera["name"],"host":camera["host"],
            "p2p_identity_verified":True,"device":metadata,"verified_at_utc":datetime.now(timezone.utc).isoformat()}


def verify_model_names(actual: dict, expected: dict) -> None:
    if actual != expected:
        raise ValueError(f"Model classes disagree with the dedicated live configuration: {actual}")


class VerifiedItemModel:
    """Require a second detector to agree on configured classes in this frame."""
    def __init__(self, primary, verification, classes, match_iou):
        self.primary=primary
        self.verification=verification
        self.classes=set(classes)
        self.match_iou=match_iou
        self.names=primary.names

    @staticmethod
    def overlap(a, b):
        width=max(0,min(a[2],b[2])-max(a[0],b[0]))
        height=max(0,min(a[3],b[3])-max(a[1],b[1]))
        intersection=width*height
        union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
        return intersection/union if union>0 else 0

    def predict(self, frame, **kwargs):
        results=self.primary.predict(frame,**kwargs)
        result=results[0]
        primary=result.boxes.data.cpu().numpy()
        needed=self.classes & {int(row[-1]) for row in primary}
        if not needed:
            return results
        verification_kwargs={**kwargs,'classes':sorted(needed)}
        verified=self.verification.predict(frame,**verification_kwargs)[0].boxes.data.cpu().numpy()
        keep=[]
        used=set()
        for index in sorted(range(len(primary)),key=lambda n:float(primary[n][-2]),reverse=True):
            row=primary[index]
            if int(row[-1]) not in self.classes:
                keep.append(index)
                continue
            matches=[(self.overlap(row[:4],other[:4]),n) for n,other in enumerate(verified)
                if n not in used and int(other[-1])==int(row[-1])]
            if matches:
                overlap,n=max(matches)
                if overlap>=self.match_iou:
                    keep.append(index)
                    used.add(n)
        # One-to-one matching also prevents duplicate boxes for one verified item.
        return [result[sorted(keep)]]


def load_item_model(model_config: dict, primary_model=None):
    from ultralytics import YOLO
    def load(value):
        weights=resolve_path(value)
        if not weights.is_file():
            raise ValueError('Trained item checkpoint is missing')
        return YOLO(str(weights))
    primary=primary_model if primary_model is not None else load(model_config['weights'])
    verify_model_names(primary.names,model_config['expected_names'])
    verification_weights=model_config.get('verification_weights')
    if verification_weights is None:
        return primary
    classes=model_config.get('verification_classes',list(model_config['expected_names']))
    match_iou=float(model_config.get('verification_match_iou',.4))
    if not classes or not set(classes)<=set(model_config['expected_names']) or not 0<match_iou<=1:
        raise ValueError('Invalid item verification classes or matching IoU')
    verification=load(verification_weights)
    verify_model_names(verification.names,model_config['expected_names'])
    return VerifiedItemModel(primary,verification,classes,match_iou)


def predict_item_boxes(model, frame, model_config: dict) -> list[dict]:
    """Optionally retry missing classes at another size on this same frame."""
    def predict(size, classes):
        result=model.predict(frame,imgsz=size,device=model_config['device'],
            conf=model_config['confidence'],iou=model_config.get('iou',.7),
            classes=classes,verbose=False)[0]
        return [{'class_id':int(box.cls.item()),'confidence':float(box.conf.item()),
                 'bbox_xyxy':box.xyxy[0].tolist()} for box in result.boxes]

    boxes=predict(model_config['imgsz'],list(model_config['expected_names']))
    fallback=model_config.get('fallback_imgsz')
    missing=set(model_config['expected_names'])-{box['class_id'] for box in boxes}
    missing&=set(model_config.get('fallback_classes',model_config['expected_names']))
    if fallback is not None and fallback!=model_config['imgsz'] and missing:
        if not isinstance(fallback,int) or isinstance(fallback,bool) or fallback<=0:
            raise ValueError('Fallback inference size must be a positive integer')
        boxes.extend(predict(fallback,sorted(missing)))
    return boxes


def input_color_issue(frame, maximum_magenta_fraction: float | None = None) -> str | None:
    """Optional site-specific check for the observed camera-wide magenta failure."""
    if maximum_magenta_fraction is None:
        return None
    pixels=frame[::4,::4]
    magenta=(pixels[:,:,0]>180)&(pixels[:,:,1]<80)&(pixels[:,:,2]>180)
    return "camera_magenta_cast" if float(magenta.mean())>maximum_magenta_fraction else None


class LatestCamera:
    """Continuously decode while retaining one latest source frame, without queues."""
    def __init__(self, source: str):
        self.source=source
        self.lock=threading.Lock()
        self.stop_event=threading.Event()
        self.latest=None
        self.sequence=0
        self.thread=threading.Thread(target=self._read, name="camera1-decoder", daemon=True)

    def _read(self):
        while not self.stop_event.is_set():
            capture=cv2.VideoCapture(self.source,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,5000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,3000])
            try:
                while capture.isOpened() and not self.stop_event.is_set():
                    ok,frame=capture.read()
                    if not ok or frame is None:
                        break
                    with self.lock:
                        self.sequence+=1
                        self.latest=(self.sequence,time.monotonic(),datetime.now(timezone.utc).isoformat(),frame)
            finally:
                capture.release()
            self.stop_event.wait(1)

    def get(self):
        with self.lock:
            if self.latest is None:
                return None
            sequence,received,utc,frame=self.latest
            return sequence,received,utc,frame.copy()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)


def render_frame(frame, detections: list[dict], camera_name: str, names: dict, fps: float, font):
    im=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
    draw=ImageDraw.Draw(im)
    for item in detections:
        class_id=item["class_id"]
        if class_id not in names:
            continue
        x1,y1,x2,y2=item["bbox_xyxy"]
        draw.rectangle((x1,y1,x2,y2),outline=COLORS[class_id],width=2)
        text=f"{names[class_id]} {item['confidence']:.2f}"
        l,t,r,b=draw.textbbox((0,0),text,font=font)
        left=min(max(0,int(x1)),max(0,im.width-(r-l)-8))
        top=max(0,int(y1)-(b-t)-10)
        draw.rectangle((left,top,left+r-l+8,top+b-t+7),fill=COLORS[class_id])
        draw.text((left+4,top+3-t),text,font=font,fill="#111827")
    counts={key:sum(item["class_id"]==key for item in detections) for key in names}
    canvas=Image.new("RGB",(im.width,im.height+56),"#111827")
    canvas.paste(im,(0,56))
    d=ImageDraw.Draw(canvas)
    d.text((10,3),f"{camera_name}  |  实时物品识别  |  {fps:.1f} FPS",font=font,fill="white")
    d.text((10,29),"   ".join(f"{names[k]}：{counts[k]}" for k in names)+"    Q / Esc 退出",font=font,fill="#cbd5e1")
    return cv2.cvtColor(np.asarray(canvas),cv2.COLOR_RGB2BGR),counts


def replace_snapshot(temporary: Path, path: Path) -> bool:
    # Windows readers can briefly deny replacing a status/image they have open.
    for attempt in range(3):
        try:
            os.replace(temporary,path)
            return True
        except PermissionError as error:
            if os.name!="nt" or error.winerror not in (5,32,33):
                raise
            if attempt<2:
                time.sleep(.005)
    return False


def atomic_json(path: Path, value: dict) -> bool:
    temporary=path.with_name(path.stem+".tmp.json")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    return replace_snapshot(temporary,path)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=ROOT/"config/toy_guitar_live.yaml")
    parser.add_argument("--duration",type=float,default=0,help="Seconds; zero runs until Q/Esc/close or stop.request")
    parser.add_argument("--headless",action="store_true")
    parser.add_argument("--runtime-dir",type=Path)
    args=parser.parse_args()
    config=yaml.safe_load(args.config.read_text(encoding="utf-8"))
    runtime=args.runtime_dir or resolve_path(config["preview"]["runtime_dir"])
    runtime.mkdir(parents=True,exist_ok=True)
    log=(runtime/"live.log").open("a",encoding="utf-8",buffering=1)
    sys.stdout=sys.stderr=log
    logging.basicConfig(stream=log,level=logging.INFO,format="%(asctime)s %(message)s",force=True)
    state={"status":"starting","pid":os.getpid(),"camera_name":config["camera"]["name"],"processed_frames":0}
    atomic_json(runtime/"status.json",state)
    camera=None
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    try:
        model_config=config["model"]
        inference_fps=float(config["preview"]["inference_fps"])
        maximum_age=float(config["preview"]["max_frame_age_seconds"])
        maximum_magenta=config["preview"].get("maximum_magenta_fraction")
        if maximum_magenta is not None and not 0<float(maximum_magenta)<1:
            raise ValueError("Maximum magenta fraction must be between zero and one")
        if not math.isfinite(inference_fps) or inference_fps<=0 or not math.isfinite(maximum_age) or maximum_age<=0:
            raise ValueError("Preview FPS and frame age must be positive")
        password=camera_password(config["camera"])
        identity=verify_camera(config["camera"],password)
        atomic_json(runtime/"camera_identity.json",identity)
        weights=resolve_path(model_config["weights"])
        if not weights.is_file():
            raise ValueError("Trained toy/guitar checkpoint is missing")
        settings_dir=ROOT/"runs/toy_guitar/_ultralytics_settings"
        settings_dir.mkdir(parents=True,exist_ok=True)
        os.environ["YOLO_CONFIG_DIR"]=str(settings_dir)
        os.environ["YOLO_OFFLINE"]="true"
        os.environ["YOLO_AUTOINSTALL"]="false"
        model=load_item_model(model_config)
        with weights.open("rb") as checkpoint:
            state.update(model_weights=str(weights),model_weights_sha256=hashlib.file_digest(checkpoint,"sha256").hexdigest())
        state.update(model_imgsz=model_config['imgsz'],model_fallback_imgsz=model_config.get('fallback_imgsz'),
            model_fallback_classes=model_config.get('fallback_classes'))
        if model_config.get('verification_weights') is not None:
            verification_weights=resolve_path(model_config['verification_weights'])
            with verification_weights.open('rb') as checkpoint:
                state.update(model_verification_weights=str(verification_weights),
                    model_verification_weights_sha256=hashlib.file_digest(checkpoint,'sha256').hexdigest(),
                    model_verification_classes=model_config.get('verification_classes'),
                    model_verification_match_iou=model_config.get('verification_match_iou',.4))
        model.predict(np.zeros((448,800,3),dtype=np.uint8),imgsz=model_config["imgsz"],device=model_config["device"],verbose=False)
        camera=LatestCamera(make_source(config["camera"],password))
        camera.thread.start()
        font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",16)
        if not args.headless:
            cv2.namedWindow(WINDOW_TITLE,cv2.WINDOW_NORMAL|cv2.WINDOW_KEEPRATIO)
            cv2.resizeWindow(WINDOW_TITLE,int(config["preview"]["window_width"]),813)
        start=time.monotonic()
        start_wall=time.time()
        last_sequence=0
        last_snapshot=0
        next_inference=0
        times=[]
        stale_screen=np.zeros((504,800,3),dtype=np.uint8)
        cv2.putText(stale_screen,"Waiting for a fresh camera frame...",(30,240),cv2.FONT_HERSHEY_SIMPLEX,.8,(230,230,230),2)
        while not stop.is_set():
            now=time.monotonic()
            if args.duration>0 and now-start>=args.duration:
                break
            stop_file=runtime/"stop.request"
            if stop_file.exists() and stop_file.stat().st_mtime>=start_wall:
                break
            if not args.headless:
                key=cv2.waitKey(1)&255
                if key in (ord("q"),ord("Q"),27) or cv2.getWindowProperty(WINDOW_TITLE,cv2.WND_PROP_VISIBLE)<1:
                    break
            sample=camera.get()
            if sample is None or now-sample[1]>maximum_age:
                state.update(status="waiting_for_fresh_frame",current_detections=[],counts={str(k):0 for k in model_config["display_names"]})
                atomic_json(runtime/"status.json",state)
                if not args.headless:
                    cv2.imshow(WINDOW_TITLE,stale_screen)
                stop.wait(.05)
                continue
            sequence,received,utc,frame=sample
            if sequence==last_sequence or now<next_inference:
                stop.wait(.005)
                continue
            last_sequence=sequence
            color_issue=input_color_issue(frame,maximum_magenta)
            if color_issue:
                preview,_=render_frame(frame,[],config["camera"]["name"],model_config["display_names"],0,font)
                warning=Image.fromarray(cv2.cvtColor(preview,cv2.COLOR_BGR2RGB))
                draw=ImageDraw.Draw(warning)
                draw.rectangle((0,0,warning.width,56),fill="#8B0000")
                draw.text((10,3),"摄像头色彩异常：紫红色失真，已暂停 AI 识别",font=font,fill="white")
                draw.text((10,29),"等待摄像头恢复正常画面；当前不显示物品检测框",font=font,fill="white")
                preview=cv2.cvtColor(np.asarray(warning),cv2.COLOR_RGB2BGR)
                state.update(status="invalid_camera_input",input_issue=color_issue,
                    rejected_input_frames=state.get("rejected_input_frames",0)+1,
                    current_detections=[],counts={v:0 for v in model_config["semantic_names"].values()},
                    source_frame_sequence=sequence,frame_received_at_utc=utc,
                    frame_size=[frame.shape[1],frame.shape[0]],
                    frame_age_seconds=round(now-received,3),inference_fps=0,
                    updated_at_utc=datetime.now(timezone.utc).isoformat())
                atomic_json(runtime/"status.json",state)
                if not args.headless:
                    cv2.imshow(WINDOW_TITLE,preview)
                if now-last_snapshot>=1:
                    if not cv2.imwrite(str(runtime/"latest.tmp.jpg"),preview):
                        raise OSError("Could not save invalid-input preview")
                    replace_snapshot(runtime/"latest.tmp.jpg",runtime/"latest.jpg")
                    last_snapshot=now
                next_inference=now+1/inference_fps
                continue
            boxes=predict_item_boxes(model,frame,model_config)
            detections=[{**box,"label":model_config["display_names"][box['class_id']],
                "semantic_name":model_config["semantic_names"][box['class_id']]} for box in boxes]
            completed=time.monotonic()
            if completed-received>maximum_age:
                continue
            times=[t for t in times if completed-t<3]+[completed]
            fps=(len(times)-1)/(times[-1]-times[0]) if len(times)>1 else 0
            preview,counts=render_frame(frame,detections,config["camera"]["name"],model_config["display_names"],fps,font)
            state.update(status="running",input_issue=None,processed_frames=state["processed_frames"]+1,source_frame_sequence=sequence,frame_received_at_utc=utc,
                frame_size=[frame.shape[1],frame.shape[0]],frame_age_seconds=round(completed-received,3),inference_fps=round(fps,2),
                current_detections=detections,counts={model_config["semantic_names"][k]:v for k,v in counts.items()},updated_at_utc=datetime.now(timezone.utc).isoformat())
            atomic_json(runtime/"status.json",state)
            if not args.headless:
                cv2.imshow(WINDOW_TITLE,preview)
            if completed-last_snapshot>=1:
                if not cv2.imwrite(str(runtime/"latest.tmp.jpg"),preview):
                    raise OSError("Could not save latest detection preview")
                replace_snapshot(runtime/"latest.tmp.jpg",runtime/"latest.jpg")
                last_snapshot=completed
            next_inference=max(completed,now+1/inference_fps)
        state["status"]="stopped"
        if args.headless and state["processed_frames"]==0:
            raise RuntimeError("Headless probe did not process a fresh camera frame")
        return 0
    except Exception as error:
        logging.exception("Live preview failed")
        state.update(status="failed",error_type=type(error).__name__)
        return 1
    finally:
        if camera is not None:
            camera.close()
        cv2.destroyAllWindows()
        state["updated_at_utc"]=datetime.now(timezone.utc).isoformat()
        atomic_json(runtime/"status.json",state)


if __name__=="__main__":
    raise SystemExit(main())
