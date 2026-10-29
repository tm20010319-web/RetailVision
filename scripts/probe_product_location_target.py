"""Produce an unconfirmed single-target SAM3 proposal from an exact source image.

This diagnostic does not start continuous tracking or publish a location result.
Input box and points use pixels in the original full source image.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.sam31_real_adapter import create_external_sam31_adapter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--bbox', nargs=4, type=int, required=True)
    parser.add_argument('--positive', nargs=2, type=int, action='append', required=True)
    parser.add_argument('--negative', nargs=2, type=int, action='append', default=[])
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    source_bytes = args.image.read_bytes()
    source = cv2.imdecode(np.frombuffer(source_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if source is None:
        parser.error('source image cannot be decoded')
    source_height, source_width = source.shape[:2]
    x1, y1, x2, y2 = args.bbox
    if not (0 <= x1 < x2 <= source_width and 0 <= y1 < y2 <= source_height):
        parser.error('bbox must fit the original source image')
    for x, y in args.positive + args.negative:
        if not (0 <= x < source_width and 0 <= y < source_height):
            parser.error('points must fit the original source image')
    if not all(x1 <= x < x2 and y1 <= y < y2 for x, y in args.positive):
        parser.error('positive points must be inside the target box')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    width, height = 1280, 720
    frame = cv2.resize(source, (width, height), interpolation=cv2.INTER_AREA)

    def point(value):
        return (min(width - 1, round(value[0] * width / source_width)),
                min(height - 1, round(value[1] * height / source_height)))

    bbox = (*point((x1, y1)), *point((x2, y2)))
    started = time.monotonic()
    print('Loading restored SAM3.1 predictor on GPU...', flush=True)
    log = (args.output_dir / 'model-runtime.log').open('w', encoding='utf-8')
    try:
        with redirect_stdout(log), redirect_stderr(log):
            adapter = create_external_sam31_adapter(
                frame=frame, runtime_root=args.runtime_root, checkpoint_path=args.checkpoint,
            )
    except BaseException:
        log.close()
        raise
    try:
        print('Running the existing bbox -> point refinement adapter...', flush=True)
        result = adapter.add_instance_prompt(
            frame=frame, frame_index=0, bbox=bbox,
            points=tuple(point(value) for value in args.positive + args.negative),
            point_labels=(1,) * len(args.positive) + (0,) * len(args.negative), obj_id=1,
        )
        metadata = {
            'status': 'requires_operator_confirmation',
            'product_key': 'white_guitar_1',
            'source_image': str(args.image.resolve()),
            'source_image_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'source_size': [source_width, source_height],
            'inference_size': [width, height],
            'coordinate_transform': 'full_frame_resize',
            'prompt_bbox_source_pixels': args.bbox,
            'positive_points_source_pixels': args.positive,
            'negative_points_source_pixels': args.negative,
            'model_checkpoint': str(args.checkpoint.resolve()),
            'model_runtime_log': str(args.output_dir / 'model-runtime.log'),
            'candidate_count': len(result.masks),
            'diagnostics': dict(result.diagnostics),
            'limitations': ['One saved source frame only; no continuous tracking or current-location claim.'],
        }
        if len(result.masks) != 1 or not np.any(result.masks[0]):
            metadata['status'] = 'proposal_rejected'
            (args.output_dir / 'proposal.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            print(json.dumps({'status': metadata['status'], 'diagnostics': metadata['diagnostics']}), flush=True)
            return 2
        mask = np.asarray(result.masks[0], dtype=bool)
        if mask.shape != (height, width):
            raise ValueError('SAM mask does not match the explicit inference coordinate space')
        ys, xs = np.nonzero(mask)
        target_bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        metadata['bbox'] = [target_bbox[0] / width, target_bbox[1] / height,
                            target_bbox[2] / width, target_bbox[3] / height]
        metadata['coordinate_space'] = 'normalized_full_frame'
        metadata['mask_pixels'] = int(mask.sum())
        metadata['elapsed_seconds'] = round(time.monotonic() - started, 3)
        preview = frame.copy()
        green = np.zeros_like(preview)
        green[:] = (40, 230, 40)
        preview[mask] = (preview[mask] * 0.55 + green[mask] * 0.45).astype(np.uint8)
        bx1, by1, bx2, by2 = target_bbox
        cv2.rectangle(preview, (bx1, by1), (bx2 - 1, by2 - 1), (40, 230, 40), 2)
        cv2.putText(preview, 'white_guitar_1 | UNCONFIRMED PROPOSAL', (10, height - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (40, 230, 40), 2)
        left, top, right, bottom = max(0, bx1 - 20), max(0, by1 - 20), min(width, bx2 + 20), min(height, by2 + 20)
        detail = cv2.resize(preview[top:bottom, left:right], None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
        metadata['detail_crop_inference_pixels'] = [left, top, right, bottom]
        for filename, image in [('proposal.jpg', preview), ('proposal-detail.jpg', detail),
                                ('mask.png', mask.astype(np.uint8) * 255)]:
            if not cv2.imwrite(str(args.output_dir / filename), image):
                raise RuntimeError('could not save diagnostic image')
        (args.output_dir / 'proposal.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print(json.dumps({'status': metadata['status'], 'elapsed_seconds': metadata['elapsed_seconds'],
                          'mask_pixels': metadata['mask_pixels'], 'preview': str(args.output_dir / 'proposal.jpg')}), flush=True)
        return 0
    finally:
        try:
            adapter.close()
        finally:
            log.close()


if __name__ == '__main__':
    raise SystemExit(main())
