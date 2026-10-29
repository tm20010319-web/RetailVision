"""Run VLX grounding, SAM instance refinement and timed video export in order."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--video',type=Path,help='New recording: run VLX and pose first')
    source.add_argument('--recorded-run',type=Path,help='Reuse a saved VLX/pose run, explicitly recorded in provenance')
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=ROOT/'config/vlx_interaction_refined.yaml')
    parser.add_argument('--classes',help='Fixed target indices; default: targets confidently present in the first VLX observation')
    parser.add_argument('--tracking-chunk-size',type=int,default=150,
                        help='SAM tracking window samples (default 150); large windows need more GPU memory')
    args=parser.parse_args()
    if args.tracking_chunk_size<2:parser.error('tracking chunk size must be at least 2')
    config=yaml.safe_load(args.config.read_text(encoding='utf-8'))
    output=args.output_dir.resolve()
    if output.exists():parser.error('output directory already exists; preserve previous evidence')
    def run(python,script,*arguments):
        subprocess.run([python,str(ROOT/'scripts'/script),*[str(a) for a in arguments]],cwd=ROOT,check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    recorded=args.recorded_run
    if args.video:
        recorded=output.with_name(output.name+'_grounding')
        interaction=Path(config['interaction_config'])
        if not interaction.is_absolute():interaction=ROOT/interaction
        run(config['vlx_python'],'run_vlx_interaction_video.py','--video',args.video.resolve(),
            '--config',interaction,'--output-dir',recorded,'--no-preview')
    if args.classes:
        classes=sorted(set(int(v) for v in args.classes.split(',')))
    else:
        with (recorded/'observations.jsonl').open(encoding='utf-8') as stream:
            first=json.loads(next(stream))
        classes=sorted(d['class_id'] for d in first['diagnostics']['recognition']['selected'])
    if not classes or any(i<0 or i>5 for i in classes):parser.error('no valid fixed target classes')
    run(config['vlx_python'],'vlx_mask_refinement.py','prepare','--recorded-run',recorded.resolve(),
        '--output-dir',output,'--classes',','.join(map(str,classes)))
    groups=[([i for i in classes if i!=4],'toy' if 3 in classes or 5 in classes else 'stuffed toy'),
            ([4] if 4 in classes else [],'guitar')]
    for group,prompt in groups:
        if group:
            run(config['sam_python'],'vlx_mask_refinement.py','ground' if group==[4] else 'track','--output-dir',output,
                '--classes',','.join(map(str,group)),'--prompt',prompt,
                '--checkpoint',config['sam_checkpoint'],'--sam-repository',config['sam_repository'],
                *(['--chunk-size',args.tracking_chunk_size] if group!=[4] else []))
    run(config['vlx_python'],'vlx_mask_refinement.py','render','--output-dir',output,
        '--user-missing-grace-seconds',config['user_missing_grace_seconds'])
    print(str(output/'annotated.mp4'))


if __name__=='__main__':main()
