from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from tools.training.train_toy_guitar import parse_labels, prepare_dataset, run_training


class ToyGuitarTrainingTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[dict, Path]:
        source = root / "annotations"
        (source / "images").mkdir(parents=True)
        (source / "labels").mkdir()
        frames=[]
        for index in range(12):
            name=f"frame_{index:04d}.jpg"
            image=source / "images" / name
            Image.new("RGB", (80,48), (index*18,100,70)).save(image)
            label="0 0.4 0.4 0.2 0.2\n"
            if index>=6:
                label+="1 0.6 0.7 0.3 0.2\n"
            (source / "labels" / Path(name).with_suffix(".txt")).write_text(label)
            frames.append({"image":name,"source_pts_seconds":index*3,"image_sha256":hashlib.sha256(image.read_bytes()).hexdigest()})
        (source / "annotation_manifest.json").write_text(json.dumps({"source_video":"fixture.mp4","source_sha256":"fixture_source_hash","classes":{"0":"toy","1":"white_guitar"},"frames":frames}))
        config={"dataset":{"annotations":str(source),"output":str(root/"dataset"),"names":{0:"toy",1:"white_guitar"},"split_ratios":{"train":.7,"val":.15,"test":.15},"min_box_pixels":8},
                "training":{"imgsz":640,"model":"missing-but-not-needed-for-dataset-validation.pt","project":str(root/"runs"),"name":"fixture"},"evaluation":{}}
        return config,source

    def test_both_classes_are_valid_and_unsafe_boxes_are_rejected(self):
        names={0:"toy",1:"white_guitar"}
        self.assertEqual(1,parse_labels("1 .5 .5 .2 .2",(800,448),names,640,8)[0][0])
        for label in ("2 .5 .5 .2 .2","0 .99 .5 .2 .2","0 nan .5 .2 .2","0 .5 .5 .001 .001","0.5 .5 .5 .2 .2"):
            with self.subTest(label=label),self.assertRaises(ValueError):
                parse_labels(label,(800,448),names,640,8)

    def test_preparation_keeps_sources_intact_and_discloses_shared_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,source=self.fixture(Path(temporary))
            original={str(p.relative_to(source)):p.read_bytes() for p in source.rglob("*") if p.is_file()}
            dataset,report=prepare_dataset(config)
            self.assertTrue(report["valid"])
            self.assertFalse(report["independent_video_evaluation"])
            self.assertGreater(report["near_duplicate_cross_split_pairs"],0)
            self.assertEqual([8,2,2],[report["splits"][s]["images"] for s in ("train","val","test")])
            self.assertEqual(original,{str(p.relative_to(source)):p.read_bytes() for p in source.rglob("*") if p.is_file()})
            entries=json.loads((dataset/"split_manifest.json").read_text())["entries"]
            for signature in ([0],[0,1]):
                groups={split:[e["source_pts_seconds"] for e in entries if e["split"]==split and e["class_ids"]==signature] for split in ("train","val","test")}
                self.assertLess(max(groups["train"]),min(groups["val"]))
                self.assertLess(max(groups["val"]),min(groups["test"]))
            self.assertEqual(report,prepare_dataset(config)[1])

    def test_tampered_dataset_and_changed_split_parameters_are_not_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,_=self.fixture(Path(temporary))
            dataset,_=prepare_dataset(config)
            next((dataset/"labels/train").glob("*.txt")).write_text("0 .5 .5 .2 .2\n")
            with self.assertRaisesRegex(ValueError,"copy/hash mismatch"):
                prepare_dataset(config)
            config["dataset"]["split_ratios"]={"train":.5,"val":.25,"test":.25}
            with self.assertRaisesRegex(ValueError,"different sources or parameters"):
                prepare_dataset(config)

    def test_invalid_source_label_fails_before_dataset_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,source=self.fixture(Path(temporary))
            (source/"labels/frame_0000.txt").write_text("2 .5 .5 .2 .2\n")
            with self.assertRaisesRegex(ValueError,"unknown class"):
                prepare_dataset(config)
            self.assertFalse(Path(config["dataset"]["output"]).exists())

    def test_existing_experiment_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            config,_=self.fixture(Path(temporary))
            run=Path(config["training"]["project"])/"fixture"
            run.mkdir(parents=True)
            sentinel=run/"best.pt"
            sentinel.write_bytes(b"retain previous experiment")
            with self.assertRaisesRegex(ValueError,"Experiment already exists"):
                run_training(config,Path(temporary),{},Path(temporary)/"config.yaml")
            self.assertEqual(b"retain previous experiment",sentinel.read_bytes())


if __name__=="__main__":
    unittest.main()
