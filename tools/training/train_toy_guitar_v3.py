"""Supplement V2 with reviewed relocation frames and compare fixed-threshold recall.

The old dataset/splits remain frozen. New holdouts are temporal blocks from the
same camera/video; they do not establish arbitrary-placement generalization.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import time

import yaml

from train_toy_guitar import PROJECT_ROOT, PROTECTED, SPLITS, resolve_path, sha256, source_records, write_json
from train_toy_guitar_v2 import fixed_threshold_counts, predict_rows, draw_prediction

DEFAULT_CONFIG = PROJECT_ROOT / 'config/toy_guitar_train_v3.yaml'
SCOPE = 'frozen base holdouts plus temporal blocks from relocation video and optional live capture; static scene similarity and short rear/left-back segments remain; no independent-video or arbitrary-location qualification'


def prepare_dataset(config):
    base = resolve_path(config['dataset']['base'])
    old = json.loads((base / 'split_manifest.json').read_text(encoding='utf-8'))
    entries = list(old['entries'])
    seen = {e['image_sha256'] for e in entries}
    timestamps = set()
    excluded = []
    sources = []
    for source in config['dataset']['sources']:
        root = resolve_path(source['annotations'])
        manifest, records = source_records({**config, 'dataset': {**config['dataset'], 'annotations': str(root)}})
        by_name = {r['image']: r for r in manifest['frames']}
        selected = {}
        for split, intervals in source['selections'].items():
            if split not in SPLITS:
                raise ValueError('Unknown split')
            for start, end in intervals:
                for n in range(start, end + 1):
                    if n in selected:
                        raise ValueError('Overlapping selection')
                    selected[n] = split
        found = set()
        for row in records:
            n = int(Path(row['image']).stem.split('_')[-1])
            meta = by_name[row['image']]
            if n not in selected:
                excluded.append({'source_id': source['id'], 'image': row['image'],
                                 'reason': meta.get('review_exclusion_reason') or 'unselected temporal buffer'})
                continue
            if meta.get('review_exclusion_reason'):
                raise ValueError('Unreviewable annotation selected')
            found.add(n)
            key = (manifest['source_sha256'], row['source_pts_seconds'])
            if key in timestamps or row['image_sha256'] in seen:
                raise ValueError('Duplicate source frame selected')
            timestamps.add(key)
            seen.add(row['image_sha256'])
            split = selected[n]
            name = source['id'] + '_' + row['image']
            entries.append({**row, 'image': name, 'source_image': row['image'],
                            'source_id': source['id'], 'source_root': str(root), 'split': split,
                            'dataset_image': f'images/{split}/{name}',
                            'pose_group': meta['pose_group'], 'dynamic_frame': meta['dynamic_frame']})
        if found != set(selected):
            raise ValueError('Missing selected frame')
        sources.append({'id': source['id'], 'root': str(root),
                        'annotation_manifest_sha256': sha256(root / 'annotation_manifest.json'),
                        'source_video_sha256': manifest['source_sha256']})
    payload = {'base_dataset': str(base), 'base_manifest_sha256': sha256(base / 'split_manifest.json'),
               'policy': 'all V2 images/labels/splits preserved; explicit new temporal blocks and buffers',
               'evaluation_scope': SCOPE, 'sources': sources, 'entries': entries,
               'excluded': excluded, 'names': config['dataset']['names']}
    target = resolve_path(config['dataset']['output'])
    data = {'path': target.as_posix(), 'train': 'images/train', 'val': 'images/val',
            'test': 'images/test', 'nc': 2, 'names': config['dataset']['names']}
    if target.exists():
        if json.loads((target / 'split_manifest.json').read_text(encoding='utf-8')) != json.loads(json.dumps(payload)):
            raise ValueError('Dataset exists with different content; choose a new output')
        if yaml.safe_load((target / 'data.yaml').read_text(encoding='utf-8')) != data:
            raise ValueError('Dataset YAML changed')
    else:
        for split in SPLITS:
            (target / 'images' / split).mkdir(parents=True, exist_ok=False)
            (target / 'labels' / split).mkdir(parents=True, exist_ok=False)
        old_names = {e['image'] for e in old['entries']}
        for e in entries:
            if e['image'] in old_names:
                image = base / e['dataset_image']
                label = base / 'labels' / e['split'] / Path(e['image']).with_suffix('.txt')
            else:
                image = Path(e['source_root']) / 'images' / e['source_image']
                label = Path(e['source_root']) / 'labels' / Path(e['source_image']).with_suffix('.txt')
            shutil.copy2(image, target / e['dataset_image'])
            shutil.copy2(label, target / 'labels' / e['split'] / Path(e['image']).with_suffix('.txt'))
        write_json(target / 'split_manifest.json', payload)
        (target / 'data.yaml').write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
    for split in SPLITS:
        wanted = {e['image'] for e in entries if e['split'] == split}
        if {p.name for p in (target / 'images' / split).glob('*.jpg')} != wanted:
            raise ValueError('Image membership changed')
        if {p.stem for p in (target / 'labels' / split).glob('*.txt')} != {Path(p).stem for p in wanted}:
            raise ValueError('Label membership changed')
    for e in entries:
        if sha256(target / e['dataset_image']) != e['image_sha256'] or sha256(target / 'labels' / e['split'] / Path(e['image']).with_suffix('.txt')) != e['label_sha256']:
            raise ValueError('Dataset hash mismatch')
    report = {'images': len(entries), 'boxes': sum(len(e['boxes']) for e in entries),
              'base_images_preserved': len(old['entries']),
              'v2_images_preserved': sum(not e['source_id'].startswith('relocation') for e in old['entries']),
              'v2_splits_and_labels_preserved': True,
              'new_images': len(entries) - len(old['entries']), 'excluded': len(excluded),
              'evaluation_scope': SCOPE, 'independent_video_evaluation': False, 'splits': {}}
    for split in SPLITS:
        group = [e for e in entries if e['split'] == split]
        report['splits'][split] = {'images': len(group),
                                  'sources': dict(Counter(e['source_id'] for e in group)),
                                  'class_counts': dict(Counter(int(b[0]) for e in group for b in e['boxes']))}
    write_json(target / 'validation_report.json', report)
    return target, report


def evaluate(config, run, dataset):
    from ultralytics import YOLO
    entries = [e for e in json.loads((dataset / 'split_manifest.json').read_text(encoding='utf-8'))['entries'] if e['split'] == 'test']
    paths = [dataset / e['dataset_image'] for e in entries]
    comparison = {}
    audit_path = resolve_path('runs/toy_guitar/multipose_v2_20261021/heldout_annotation_audit.json')
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    audited = deepcopy([e for e in entries if not e['source_id'].startswith('relocation')])
    corrected = [e for e in audited if e['image_sha256'] == audit['image_sha256']]
    if len(corrected) != 1:
        raise ValueError('Known audited holdout is missing')
    entry = corrected[0]
    w, h = entry['size']
    x1, y1, x2, y2 = audit['visible_bbox_xyxy']
    entry['boxes'].append([audit['added_class_id'], (x1+x2)/2/w, (y1+y2)/2/h, (x2-x1)/w, (y2-y1)/h])
    write_json(run / 'heldout_annotation_audit_reference.json', {'source_audit': str(audit_path),
               'audit_sha256': sha256(audit_path), 'reason': audit['reason'],
               'frozen_dataset_unchanged': True, 'correction_known_before_v3_training': True})
    for name, weights in [('v2', resolve_path(config['evaluation']['baseline'])), ('v3', run / 'weights/best.pt')]:
        predictions = predict_rows(YOLO(str(weights)), paths, config)
        write_json(run / f'{name}_test_predictions.json', predictions)
        groups = {'frozen_v2': [e for e in entries if not e['source_id'].startswith('relocation')],
                  'frozen_v2_audited': audited,
                  'new_temporal_holdout': [e for e in entries if e['source_id'] == 'relocation'],
                  'live_capture_temporal_holdout': [e for e in entries if e['source_id'] == 'relocation_live']}
        groups.update({f'new_pose_{pose}': [e for e in entries if e['source_id'].startswith('relocation') and e['pose_group'] == pose]
                       for pose in sorted({e['pose_group'] for e in entries if e['source_id'].startswith('relocation')})})
        results = {}
        for key, selected in groups.items():
            names = {e['image'] for e in selected}
            counts = fixed_threshold_counts([p for p in predictions if p['image'] in names], selected, config['evaluation']['match_iou'])
            for class_id in (0, 1):
                c = counts.setdefault(f'class_{class_id}', {'tp': 0, 'fp': 0, 'fn': 0})
                c['recall'] = c['tp'] / (c['tp'] + c['fn']) if c['tp'] + c['fn'] else None
                c['precision'] = c['tp'] / (c['tp'] + c['fp']) if c['tp'] + c['fp'] else None
            results[key] = counts
        comparison[name] = results
        preview = run / f'{name}_test_predictions_zh'
        preview.mkdir(exist_ok=True)
        for row, path in zip(predictions, paths):
            draw_prediction(path, row['detections'], preview / path.name)
    write_json(run / 'v2_v3_comparison.json', comparison)
    return comparison


def run_training(config, dataset, report, config_path, evaluate_existing=False):
    train = dict(config['training'])
    project = resolve_path(train.pop('project'))
    run = project / train['name']
    weights = resolve_path(train.pop('model'))
    task = train.pop('task')
    protected_paths = list(PROTECTED) + ['config/toy_guitar_train_v2.yaml', 'config/toy_guitar_live_v2.yaml',
                                        'runs/toy_guitar/multipose_v2_20261021/weights/best.pt',
                                        'data/toy_guitar_dataset_v2_20261021/split_manifest.json']
    protected = {p: sha256(PROJECT_ROOT / p) for p in protected_paths}
    os.environ.update(YOLO_CONFIG_DIR=str(project / '_ultralytics_settings_v3'), YOLO_AUTOINSTALL='false', YOLO_OFFLINE='true')
    from ultralytics import YOLO
    import torch
    if not torch.cuda.is_available():
        raise ValueError('CUDA required')
    start = time.perf_counter()
    if not evaluate_existing:
        if run.exists():
            raise ValueError('Run exists; select a new experiment name')
        model = YOLO(str(weights), task=task)
        def progress(trainer):
            write_json(Path(trainer.save_dir) / 'progress.json', {'status': 'training', 'epoch': trainer.epoch + 1,
                       'max_epochs': trainer.epochs, 'elapsed_seconds': round(time.perf_counter() - start, 1)})
        model.add_callback('on_fit_epoch_end', progress)
        model.train(data=str(dataset / 'data.yaml'), project=str(project), exist_ok=False, **train)
        if Path(model.trainer.save_dir).resolve() != run.resolve():
            raise ValueError('Unexpected training output')
        shutil.copy2(config_path, run / 'training_config.yaml')
    elif (run / 'training_config.yaml').read_bytes() != config_path.read_bytes():
        raise ValueError('Evaluation recovery config differs')
    comparison = evaluate(config, run, dataset)
    if protected != {p: sha256(PROJECT_ROOT / p) for p in protected_paths}:
        raise ValueError('Protected V2/staff artifact changed')
    with (run / 'results.csv').open(newline='', encoding='utf-8') as f:
        epochs = list(csv.DictReader(f))
    summary = {'status': 'TRAINED_RELOCATION_DEVELOPMENT_MODEL', 'weights': str(run / 'weights/best.pt'),
               'weights_sha256': sha256(run / 'weights/best.pt'), 'epochs': len(epochs),
               'training_seconds': float(epochs[-1]['time']), 'dataset': report, 'comparison': comparison,
               'confidence': config['evaluation']['confidence'], 'match_iou': config['evaluation']['match_iou'],
               'evaluation_scope': SCOPE, 'protected_sha256': protected, 'protected_files_unchanged': True,
               'live_validation': 'pending', 'independent_video_evaluation': False}
    write_json(run / 'training_summary.json', summary)
    write_json(run / 'progress.json', {'status': 'complete', 'epochs': len(epochs)})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--evaluate-existing', action='store_true')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
    if config['dataset']['names'] != {0: 'toy', 1: 'white_guitar'}:
        raise ValueError('Keep class IDs')
    dataset, report = prepare_dataset(config)
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        result = run_training(config, dataset, report, args.config, args.evaluate_existing)
        print(json.dumps({'status': result['status'], 'weights': result['weights'], 'comparison': result['comparison']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
