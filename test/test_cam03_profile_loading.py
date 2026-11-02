"""Configured profile loading is distinct from camera qualification."""
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

from _test_bootstrap import ensure_project_root_on_path
ensure_project_root_on_path()
from scripts.audit_cam03_identity_readiness import _load_configured_profiles
from scripts.qualify_p6s_device_fingerprint import _profile
from scripts.run_dpi01e2_cam03_offline_member_binding import (
    _formal_camera_configuration, resolve_configured_profile, build_recognition_runtime,
)
from agent.offline.offline_recognition_provider import DurablePushInboxRecognitionProvider
from agent.offline.offline_identity_association import OfflineSpatialGeometry


def test_configured_existing_profile_is_actually_loaded(tmp_path):
    # Reuse existing fixture construction, not a fabricated cam03 verification.
    profile = _profile('test-policy-hash')
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(asdict(profile)), encoding='utf-8')
    config = SimpleNamespace(capability_profile_path=str(path), capability_profile_id=profile.profile_id)
    with patch.dict('os.environ', {'RETAILVISION_CAPABILITY_PROFILE_PATH':str(path)}):
        profiles, reason = _load_configured_profiles(config)
    assert reason is None
    assert profiles == (profile,)


def configured(tmp_path):
    profile = _profile('test-policy-hash')
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(asdict(profile)), encoding='utf-8')
    config = SimpleNamespace(capability_profile_path=str(path), capability_profile_id=profile.profile_id,
                             available=True,device_id='cam02',evidence_mode='durable_push_inbox')
    metadata = {name:dict(normalized_value=value,source='device_push_metadata',retrieved_at='2026-09-07T03:00:00Z')
                for name,value in dict(device_serial=profile.device_serial,device_model=profile.device_model,
                                       firmware_version=profile.firmware_versions[0]).items()}
    return profile, path, config, metadata


@pytest.mark.parametrize('mutation, expected',[
    ('missing_path','profile_path_not_configured'),
    ('missing_file','profile_file_not_found'),
    ('summary','profile_schema_invalid'),
    ('tampered','profile_hash_invalid'),
    ('different_id','configured_profile_id_mismatch'),
    ('different_mode','configured_profile_evidence_mode_mismatch'),
    ('malformed','profile_file_parse_failed'),
])
def test_failed_loading_has_precise_reason(tmp_path,mutation,expected):
    profile,path,config,metadata = configured(tmp_path)
    if mutation == 'missing_path': config.capability_profile_path = None
    elif mutation == 'missing_file': config.capability_profile_path = str(tmp_path/'absent.json')
    elif mutation == 'summary': path.write_text(json.dumps({'profile_snapshot':{'profile_id':profile.profile_id}}))
    elif mutation == 'tampered':
        payload = asdict(profile); payload['device_serial'] = 'different-device'
        path.write_text(json.dumps(payload))
    elif mutation == 'different_id': config.capability_profile_id = 'p6s_cam03_offline_identity'
    elif mutation == 'different_mode': config.evidence_mode = 'historical_query'
    else: path.write_text('{bad json')
    profiles,reason = _load_configured_profiles(config)
    assert profiles == () and reason == expected
    with patch('scripts.run_dpi01e2_cam03_offline_member_binding.P6SCapabilityProfileResolver') as resolver:
        result = resolve_configured_profile(config,metadata)
    resolver.assert_not_called()  # Missing config is NOT an empty resolver search.
    assert result.reason == expected


def test_existing_resolver_checks_same_model_different_serial_and_geometry(tmp_path):
    profile,path,config,metadata = configured(tmp_path)
    result = resolve_configured_profile(config,metadata)
    assert result.status == 'resolved'
    assert result.production_eligibility_reason(device_id='cam02') is None
    assert result.runtime_geometry_reason(source_frame_size=(1920,1088),target_frame_size=(800,448)) is None
    assert result.runtime_geometry_reason(source_frame_size=(1920,1088),target_frame_size=(1280,720)) == 'face_recording_geometry_runtime_mismatch'
    config.device_id='cam03'
    metadata['device_serial']['normalized_value']='different-cam03-serial'
    result = resolve_configured_profile(config,metadata)
    assert result.reason == 'serial_scope_mismatch'
    with pytest.raises(RuntimeError,match='serial_scope_mismatch'):
        build_recognition_runtime(config,profile_resolution=result,historical_identity_resolver=Mock(),track_supplier=Mock())


def test_factory_passes_existing_profile_geometry_into_existing_runtime(tmp_path):
    profile,path,config,metadata = configured(tmp_path)
    resolution=resolve_configured_profile(config,metadata)
    sessions=Mock(); tracks=Mock()
    runtime=build_recognition_runtime(config,profile_resolution=resolution,historical_identity_resolver=Mock(),
                                      track_supplier=tracks,session_factory=sessions,push_coverage_store=Mock())
    assert isinstance(runtime.provider,DurablePushInboxRecognitionProvider)
    assert runtime.provider.profile_resolution is resolution
    assert runtime.track_supplier is tracks
    assert isinstance(runtime.geometry,OfflineSpatialGeometry)
    assert runtime.geometry.analysis_frame_size == profile.face_recording_target_frame_size
    assert runtime.geometry.scale_x == float(profile.face_recording_scale_x)
    sessions.assert_not_called()


def test_formal_camera_loader_uses_named_profile_env_not_unrelated_global(tmp_path):
    profile,path,config,metadata=configured(tmp_path)
    # cam03 points deliberately to the existing cam02 fixture for the loading
    # test only. Resolution still rejects real cam03's different fingerprint.
    payload={'cameras':[dict(camera_id='cam03',camera_role='customer_behavior',store_id='store-1',
                            source='file:///test.mp4',interaction_areas=[],display_areas=[],
                            recognition=dict(device_id='cam03',base_url='http://test',auth_mode='none',
                                             evidence_mode='durable_push_inbox',capability_profile_id=profile.profile_id,
                                             capability_profile_path_env='TEST_CAM03_PROFILE'))]}
    camera_path=tmp_path/'camera.json'; camera_path.write_text(json.dumps(payload))
    with patch.dict('os.environ', {'TEST_CAM03_PROFILE':str(path),
                                  'RETAILVISION_CAPABILITY_PROFILE_PATH':'wrong-unused-path',
                                  'CAMERA_FACE_DEVICE_ID':'cam02'}):
        parsed=_formal_camera_configuration(config_path=camera_path)
        profiles,reason=_load_configured_profiles(parsed)
    assert parsed.device_id == 'cam03' and parsed.available
    assert profiles == (profile,) and reason is None
