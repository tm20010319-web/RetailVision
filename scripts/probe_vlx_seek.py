"""Actual VLX-Seek zero-shot trial with automatic WeDetect proposals.

NF4 language quantization and SDPA are local compatibility choices for the
16GB Windows GPU, not the official unquantized benchmark configuration.
Reference labels are used ONLY after inference for evaluation, never as inputs.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import yaml
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def load_quantized_worker(model_path):
    import torch
    from transformers import AutoTokenizer,BitsAndBytesConfig
    from transformers import modeling_utils
    from vlx_seek.models.vlx_seek_1_5 import VLXSeek1_5ForCausalLM,VLXSeek1_5Config
    from vlx_seek_worker import VLXSeekWorker
    quantization=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,llm_int8_skip_modules=['lm_head','model.vision_tower','model.vision_tower_aux',
            'model.object_vp_extractor','model.mm_projector','model.mm_projector_aux'])
    tokenizer=AutoTokenizer.from_pretrained(model_path,use_fast=True,local_files_only=True)
    config=VLXSeek1_5Config.from_pretrained(model_path,local_files_only=True)
    config.name_or_path=model_path  # Use checkpoint-contained vision weights.
    # Windows mmap crashes inside torch_cpu.dll when the full custom model
    # loader reads this 20GB checkpoint. Safetensors' supported pread backend
    # avoids the mapping and retains lazy per-tensor reads. Scope to this load.
    safe_open=modeling_utils.safe_open
    def pread_open(*args,**kwargs):
        kwargs['backend']='pread'
        return safe_open(*args,**kwargs)
    if os.name=='nt':modeling_utils.safe_open=pread_open
    try:
        model=VLXSeek1_5ForCausalLM.from_pretrained(model_path,config=config,local_files_only=True,low_cpu_mem_usage=True,
            device_map={'':'cuda:0'},dtype=torch.bfloat16,attn_implementation='sdpa',quantization_config=quantization)
    finally:
        modeling_utils.safe_open=safe_open
    model.eval()
    unexpected=[name for name,module in model.named_modules()
        if 'Linear4bit' in type(module).__name__ and not name.startswith('model.language_model.')]
    if unexpected:raise RuntimeError(f'Non-language modules were unexpectedly quantized: {unexpected[:5]}')
    vt=model.get_vision_tower();aux=model.get_vision_tower_aux()
    vt.to(device='cuda:0',dtype=torch.bfloat16);aux.to(device='cuda:0',dtype=torch.bfloat16)
    worker=VLXSeekWorker.__new__(VLXSeekWorker);worker.device=torch.device('cuda:0');worker.model=model
    worker.tokenizer=tokenizer;worker.image_processor=vt.image_processor;worker.image_processor_aux=aux.image_processor
    return worker


def iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/max(1e-9,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)


def render(image,boxes,path):
    im=image.copy();draw=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',15)
    for box in boxes:
        coords=[box[k] for k in ('xmin','ymin','xmax','ymax')]
        draw.rectangle(coords,outline='#ffd54a',width=2)
        draw.text((coords[0],max(0,coords[1]-22)),box.get('label',''),font=font,fill='#ff3322')
    im.save(path)


def initialization_prompts(output, image_size):
    """Resolve explicit target nouns; reject absent/ambiguous region references."""
    aliases={'bee plush toy':'yellow_bee_toy','yellow bee plush toy':'yellow_bee_toy','plush toy':'yellow_bee_toy',
             'white guitar':'white_guitar','guitar':'white_guitar'}
    selected={};width,height=image_size
    for item in output['result_bbox_list']:
        key=aliases.get(item['label'].strip().casefold())
        if key is None:continue
        box=[float(item[k]) for k in ('xmin','ymin','xmax','ymax')]
        if not (0<=box[0]<box[2]<=width and 0<=box[1]<box[3]<=height):raise ValueError('VLX returned invalid region geometry')
        if key in selected:
            if iou(box,selected[key])<.7:raise ValueError(f'VLX returned ambiguous multiple regions for {key}')
            continue
        selected[key]=box
    if set(selected)!={'yellow_bee_toy','white_guitar'}:raise ValueError('VLX did not identify both requested targets')
    # The bee can stand in front of the guitar neck: overlapping rectangles
    # do not imply identical targets. Reject essentially the same region only.
    if iou(selected['yellow_bee_toy'],selected['white_guitar'])>.8:raise ValueError('VLX target regions conflict')
    prompts={}
    for obj_id,key in enumerate(('yellow_bee_toy','white_guitar')):
        x1,y1,x2,y2=selected[key]
        prompts[key]={'obj_id':obj_id,'bbox_xyxy':[int(x1),int(y1),int(x2+0.999),int(y2+0.999)],
                      'points':[[round((x1+x2)/2),round((y1+y2)/2)]],'point_labels':[1]}
    return prompts


def ground_image(args,config):
    import torch
    from inference import load_wedetect_proposals
    image=Image.open(args.ground_image).convert('RGB');start=time.perf_counter()
    boxes=load_wedetect_proposals(image,config['detector_checkpoint'],device='cuda:0')
    gc.collect();torch.cuda.empty_cache();worker=load_quantized_worker(config['model_path'])
    result=worker.run_task(image,'detection',config['queries'][0],lang='en',bbox_list=boxes,temperature=0.,max_new_tokens=config['max_new_tokens'])
    args.proposal_output.parent.mkdir(parents=True,exist_ok=True)
    write_json(args.proposal_output.with_suffix('.raw.json'),{'output':result,'query':config['queries'][0],'automatic_proposals':boxes})
    prompts=initialization_prompts(result,image.size)
    write_json(args.proposal_output,{'method':'VLX-Seek_NF4_with_automatic_WeDetect_proposals','prompts':prompts,
        'query':config['queries'][0],'source_sha256':hashlib.sha256(args.ground_image.read_bytes()).hexdigest(),
        'seconds_including_load':time.perf_counter()-start,'output':result,'model_revision':config['model_revision']})
    render(image,result['result_bbox_list'],args.proposal_output.with_suffix('.jpg'))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,default=ROOT/'config/vlx_seek_trial.yaml')
    parser.add_argument('--case-limit',type=int);parser.add_argument('--prepare-proposals',action='store_true');parser.add_argument('--runtime',type=Path)
    parser.add_argument('--ground-image',type=Path);parser.add_argument('--proposal-output',type=Path)
    args=parser.parse_args();config=yaml.safe_load(args.config.read_text(encoding='utf-8'))
    sys.path.insert(0,config['external_repository']);os.environ['HF_HUB_OFFLINE']='1'
    import torch
    from inference import load_wedetect_proposals
    if args.ground_image:
        if args.proposal_output is None:parser.error('--ground-image requires --proposal-output')
        ground_image(args,config);return
    run=args.runtime or Path(config['runtime_root'])/datetime.now().strftime('trial_%Y%m%d_%H%M%S')
    run.mkdir(parents=True,exist_ok=True)
    cases=config['cases'][:args.case_limit] if args.case_limit else config['cases'];prepared=[]
    try:
        for case in cases:
            write_json(run/'progress.json',{'status':'automatic_proposals','case':case['id']})
            image=Image.open(case['image']).convert('RGB');start=time.perf_counter()
            boxes=load_wedetect_proposals(image,config['detector_checkpoint'],device='cuda:0')
            proposal_seconds=time.perf_counter()-start
            row={'case_id':case['id'],'source_image':case['image'],'source_sha256':hashlib.sha256(Path(case['image']).read_bytes()).hexdigest(),
                'boxes':boxes,'proposal_seconds':proposal_seconds,'proposal_source':'unmodified_official_WeDetect_100_automatic',
                'reference_proposal_recall':{label:max((iou(b,ref) for b in boxes),default=0) for label,ref in case.get('reference_boxes',{}).items()}}
            write_json(run/f'{case["id"]}_proposals.json',row);prepared.append((case,image,row))
            gc.collect();torch.cuda.empty_cache()
        if args.prepare_proposals:print(str(run));return
        write_json(run/'progress.json',{'status':'loading_vlx_nf4'})
        torch.cuda.reset_peak_memory_stats();start=time.perf_counter();worker=load_quantized_worker(config['model_path']);load_seconds=time.perf_counter()-start
        results=[]
        for case,image,proposals in prepared:
            for index,query in enumerate(config['queries']):
                write_json(run/'progress.json',{'status':'vlx_inference','case':case['id'],'query':query})
                torch.cuda.synchronize();start=time.perf_counter()
                output=worker.run_task(image,'detection',query,lang='en',bbox_list=proposals['boxes'],temperature=0.,max_new_tokens=config['max_new_tokens'])
                torch.cuda.synchronize();elapsed=time.perf_counter()-start
                # Reference geometry is inspected only AFTER the actual model output.
                predicted=output['result_bbox_list']
                evaluation={label:max((iou([b[k] for k in ('xmin','ymin','xmax','ymax')],ref)
                    for b in predicted if b['label'].strip().casefold()==label),default=0.)
                    for label,ref in case.get('reference_boxes',{}).items()} if index==0 else {'absent_query_returned_boxes':len(predicted)}
                row={'case_id':case['id'],'query':query,'seconds':elapsed,'output':output,'proposal_seconds':proposals['proposal_seconds'],
                     'post_inference_reference_evaluation':evaluation}
                results.append(row);write_json(run/f'{case["id"]}_query_{index}.json',row)
                render(image,output['result_bbox_list'],run/f'{case["id"]}_query_{index}.jpg')
        summary={'status':'ACTUAL_VLX_INFERENCE_COMPLETE','precision':config['precision'],'model_load_seconds':load_seconds,
                 'peak_vram_bytes':torch.cuda.max_memory_allocated(),'quantized_modules':sum('Linear4bit' in type(m).__name__ for m in worker.model.modules()),
                 'automatic_proposals_only':True,'manual_boxes_supplied_to_model':False,'separate_tracking_required':True,'results':results}
        write_json(run/'summary.json',summary);write_json(run/'progress.json',{'status':'complete'});print(str(run))
    except Exception as e:
        write_json(run/'failure.json',{'status':'failed','type':type(e).__name__,'detail':str(e)});raise


if __name__=='__main__':main()
