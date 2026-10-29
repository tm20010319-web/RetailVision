"""Protect frozen holdouts and exclude unreviewable source images."""
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/training'))
from train_toy_guitar import sha256
from train_toy_guitar_v3 import prepare_dataset


@pytest.fixture
def supplement(tmp_path):
    base = tmp_path / 'base'
    old = []
    for i, split in enumerate(('train', 'val', 'test')):
        (base / 'images' / split).mkdir(parents=True)
        (base / 'labels' / split).mkdir(parents=True)
        image = base / 'images' / split / f'old_{i}.jpg'
        Image.new('RGB', (100, 100), (i * 50, 10, 10)).save(image)
        label = base / 'labels' / split / f'old_{i}.txt'
        label.write_text('0 .5 .5 .2 .2\n')
        old.append({'image': image.name, 'dataset_image': f'images/{split}/{image.name}',
                    'split': split, 'image_sha256': sha256(image), 'label_sha256': sha256(label),
                    'boxes': [[0, .5, .5, .2, .2]], 'source_id': 'original'})
    (base / 'split_manifest.json').write_text(json.dumps({'entries': old}))
    source = tmp_path / 'source'
    (source / 'images').mkdir(parents=True)
    (source / 'labels').mkdir()
    frames = []
    for n in (1, 2):
        image = source / 'images' / f'frame_{n:04d}.jpg'
        Image.new('RGB', (100, 100), (20, n * 60, 30)).save(image)
        (source / 'labels' / image.with_suffix('.txt').name).write_text('0 .5 .5 .2 .2\n')
        frames.append({'index': n, 'image': image.name, 'image_sha256': sha256(image),
                       'source_pts_seconds': n * 3., 'pose_group': 'new_location',
                       'dynamic_frame': False, 'review_exclusion_reason': None})
    (source / 'annotation_manifest.json').write_text(json.dumps({'classes': {0: 'toy', 1: 'white_guitar'},
        'source_sha256': 'source-video-hash', 'frames': frames}))
    config = {'dataset': {'base': str(base), 'output': str(tmp_path / 'prepared'),
                         'names': {0: 'toy', 1: 'white_guitar'}, 'min_box_pixels': 4,
                         'sources': [{'id': 'relocation', 'annotations': str(source),
                                      'selections': {'train': [[1, 1]], 'test': [[2, 2]]}}]},
              'training': {'imgsz': 640}}
    return config, old, source


def test_old_holdouts_preserved_and_copied_label_tampering_rejected(supplement):
    config, old, _ = supplement
    target, report = prepare_dataset(config)
    prepared = json.loads((target / 'split_manifest.json').read_text())
    assert prepared['entries'][:3] == old
    assert report['v2_images_preserved'] == 3
    heldout = target / 'labels/test/old_2.txt'
    assert sha256(heldout) == old[2]['label_sha256']
    heldout.write_text('1 .5 .5 .2 .2\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        prepare_dataset(config)


def test_ambiguous_source_cannot_enter_training(supplement):
    config, _, source = supplement
    path = source / 'annotation_manifest.json'
    manifest = json.loads(path.read_text())
    manifest['frames'][0]['review_exclusion_reason'] = 'object boundary cannot be determined'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Unreviewable'):
        prepare_dataset(config)
    assert not Path(config['dataset']['output']).exists()


def test_same_video_timestamp_cannot_enter_train_and_test_twice(supplement):
    config, _, _ = supplement
    duplicate = {**config['dataset']['sources'][0], 'id': 'duplicate',
                 'selections': {'test': [[1, 1]]}}
    config['dataset']['sources'].append(duplicate)
    with pytest.raises(ValueError, match='Duplicate source frame'):
        prepare_dataset(config)
