from __future__ import annotations
import unittest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from urllib.parse import urlsplit, unquote

from scripts.run_toy_guitar_live import camera_password, make_source, verify_model_names, verify_p2p, input_color_issue, atomic_json


class ToyGuitarLiveTests(unittest.TestCase):
    @unittest.skipUnless(os.name=="nt","Windows readers deny atomic replacement while their handle is open")
    def test_busy_windows_snapshot_reader_does_not_crash_live_writer(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/"status.json"
            path.write_text('{"frame":1}',encoding="utf-8")
            with path.open("rb"):
                self.assertFalse(atomic_json(path,{"frame":2}))
                self.assertEqual(json.loads(path.read_text()),{"frame":1})
            self.assertTrue(atomic_json(path,{"frame":3}))
            self.assertEqual(json.loads(path.read_text()),{"frame":3})

    def test_camera_wide_magenta_cast_is_rejected_only_when_enabled(self):
        frame=np.full((448,800,3),(255,0,255),dtype=np.uint8)
        self.assertEqual(input_color_issue(frame,.5),"camera_magenta_cast")
        self.assertIsNone(input_color_issue(frame))

    def test_small_magenta_object_does_not_reject_the_entire_scene(self):
        frame=np.full((448,800,3),(120,110,100),dtype=np.uint8)
        frame[100:150,200:250]=(255,0,255)
        self.assertIsNone(input_color_issue(frame,.5))

    def test_normal_and_infrared_frames_pass_the_color_check(self):
        for bgr in ((150,100,20),(100,100,100),(10,10,10)):
            frame=np.full((448,800,3),bgr,dtype=np.uint8)
            self.assertIsNone(input_color_issue(frame,.5))

    def test_different_camera_identity_is_rejected(self):
        verify_p2p(b"<P2P><DID>REPLACE_WITH_DEVICE_P2P_PREFIX-extra</DID></P2P>","REPLACE_WITH_DEVICE_P2P_PREFIX")
        with self.assertRaisesRegex(ValueError,"identity does not match"):
            verify_p2p(b"<P2P><DID>REPLACE_WITH_DEVICE_P2P_PREFIX</DID></P2P>","REPLACE_WITH_DEVICE_P2P_PREFIX")

    def test_saved_empty_password_requires_explicit_configuration(self):
        camera={"password_env":"TEST_CAMERA_PASSWORD","allow_empty_password":True}
        self.assertEqual("",camera_password(camera,{}))
        self.assertEqual("provided",camera_password(camera,{"TEST_CAMERA_PASSWORD":"provided"}))
        camera["allow_empty_password"]=False
        with self.assertRaisesRegex(ValueError,"not set"):
            camera_password(camera,{})

    def test_rtsp_credentials_are_encoded_without_changing_the_verified_host(self):
        source=make_source({"username":"camera user","host":"192.0.2.6","rtsp_port":554,"rtsp_path":"/live/0/SUB"},"a@b:#")
        parsed=urlsplit(source)
        self.assertEqual("192.0.2.6",parsed.hostname)
        self.assertEqual("camera user",unquote(parsed.username))
        self.assertEqual("a@b:#",unquote(parsed.password))
        self.assertEqual("/live/0/SUB",parsed.path)

    def test_staff_vest_or_reversed_classes_cannot_be_used_accidentally(self):
        names={0:"toy",1:"white_guitar"}
        verify_model_names(names,names)
        for wrong in ({0:"staff_vest"},{0:"white_guitar",1:"toy"}):
            with self.assertRaisesRegex(ValueError,"classes disagree"):
                verify_model_names(wrong,names)


if __name__=="__main__":
    unittest.main()
