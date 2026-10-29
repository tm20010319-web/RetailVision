"""Versioned multi-source YOLO training with preserved V1 holdouts and miss replay.

All sources share the same camera/scene. Held-out metrics are development
checks, not independent-video, arbitrary-placement or tracking qualification.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import yaml

from train_toy_guitar import PROJECT_ROOT, PROTECTED, SPLITS, resolve_path, sha256, write_json, source_records

DEFAULT_CONFIG=PROJECT_ROOT/'config/toy_guitar_train_v2.yaml'
BOUNDARY='same_camera_two_recordings_pose_holdout; adjacent/static appearances remain similar; no independent-video or arbitrary-relocation qualification'
EXTRA_PROTECTED=('config/toy_guitar_train.yaml','config/toy_guitar_live.yaml',
                 'tools/training/train_toy_guitar.py','runs/toy_guitar/baseline_v1_20261021/weights/best.pt')


def load_config(path):
    config=yaml.safe_load(path.read_text(encoding='utf-8'))
    if config['dataset']['names']!={0:'toy',1:'white_guitar'}:raise ValueError('Keep existing class IDs')
    if not config['dataset']['sources']:raise ValueError('Missing annotation sources')
    ids=[s['id'] for s in config['dataset']['sources']]
    if len(set(ids))!=len(ids) or any(not re.fullmatch('[a-z][a-z0-9_]*',x) for x in ids):raise ValueError('Invalid source IDs')
    train=config['training']
    if train['task']!='detect' or Path(train['name']).name!=train['name'] or train['name'] in ('','.','..'):raise ValueError('Invalid training target')
    for k in ('imgsz','epochs','patience','batch'):
        if type(train[k]) is not int or train[k]<1:raise ValueError(f'Invalid training.{k}')
    for k in ('confidence','iou','match_iou'):
        if not 0<float(config['evaluation'][k])<=1:raise ValueError(f'Invalid evaluation.{k}')
    return config


def assign_splits(source, records):
    if 'preserve_split_manifest' in source:
        old=json.loads(resolve_path(source['preserve_split_manifest']).read_text(encoding='utf-8'))
        by_name={e['image']:e for e in old['entries']}
        if set(by_name)!={r['image'] for r in records}:raise ValueError('V1 split membership mismatch')
        for row in records:
            old_row=by_name[row['image']]
            if any(row[k]!=old_row[k] for k in ('image_sha256','label_sha256','source_pts_seconds')):raise ValueError('V1 source changed')
        return {r['image']:by_name[r['image']]['split'] for r in records}
    result={}
    overrides=source.get('split_overrides',{})
    for row in records:
        index=int(Path(row['image']).stem.split('_')[-1])
        matches=[split for start,end,split in source['split_blocks'] if start<=index<=end]
        if len(matches)!=1:raise ValueError(f'Overlapping/missing split block: {index}')
        split=overrides.get(index,matches[0])
        if split not in SPLITS:raise ValueError('Unknown split')
        result[row['image']]=split
    if set(overrides)-{int(Path(r['image']).stem.split('_')[-1]) for r in records}:raise ValueError('Unknown override frame')
    return result


def prepare_dataset(config):
    entries=[];sources=[];seen=set();hashes=[]
    for source in config['dataset']['sources']:
        one={**config,'dataset':{**config['dataset'],'annotations':source['annotations']}}
        manifest,records=source_records(one)
        splits=assign_splits(source,records)
        by_name={r['image']:r for r in manifest['frames']}
        root=resolve_path(source['annotations'])
        sources.append({'id':source['id'],'annotations':str(root),'manifest_sha256':sha256(root/'annotation_manifest.json'),
                        'source_video':manifest['source_video'],'source_video_sha256':manifest['source_sha256']})
        for row in records:
            if row['image_sha256'] in seen:raise ValueError('Exact image duplicate across source/split')
            seen.add(row['image_sha256'])
            target=f"{source['id']}_{row['image']}";split=splits[row['image']]
            with Image.open(root/'images'/row['image']) as im:
                px=np.asarray(im.convert('L').resize((9,8),Image.Resampling.LANCZOS))
                dh=int.from_bytes(np.packbits(px[:,1:]<px[:,:-1]).tobytes(),'big')
            hashes.append((split,dh))
            entries.append({**row,'source_id':source['id'],'source_root':str(root),'source_image':row['image'],
                            'image':target,'split':split,'dataset_image':f'images/{split}/{target}',
                            'pose_group':by_name[row['image']].get('pose_group','original_fixed_pose'),
                            'dynamic_frame':by_name[row['image']].get('dynamic_frame',False)})
    regression=resolve_path(config['evaluation']['regression_image'])
    if sha256(regression) in seen:raise ValueError('Pinned regression image leaked into dataset')
    manifest={'sources':sources,'entries':entries,'names':config['dataset']['names'],
              'policy':'preserve_v1_holdouts_and_explicit_new_pose_blocks','evaluation_scope':BOUNDARY,
              'split_configuration':config['dataset']['sources'],'regression_image_sha256':sha256(regression)}
    report={'image_count':len(entries),'box_count':sum(len(e['boxes']) for e in entries),
            'splits':{},'exact_duplicate_images':0,'independent_video_evaluation':False,'evaluation_scope':BOUNDARY,
            'near_duplicate_cross_split_pairs':sum(a[0]!=b[0] and (a[1]^b[1]).bit_count()<=4 for i,a in enumerate(hashes) for b in hashes[i+1:]),
            'regression_exact_file_excluded':True}
    for split in SPLITS:
        selected=[e for e in entries if e['split']==split];counts=Counter(int(b[0]) for e in selected for b in e['boxes'])
        if set(counts)!={0,1}:raise ValueError(f'Missing class in {split}')
        report['splits'][split]={'images':len(selected),'class_counts':dict(counts),
                                 'source_counts':dict(Counter(e['source_id'] for e in selected)),
                                 'dynamic_new_frames':[e['source_image'] for e in selected if e['dynamic_frame']]}
    root=resolve_path(config['dataset']['output'])
    data={'path':root.as_posix(),'train':'images/train','val':'images/val','test':'images/test','nc':2,'names':config['dataset']['names']}
    if root.exists():
        if json.loads((root/'split_manifest.json').read_text(encoding='utf-8'))!=json.loads(json.dumps(manifest)):raise ValueError('Existing dataset differs; select a new output')
        if yaml.safe_load((root/'data.yaml').read_text(encoding='utf-8'))!=data:raise ValueError('Dataset YAML changed')
    else:
        for split in SPLITS:
            (root/'images'/split).mkdir(parents=True,exist_ok=False);(root/'labels'/split).mkdir(parents=True,exist_ok=False)
        for e in entries:
            src=Path(e['source_root']);shutil.copy2(src/'images'/e['source_image'],root/e['dataset_image'])
            shutil.copy2(src/'labels'/Path(e['source_image']).with_suffix('.txt'),root/'labels'/e['split']/Path(e['image']).with_suffix('.txt'))
        write_json(root/'split_manifest.json',manifest)
        (root/'data.yaml').write_text(yaml.safe_dump(data,sort_keys=False),encoding='utf-8')
    for split in SPLITS:
        wanted={e['image'] for e in entries if e['split']==split}
        if {p.name for p in (root/'images'/split).glob('*.jpg')}!=wanted:raise ValueError('Image membership changed')
        if {p.stem for p in (root/'labels'/split).glob('*.txt')}!={Path(n).stem for n in wanted}:raise ValueError('Label membership changed')
    for e in entries:
        if sha256(root/e['dataset_image'])!=e['image_sha256'] or sha256(root/'labels'/e['split']/Path(e['image']).with_suffix('.txt'))!=e['label_sha256']:raise ValueError('Dataset copy changed')
    write_json(root/'validation_report.json',report)
    return root,report


def iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/max(1e-9,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)


def predict_rows(model, paths, config):
    # Some Ultralytics list loaders invent image0.jpg even for path inputs.
    # Predict individual paths and retain the authoritative input filename.
    rows=[]
    for path in paths:
        r=model.predict(source=str(path),imgsz=config['training']['imgsz'],device=config['training']['device'],
                        conf=config['evaluation']['confidence'],iou=config['evaluation']['iou'],verbose=False)[0]
        rows.append({'image':Path(path).name,'detections':[{'class_id':int(b.cls.item()),'confidence':float(b.conf.item()),'bbox_xyxy':b.xyxy[0].tolist()} for b in r.boxes]})
    return rows


def fixed_threshold_counts(predictions, entries, match_iou):
    by_name={e['image']:e for e in entries};groups=defaultdict(lambda:{'tp':0,'fp':0,'fn':0})
    for row in predictions:
        entry=by_name[row['image']];w,h=entry['size'];truth=[]
        for k,cx,cy,bw,bh in entry['boxes']:truth.append((int(k),[(cx-bw/2)*w,(cy-bh/2)*h,(cx+bw/2)*w,(cy+bh/2)*h]))
        matched=set()
        group_names=['all',entry['source_id']]+(['dynamic_new'] if entry['dynamic_frame'] else [])
        for box in sorted(row['detections'],key=lambda d:-d['confidence']):
            candidates=[(iou(box['bbox_xyxy'],b),j) for j,(k,b) in enumerate(truth) if k==box['class_id'] and j not in matched]
            best=max(candidates,default=(0,-1));hit=best[0]>=match_iou
            if hit:matched.add(best[1])
            for g in group_names+[f"class_{box['class_id']}"]:groups[g]['tp' if hit else 'fp']+=1
        for j,(k,b) in enumerate(truth):
            if j not in matched:
                for g in group_names+[f'class_{k}']:groups[g]['fn']+=1
    return dict(groups)


def draw_prediction(path,predictions,target):
    im=Image.open(path).convert('RGB');draw=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',15)
    for b in predictions:
        k=b['class_id'];color='#FFD54A' if k==0 else '#FF647C';x1,y1,x2,y2=b['bbox_xyxy']
        draw.rectangle((x1,y1,x2,y2),outline=color,width=2)
        text=('黄色小蜜蜂玩偶' if k==0 else '白色吉他')+f" {b['confidence']:.2f}"
        l,t,r,bt=draw.textbbox((0,0),text,font=font);y=max(0,y1-24)
        draw.rectangle((x1,y,x1+r-l+8,y+23),fill=color);draw.text((x1+4,y+2-t),text,font=font,fill='#15202b')
    im.save(target,quality=95)


def run_training(config,dataset,validation,config_path,evaluate_existing=False):
    train=dict(config['training']);project=resolve_path(train.pop('project'));run=project/train['name'];weights=resolve_path(train.pop('model'))
    if run.exists() and not evaluate_existing:raise ValueError('Experiment exists; choose a new name')
    if evaluate_existing:
        if not (run/'weights/best.pt').is_file() or not (run/'results.csv').is_file():raise ValueError('Completed checkpoint required for evaluation recovery')
        if (run/'training_config.yaml').read_bytes()!=config_path.read_bytes():raise ValueError('Recovery config differs from the completed training')
    if not weights.is_file():raise ValueError('Local pretrained model missing')
    settings_dir=project/'_ultralytics_settings_v2';settings_dir.mkdir(parents=True,exist_ok=True)
    os.environ.update(YOLO_CONFIG_DIR=str(settings_dir),YOLO_AUTOINSTALL='false',YOLO_OFFLINE='true')
    from ultralytics import YOLO,settings
    import torch
    if not torch.cuda.is_available():raise ValueError('CUDA required')
    settings.update({'weights_dir':str(weights.parent),'runs_dir':str(project),'datasets_dir':str(dataset.parent)})
    protected={p:sha256(PROJECT_ROOT/p) for p in PROTECTED+EXTRA_PROTECTED}
    model=YOLO(str(weights),task=train.pop('task'));start=time.perf_counter()
    def progress(trainer):write_json(Path(trainer.save_dir)/'progress.json',{'status':'training','epoch':trainer.epoch+1,'max_epochs':trainer.epochs,'elapsed_seconds':round(time.perf_counter()-start,1)})
    if not evaluate_existing:
        model.add_callback('on_fit_epoch_end',progress)
        model.train(data=str(dataset/'data.yaml'),project=str(project),exist_ok=False,**train)
        if Path(model.trainer.save_dir).resolve()!=run.resolve():raise ValueError('Unexpected run output')
        elapsed=time.perf_counter()-start;shutil.copy2(config_path,run/'training_config.yaml')
    else:
        with (run/'results.csv').open(newline='',encoding='utf-8') as f:completed=list(csv.DictReader(f))
        elapsed=float(completed[-1]['time'])
    best=YOLO(str(run/'weights/best.pt'))
    metrics=best.val(data=str(dataset/'data.yaml'),split='test',imgsz=train['imgsz'],batch=train['batch'],device=train['device'],workers=train['workers'],project=str(run),name='test_evaluation',plots=True,exist_ok=evaluate_existing)
    entries=[e for e in json.loads((dataset/'split_manifest.json').read_text(encoding='utf-8'))['entries'] if e['split']=='test']
    paths=[dataset/e['dataset_image'] for e in entries];comparison={}
    for name,detector in [('v1',YOLO(str(resolve_path(config['evaluation']['baseline'])))),('v2',best)]:
        predictions=predict_rows(detector,paths,config);write_json(run/f'{name}_test_predictions.json',predictions)
        comparison[name]=fixed_threshold_counts(predictions,entries,config['evaluation']['match_iou'])
        if name=='v2':
            preview=run/'test_predictions_zh';preview.mkdir(exist_ok=evaluate_existing)
            for row,path in zip(predictions,paths):draw_prediction(path,row['detections'],preview/path.name)
        pinned=resolve_path(config['evaluation']['regression_image']);replays=[]
        for repeat in range(2):
            result=predict_rows(detector,[pinned],config)[0]
            result['bee_matches']=[b for b in result['detections'] if b['class_id']==0 and iou(b['bbox_xyxy'],config['evaluation']['regression_bbox'])>=config['evaluation']['match_iou']]
            replays.append(result)
        comparison[name]['pinned_miss_replay']=replays
        draw_prediction(pinned,replays[0]['detections'],run/f'{name}_pinned_replay.jpg')
    write_json(run/'v1_v2_comparison.json',comparison)
    after={p:sha256(PROJECT_ROOT/p) for p in protected}
    if protected!=after:raise ValueError('Protected original artifact changed')
    with (run/'results.csv').open(newline='',encoding='utf-8') as f:epochs=list(csv.DictReader(f))
    summary={'status':'TRAINED_MULTIPOSE_DEVELOPMENT_MODEL','best_weights':str(run/'weights/best.pt'),
             'completed_epochs':len(epochs),'training_seconds':round(elapsed,2),'dataset_validation':validation,
             'test_metrics':{k:float(v) for k,v in metrics.results_dict.items()},'comparison':comparison,
             'confidence':config['evaluation']['confidence'],'match_iou':config['evaluation']['match_iou'],
             'evaluation_scope':BOUNDARY,'independent_video_evaluation':False,'arbitrary_relocation_verified':False,
             'live_model_changed':False,'protected_files_unchanged':True,'protected_sha256':after,
             'evaluation_recovered':evaluate_existing,
             'protection_check_scope':'before_after_evaluation_recovery' if evaluate_existing else 'before_after_training_and_evaluation'}
    write_json(run/'training_summary.json',summary);write_json(run/'progress.json',{'status':'complete','epochs':len(epochs)})
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG);parser.add_argument('--dry-run',action='store_true');parser.add_argument('--evaluate-existing',action='store_true',help='Recover evaluation of the matching completed checkpoint without retraining');args=parser.parse_args()
    config=load_config(args.config);dataset,validation=prepare_dataset(config)
    if args.dry_run:print(json.dumps(validation,ensure_ascii=False,indent=2));return
    summary=run_training(config,dataset,validation,args.config,evaluate_existing=args.evaluate_existing)
    print(json.dumps({'status':summary['status'],'best_weights':summary['best_weights'],'comparison':summary['comparison']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    multiprocessing.freeze_support();main()
