"""Connect optional YOLO or VLX initialization to original SAM3.1 tracking.

The default agents environment owns the responsive viewer; a dedicated SAM3
worker runs the original rolling-window providers and Stage7C/8/9/10 runtime.
No identity/DB/production publication is enabled. Q/Esc closes recordings safely.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib
import json
import logging
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import yaml

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_toy_guitar_live import resolve_path, camera_password, make_source, verify_camera, atomic_json

TITLE='Behavior-1 | SAM3.1 Tracking + Recording | Q / Esc'


def load_config(path):
    config=yaml.safe_load(path.read_text(encoding='utf-8'))
    if config['products']!={0:'yellow_bee_toy',1:'white_guitar'}:raise ValueError('Two-target class mapping differs')
    if config['frame_size']!=[1280,720] or config['sample_fps']!=5.0:raise ValueError('Keep original SAM3.1 geometry and sampling')
    if not 0<float(config['segment_seconds'])<=300 or not .2<float(config['max_recording_gap_seconds'])<=2:raise ValueError('Invalid recording limits')
    live=yaml.safe_load(resolve_path(config['live_config']).read_text(encoding='utf-8'))
    if live['model']['expected_names']!={0:'toy',1:'white_guitar'}:raise ValueError('Unexpected YOLO names')
    return config,live


def bbox_iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/max(1e-9,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)


def select_initial_boxes(detections):
    selected={}
    for class_id in (0,1):
        items=sorted([b for b in detections if b['class_id']==class_id],key=lambda b:-b['confidence'])
        if not items:raise ValueError(f'Initialization target missing: class {class_id}; put both targets in view')
        best=items[0]
        if any(bbox_iou(best['bbox_xyxy'],b['bbox_xyxy'])<.30 and best['confidence']-b['confidence']<.15 for b in items[1:]):
            raise ValueError(f'Ambiguous initialization: multiple distinct class {class_id} candidates')
        selected[class_id]=best
    if bbox_iou(selected[0]['bbox_xyxy'],selected[1]['bbox_xyxy'])>.10:raise ValueError('Initialization targets overlap ambiguously')
    return selected


def propose(args,config,live):
    settings=ROOT/'runs/toy_guitar/_ultralytics_settings';settings.mkdir(parents=True,exist_ok=True)
    os.environ.update(YOLO_CONFIG_DIR=str(settings),YOLO_OFFLINE='true',YOLO_AUTOINSTALL='false')
    from ultralytics import YOLO
    weights=resolve_path(live['model']['weights']);model=YOLO(str(weights))
    if model.names!=live['model']['expected_names']:raise ValueError('Checkpoint class mapping differs')
    result=model.predict(str(args.seed_frame),imgsz=live['model']['imgsz'],device=live['model']['device'],conf=live['model']['confidence'],verbose=False)[0]
    detections=[{'class_id':int(b.cls.item()),'confidence':float(b.conf.item()),'bbox_xyxy':b.xyxy[0].tolist()} for b in result.boxes]
    chosen=select_initial_boxes(detections)
    prompts={}
    for k,b in chosen.items():
        x1,y1,x2,y2=b['bbox_xyxy'];box=[max(0,int(math.floor(x1))),max(0,int(math.floor(y1))),min(1280,int(math.ceil(x2))),min(720,int(math.ceil(y2)))]
        prompts[config['products'][k]]={'obj_id':k,'bbox_xyxy':box,'points':[[round((x1+x2)/2),round((y1+y2)/2)]],'point_labels':[1]}
    atomic_json(args.proposal_output,{'prompts':prompts,'detections':detections,'weights':str(weights),
        'weights_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'source_sha256':hashlib.sha256(args.seed_frame.read_bytes()).hexdigest()})


class OriginalBundleWithYoloInitialization:
    """Seed the original SAM provider from the EXACT first-window image."""
    def __init__(self,bundle,config,config_path,run):
        self.bundle=bundle;self.config=config;self.config_path=config_path;self.run=run;self.seeded=False

    def __getattr__(self,name):return getattr(self.bundle,name)

    def semantic_initialization(self,context):
        """Night-vision fallback using real SAM3 masks, with the original gate."""
        from agent.sam31_product_tracking import evaluate_candidate
        base=self.bundle.base;predictor=self.bundle.predictor
        folder=self.run/'semantic_initialization';folder.mkdir(exist_ok=True)
        cv2.imwrite(str(folder/'00000.jpg'),context.frame)
        prompts={};diagnostics=[]
        with base.torch.inference_mode(),base.autocast_context():
            for k,text in [(0,'plush toy'),(1,'guitar')]:
                session=predictor.handle_request({'type':'start_session','resource_path':str(folder),'offload_video_to_cpu':True})['session_id']
                try:
                    output=self.bundle.product_module.stage7c.add_text_prompt(predictor,session,text)
                    candidates=self.bundle.product_module.stage7c.candidates_from_outputs((output or {}).get('outputs',{}))
                    credible=[]
                    for c in candidates:
                        evidence=evaluate_candidate(c,self.bundle.product_surface,frame_width=1280,frame_height=720)
                        if evidence.centroid_inside_product_display_surface and evidence.surface_overlap_ratio>=.5 and c.confidence>=.5:credible.append(c)
                    credible.sort(key=lambda c:-c.confidence)
                    diagnostics.append({'class_id':k,'text':text,'candidates':[{'id':c.local_obj_id,'confidence':c.confidence,'bbox':c.bbox} for c in candidates]})
                    if not credible:raise RuntimeError(f'SAM3 semantic initialization found no reliable {text}')
                    if len(credible)>1 and credible[0].confidence-credible[1].confidence<.15:raise RuntimeError(f'SAM3 initialization ambiguous for {text}')
                    c=credible[0];ys,xs=np.where(c.mask);cx,cy=c.centroid
                    index=int(np.argmin((xs-cx)**2+(ys-cy)**2));point=[int(xs[index]),int(ys[index])]
                    prompts[self.config['products'][k]]={'obj_id':k,'bbox_xyxy':[int(v) for v in c.bbox],'points':[point],'point_labels':[1]}
                    cv2.imwrite(str(folder/f'class_{k}_mask.png'),c.mask.astype(np.uint8)*255)
                finally:predictor.handle_request({'type':'close_session','session_id':session})
        if bbox_iou(prompts['yellow_bee_toy']['bbox_xyxy'],prompts['white_guitar']['bbox_xyxy'])>.10:raise RuntimeError('SAM3 initialization masks overlap ambiguously')
        atomic_json(folder/'proposal.json',{'method':'SAM3 semantic fallback after YOLO seed rejection','prompts':prompts,'diagnostics':diagnostics})
        return {'prompts':prompts,'method':'SAM3_semantic_fallback'}

    def process_window(self,contexts):
        if not self.seeded:
            seed=self.run/'initialization_source.png';output=self.run/'yolo_initialization.json'
            if not cv2.imwrite(str(seed),contexts[0].frame):raise OSError('Cannot save exact initialization source')
            with (self.run/'yolo_initialization.log').open('a',encoding='utf-8') as log:
                result=subprocess.run([self.config['yolo_python'],'-B',str(Path(__file__).resolve()),'--config',str(self.config_path),
                    '--seed-frame',str(seed),'--proposal-output',str(output)],stdout=log,stderr=subprocess.STDOUT,timeout=60,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:
                proposal=self.semantic_initialization(contexts[0])
                atomic_json(self.run/'accepted_initialization.json',proposal)
            else:
                proposal=json.loads(output.read_text(encoding='utf-8'))
                atomic_json(self.run/'accepted_initialization.json',{**proposal,'method':'YOLO_v2_exact_frame_seed'})
            self.bundle.initialization_prompts=proposal['prompts']
            self.bundle.candidate_assignments={k:p['obj_id'] for k,p in proposal['prompts'].items()}
            # Stage10 starts the bundle before asking the source for a window.
            # Update that already-created adapter with this exact-frame seed.
            product_provider=self.bundle.product_provider
            if product_provider is not None:
                product_provider.initialization_prompts=proposal['prompts']
                product_provider.candidate_assignments=product_provider._validate_assignment_config(self.bundle.candidate_assignments)
            self.seeded=True
        return self.bundle.process_window(contexts)


class RecordingSink:
    """Persist actual original Stage10 rendered frames with explicit time gaps."""
    def __init__(self,run,config):
        self.run=run;self.config=config;self.writer=None;self.segment_frames=0;self.total=0;self.segment_no=0;self.last_time=None
        self.rows=[];self.segments=[];self.first_time=None;self.path=None
        (run/'recordings').mkdir(parents=True,exist_ok=True)

    def close_segment(self,reason):
        if self.writer is None:return
        self.writer.release();self.writer=None
        metadata={'path':str(self.path),'frames':self.segment_frames,'fps':self.config['sample_fps'],
                  'first_source_time':self.first_time.isoformat(),'last_source_time':self.last_time.isoformat(),'close_reason':reason}
        atomic_json(self.path.with_suffix('.json'),metadata);self.segments.append(metadata)
        atomic_json(self.run/'recordings/index.json',{'segments':self.segments})

    def _write_latest_preview(self, rendered):
        """Update the viewer preview without letting a locked old frame stop recording."""
        temporary=self.run/'latest.tmp.jpg';latest=self.run/'latest.jpg'
        if not cv2.imwrite(str(temporary),rendered):raise OSError('Cannot save latest detection preview')
        try:
            os.replace(temporary,latest)
        except PermissionError:
            # Windows viewers may keep the previous JPEG open. The annotated
            # recording and metadata remain authoritative, so retain the last
            # readable preview and continue processing subsequent frames.
            try:temporary.unlink()
            except OSError:pass

    def __call__(self,rendered,result):
        current=result.context.timestamp
        if self.last_time and (current-self.last_time).total_seconds()>self.config['max_recording_gap_seconds']:
            self.close_segment('source_time_gap')
        if self.writer is None:
            self.segment_no+=1;self.segment_frames=0;self.first_time=current
            self.path=self.run/'recordings'/f'overlay_{self.segment_no:05d}.mp4'
            self.writer=cv2.VideoWriter(str(self.path),cv2.VideoWriter_fourcc(*'mp4v'),self.config['sample_fps'],(rendered.shape[1],rendered.shape[0]))
            if not self.writer.isOpened():raise OSError('Cannot create annotated recording')
        self.writer.write(rendered);self.segment_frames+=1;self.total+=1;self.last_time=current
        self._write_latest_preview(rendered)
        products=[{'product_key':p.product_key,'track_id':p.product_track_id,'track_token':p.product_track_token,
                   'status':p.track_status.value,'bbox':p.bbox} for p in result.products]
        atomic_json(self.run/'status.json',{'status':'running','pid':os.getpid(),'processed_frames':self.total,'camera_name':'行为检测-1',
            'engine':'original SAM3.1 + Stage7C/8/9/10','products':products,'source_time_utc':current.isoformat(),
            'frame_age_seconds':round((datetime.now(timezone.utc).replace(tzinfo=None)-current).total_seconds(),3),
            'current_recording':str(self.path),'closed_segments':len(self.segments),'updated_at_utc':datetime.now(timezone.utc).isoformat()})
        if self.segment_frames>=round(self.config['segment_seconds']*self.config['sample_fps']):self.close_segment('segment_limit')

    def close(self):self.close_segment('shutdown')


def prepare_vlx_window(config,live,run):
    """Locate before loading SAM, keeping both models out of GPU together."""
    from agent.stage10_demo import RtspFrameReader
    camera=live['camera'];reader=RtspFrameReader(source=make_source(camera,camera_password(camera)),camera_id=config['camera_id'],
        sample_fps=5.,frame_width=1280,frame_height=720,warmup_seconds=2.)
    contexts=[];deadline=time.monotonic()+20
    try:
        while len(contexts)<12 and time.monotonic()<deadline:
            context=reader.read_sampled()
            if context is not None:contexts.append(context)
    finally:reader.close()
    if len(contexts)!=12:raise RuntimeError('Could not capture the exact 12-frame VLX initialization window')
    seed=run/'vlx_initialization_source.png';output=run/'accepted_initialization.json'
    cv2.imwrite(str(seed),contexts[0].frame)
    atomic_json(run/'status.json',{'status':'vlx_text_localization','pid':os.getpid()})
    with (run/'vlx_initialization.log').open('a',encoding='utf-8') as log:
        result=subprocess.run([config['vlx_python'],'-B',str(ROOT/'scripts/probe_vlx_seek.py'),'--config',str(resolve_path(config['vlx_config'])),
            '--ground-image',str(seed),'--proposal-output',str(output)],stdout=log,stderr=subprocess.STDOUT,timeout=300,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('VLX localization failed; see vlx_initialization.log and raw result')
    proposal=json.loads(output.read_text(encoding='utf-8'))
    if proposal['source_sha256']!=hashlib.sha256(seed.read_bytes()).hexdigest():raise ValueError('VLX seed source mismatch')
    return tuple(contexts),proposal['prompts']


class PrefixedWindowSource:
    """Replay the exact initialization window, then use fresh original RTSP."""
    def __init__(self,source,contexts):self.source=source;self.contexts=contexts;self.first=True
    def __getattr__(self,name):return getattr(self.source,name)
    def start(self):self.source.start();return self
    def wait_window(self,**kwargs):
        if self.first:
            from agent.stage10_demo import RealtimeWindow
            self.first=False;return RealtimeWindow(0,self.contexts,self.contexts,0)
        window=self.source.wait_window(**kwargs)
        return None if window is None else replace(window,window_no=window.window_no+1)


class SequenceOffsetReader:
    def __init__(self,reader,offset):self.reader=reader;self.offset=offset
    def __getattr__(self,name):return getattr(self.reader,name)
    def read_sampled(self):
        context=self.reader.read_sampled()
        return None if context is None else replace(context,source_seq=context.source_seq+self.offset)


def worker(args,config,live):
    run=args.runtime;run.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    atomic_json(run/'status.json',{'status':'verifying_camera','pid':os.getpid()})
    camera=live['camera'];password=camera_password(camera)
    atomic_json(run/'camera_identity.json',verify_camera(camera,password))
    sys.path.insert(0,str(resolve_path(config['sam_runtime_root'])))
    from agent.sam31_real_adapter import _build_external_predictor
    from agent.sam31_realtime_providers import Sam31RealtimeProviderBundle
    from agent.sam31_product_tracking import ProductDisplaySurface
    from agent.sam31_behavior_adapter import Sam31BehaviorAdapter
    from agent.person_product_association import PersonProductAssociationConfig
    from agent.product_interaction import ProductInteractionConfig
    from agent.stage10_demo import (RtspFrameReader,AsyncRtspWindowSource,Stage10DemoRuntime,EventStream,JsonlEventSink,
                                   run_windowed_rtsp_demo)
    base=importlib.import_module('sam31_stage5_1_1_demo_latency_finalization')
    product=importlib.import_module('sam31_stage7c6_dual_smart_toy_probe')
    tracking=importlib.import_module('sam31_stage7c_product_tracking')
    keys=tuple(config['products'].values())
    initial_contexts=();initial_prompts=None
    if config.get('initializer')=='vlx_seek':
        initial_contexts,initial_prompts=prepare_vlx_window(config,live,run)
    # The original provider already supports process-local catalogs. Set these
    # explicitly so its old two-guitars compatibility fallback is not selected.
    product.PRODUCT_KEYS=keys;product.PRODUCT_TYPE='smart_toy';product.TEXT_PROMPT='yellow bee plush toy or white guitar'
    atomic_json(run/'status.json',{'status':'loading_sam3','pid':os.getpid()})
    base.configure_torch();predictor=_build_external_predictor(base,resolve_path(config['checkpoint']));base.tune_runtime(predictor)
    surface=ProductDisplaySurface(tuple(tuple(p) for p in config['display_surface']))
    product_config={'camera_id':config['camera_id'],'frame_size':config['frame_size'],'selected_product_keys':list(keys),
        'products':{'yellow_bee_toy':{'product_type':'smart_toy'},'white_guitar':{'product_type':'smart_guitar'}},'surface_overlap_min':.5,'product_track_lost_after_windows':3,
        'association_overlap_iou_min':.05,'association_geometry_distance_max':.2,'association_ambiguity_margin':.05}
    bundle=Sam31RealtimeProviderBundle(runtime_instance_id=run.name,output_dir=run/'original_pipeline',sam_runtime_root=config['sam_runtime_root'],
        selected_product_keys=keys,product_config=product_config,product_surface=surface,predictor=predictor,
        base_module=base,product_module=product,product_tracking_module=tracking,
        initialization_prompts=initial_prompts,candidate_assignments=None if initial_prompts is None else {k:p['obj_id'] for k,p in initial_prompts.items()})
    provider=bundle if initial_prompts is not None else OriginalBundleWithYoloInitialization(bundle,config,args.config.resolve(),run)
    a=json.loads(resolve_path(config['association_config']).read_text(encoding='utf-8'))
    association=PersonProductAssociationConfig(**{k:v for k,v in a.items() if k not in ('store_id','camera_id','frame_size','sync_mode')},frame_width=1280,frame_height=720)
    i=json.loads(resolve_path(config['interaction_config']).read_text(encoding='utf-8'))
    interaction=ProductInteractionConfig(**{k:v for k,v in i.items() if k not in ('camera_id','frame_size')},frame_width=1280,frame_height=720)
    events=JsonlEventSink(run/'event_stream.jsonl')
    class EmptyProducts:
        def process_frame(self,context):return ()
    runtime=Stage10DemoRuntime(camera_id=config['camera_id'],person_adapter=Sam31BehaviorAdapter(store_id=config['store_id'],camera_id=config['camera_id'],
        runtime_instance_id=run.name,frame_width=1280,frame_height=720),person_detector=lambda context:(),product_source=EmptyProducts(),
        association_config=association,interaction_config=interaction,event_stream=EventStream((events,)))
    reader=RtspFrameReader(source=make_source(camera,password),camera_id=config['camera_id'],sample_fps=5.,frame_width=1280,frame_height=720,warmup_seconds=2.)
    if initial_contexts:reader=SequenceOffsetReader(reader,initial_contexts[-1].source_seq+1)
    source=AsyncRtspWindowSource(reader,max_queue_size=config['queue_size'])
    if initial_contexts:source=PrefixedWindowSource(source,initial_contexts)
    sink=RecordingSink(run,config)
    atomic_json(run/'status.json',{'status':'waiting_for_initialization_window','pid':os.getpid()})
    try:
        summary=run_windowed_rtsp_demo(source=source,runtime=runtime,provider_bundle=provider,frame_consumer=sink,
            frame_result_path=run/'frame_results.jsonl',keyframes_dir=run/'keyframes',max_windows=args.max_windows,
            max_duration_seconds=args.duration if args.duration>0 else None,stop_requested=lambda:(run/'stop.request').exists())
        atomic_json(run/'runtime_summary.json',summary.to_dict())
        final=json.loads((run/'status.json').read_text(encoding='utf-8'));final['status']='stopped';atomic_json(run/'status.json',final)
        if sink.total==0:raise RuntimeError('Original pipeline emitted no recorded frames')
    finally:
        sink.close();events.close()


def waiting_image(status,initializer='yolo_v2'):
    im=Image.new('RGB',(1280,720),'#111827');d=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
    engine='VLX-Seek 文字定位 → SAM3.1 持续跟踪' if initializer=='vlx_seek' else 'YOLO / SAM3 初始化 → SAM3.1 持续跟踪'
    lines=['行为检测-1｜物品跟踪与带框录像',engine,f'状态：{status}',
           '正在初始化时，请让小蜜蜂和白色吉他保持可见。','分析按原流程分窗口进行，画面存在处理延迟。','Q / Esc 结束并保存录像']
    for n,line in enumerate(lines):d.text((40,130+n*62),line,font=font,fill='white')
    return cv2.cvtColor(np.asarray(im),cv2.COLOR_RGB2BGR)


def viewer(args,config):
    root=resolve_path(config['runtime_root']);root.mkdir(parents=True,exist_ok=True)
    run=root/datetime.now().strftime('%Y%m%d_%H%M%S_%f');run.mkdir()
    atomic_json(root/'current.json',{'runtime':str(run),'config':str(args.config.resolve()),'started_at_utc':datetime.now(timezone.utc).isoformat()})
    command=[config['sam_python'],'-B',str(Path(__file__).resolve()),'--config',str(args.config.resolve()),'--worker','--runtime',str(run)]
    if args.duration:command+=['--duration',str(args.duration)]
    if args.max_windows:command+=['--max-windows',str(args.max_windows)]
    with (run/'worker.log').open('a',encoding='utf-8') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        atomic_json(run/'launch.json',{'viewer_pid':os.getpid(),'worker_pid':process.pid,'engine':'original SAM3.1 + Stage10','config':str(args.config.resolve())})
        if args.headless:
            return process.wait()
        cv2.namedWindow(TITLE,cv2.WINDOW_NORMAL|cv2.WINDOW_KEEPRATIO);cv2.resizeWindow(TITLE,1280,720)
        last_mtime=0;last_draw=0
        try:
            cv2.imshow(TITLE,waiting_image('启动中',config.get('initializer')))
            while process.poll() is None:
                path=run/'latest.jpg'
                if path.exists() and path.stat().st_mtime!=last_mtime:
                    im=cv2.imread(str(path))
                    if im is not None:cv2.imshow(TITLE,im);last_mtime=path.stat().st_mtime
                elif not last_mtime and time.monotonic()-last_draw>1:
                    try:state=json.loads((run/'status.json').read_text(encoding='utf-8'))['status']
                    except (OSError,ValueError,KeyError):state='启动中'
                    cv2.imshow(TITLE,waiting_image(state,config.get('initializer')));last_draw=time.monotonic()
                key=cv2.waitKey(40)&255
                if key in (27,ord('q'),ord('Q')) or cv2.getWindowProperty(TITLE,cv2.WND_PROP_VISIBLE)<1:
                    (run/'stop.request').touch();break
            try:process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                logging.error('Worker has not stopped; preserving it to finalize recordings. See %s',run)
                return 1
        finally:cv2.destroyAllWindows()
        return process.returncode


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,default=ROOT/'config/toy_guitar_tracking_v2.yaml')
    parser.add_argument('--worker',action='store_true');parser.add_argument('--runtime',type=Path);parser.add_argument('--headless',action='store_true')
    parser.add_argument('--duration',type=float,default=0);parser.add_argument('--max-windows',type=int)
    parser.add_argument('--seed-frame',type=Path);parser.add_argument('--proposal-output',type=Path);args=parser.parse_args()
    config,live=load_config(args.config)
    if args.seed_frame:propose(args,config,live);return 0
    if args.worker:
        try:worker(args,config,live);return 0
        except Exception as e:
            atomic_json(args.runtime/'status.json',{'status':'failed','pid':os.getpid(),'error_type':type(e).__name__,'detail':str(e)})
            logging.exception('Original tracking pipeline failed');return 1
    return viewer(args,config)


if __name__=='__main__':raise SystemExit(main())
