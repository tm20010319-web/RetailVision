"""Behavior replay preserves evidence and never claims fresh model inference."""
import json
from pathlib import Path
import cv2
import numpy as np
import pytest

from scripts import vlx_mask_refinement as refinement


def make_evidence(root,monkeypatch):
    source=root/'source';source.mkdir();(source/'frames').mkdir()
    data={'classes':[0,1,2],'users':[], 'recorded_run':'immutable-vlx-run',
          'recorded_trace_sha256':'b'*64,
          'original_manifest':{'config':{'interaction':{},'font_path':'unused','preview_fps':5},
                               'timeline':{'duration_seconds':1},'source_sha256':'a'*64,'writes_database':False},
          'rows':[{'timestamp_seconds':0,'people':[]},{'timestamp_seconds':.2,'people':[]}]}
    (source/'input.json').write_text(json.dumps(data),encoding='utf-8')
    group=source/'masks_0_1_2';group.mkdir()
    (group/'geometry.json').write_text(json.dumps({'classes':[0,1,2],'prompt':'stuffed toy','rows':[{},{}]}))
    (group/'status.json').write_text(json.dumps({'status':'completed'}))
    for index in range(2):cv2.imwrite(str(source/'frames'/f'{index:06d}.jpg'),np.zeros((20,20,3),np.uint8))
    class Overlay:
        def __init__(self,*args):pass
        def render(self,frame,*args,**kwargs):return frame
    class Writer:
        def __init__(self,*args):pass
        def push(self,*args):pass
        def finish(self,*args):pass
        def abort(self):pass
    monkeypatch.setattr(refinement,'Overlay',Overlay)
    monkeypatch.setattr(refinement,'PreviewWriter',Writer)
    return source


def test_replay_records_old_evidence_and_effective_new_config(tmp_path,monkeypatch):
    source=make_evidence(tmp_path,monkeypatch)
    before={p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()}
    output=tmp_path/'replay'
    refinement.render(output,reuse_refinement_from=source)
    manifest=refinement.read(output/'manifest.json');provenance=manifest['evidence_provenance']
    assert provenance['fresh_vlx_inference'] is False
    assert provenance['fresh_sam31_tracking'] is False
    assert provenance['recomputed']==['interaction_decisions','video_overlay']
    assert provenance['recorded_refinement']==str(source.resolve())
    assert manifest['config']['interaction']['occluded_hold_grace_seconds']==15
    assert len(manifest['targets'])==len(refinement.read(output/'result.json')['by_object'])==3
    assert before=={p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()}


def test_replay_refuses_existing_output(tmp_path,monkeypatch):
    source=make_evidence(tmp_path,monkeypatch);out=tmp_path/'existing';out.mkdir()
    marker=out/'keep.txt';marker.write_text('keep')
    with pytest.raises(FileExistsError):refinement.render(out,reuse_refinement_from=source)
    assert marker.read_text()=='keep'


def test_replay_refuses_incomplete_geometry_before_creating_output(tmp_path,monkeypatch):
    source=make_evidence(tmp_path,monkeypatch)
    (source/'masks_0_1_2/status.json').write_text(json.dumps({'status':'tracking'}))
    out=tmp_path/'output'
    with pytest.raises(ValueError,match='incomplete'):refinement.render(out,reuse_refinement_from=source)
    assert not out.exists()
