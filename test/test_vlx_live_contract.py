import pytest

from scripts.run_toy_guitar_vlx_live import detections_from_output


def region(label, box):
    return dict(label=label, **dict(zip(('xmin', 'ymin', 'xmax', 'ymax'), box)))


def test_live_absence_is_valid_and_next_frame_does_not_retain_boxes():
    detected = detections_from_output({'result_bbox_list': [region('white electric guitar', [2, 3, 40, 60])]}, (80, 80))
    assert [d['class_id'] for d in detected] == [1]
    assert detections_from_output({'result_bbox_list': []}, (80, 80)) == []


def test_overlapping_different_targets_are_not_suppressed():
    rows = [region('bee plush toy', [2, 3, 40, 60]), region('white electric guitar', [3, 4, 41, 61])]
    assert len(detections_from_output({'result_bbox_list': rows}, (80, 80))) == 2


@pytest.mark.parametrize('box', [[-1, 2, 10, 20], [1, 2, 90, 20], [1, 2, float('nan'), 20]])
def test_bad_geometry_cannot_reach_live_overlay(box):
    with pytest.raises(ValueError):
        detections_from_output({'result_bbox_list': [region('white electric guitar', box)]}, (80, 80))


def test_guitar_identity_comes_from_appearance_category_even_if_pixel_size_is_reversed():
    rows = [region('white electric guitar', [2, 3, 12, 23]),
            region('white ukulele', [20, 10, 75, 70])]
    result = detections_from_output({'result_bbox_list': rows}, (80, 80))
    assert [(d['class_id'], d['label']) for d in result] == [(1, '大吉他'), (2, '小吉他')]
    assert detections_from_output({'result_bbox_list': [region('white guitar', [2, 3, 40, 60])]}, (80, 80)) == []
