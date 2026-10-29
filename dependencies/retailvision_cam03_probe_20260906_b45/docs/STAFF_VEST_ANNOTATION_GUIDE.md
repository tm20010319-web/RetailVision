# Staff vest annotation guide

## Purpose

This dataset trains one object-detection class only:

```yaml
names:
  0: staff_vest
```

`staff_vest` means the configured blue employee vest as a visible garment. It
is a Staff Role signal, not a Staff Identity and not a named employee.

The detector produces vest bounding boxes only. Person detection and tracking
remain the responsibility of the existing person tracking model:

```text
person_tracking_model -> person + Tracker
staff_vest_model      -> vest bbox
person + vest         -> RoleResolver -> staff/customer
```

## What to annotate

- Draw one tight rectangle around each visible blue staff-vest body.
- Include the visible front, back, or side panels that belong to the vest.
- When the vest is partly occluded or cut by the image edge, box only the
  visible portion.
- Annotate each qualifying vest separately when several staff members appear.
- Keep the box tight enough that its centre remains on the vest torso region.

## What not to annotate

- Do not box the whole person, head, trousers, or surrounding background.
- Do not expand the box to include large shirt sleeves or arms.
- Do not label ordinary blue shirts, coats, dresses, or trousers.
- Do not label blue walls, door frames, lights, signs, reflections, or screens.
- Do not force a box when the vest is too occluded or blurred to identify.
- Do not create a box merely because the image came from `blue_staff/`.

If a supposed positive image contains no qualifying visible vest, move it to
the negative review queue and give it an empty label instead of inventing a
box.

## YOLO label format

Each image has a same-stem `.txt` file. Every non-empty line is:

```text
0 x_center y_center width height
```

All four coordinates are normalized to the image width and height. Class IDs
other than `0` are invalid. A negative image must have a same-stem empty label
file; a missing label is never interpreted as a negative.

At the 640-pixel training size, a vest whose resized box has either dimension
below 8 pixels is rejected by the default validator. Review the frame rather
than enlarging the annotation.

## Review checklist

1. Confirm that every positive box contains a configured blue staff vest.
2. Confirm that boxes exclude the head, trousers, background, and large sleeve
   regions.
3. Confirm that every visible qualifying vest is annotated.
4. Confirm that intentional negative images have empty labels.
5. Run `tools/training/validate_staff_vest_dataset.py` and resolve every error before
   training.
6. Inspect the reported small boxes, duplicates, and perceptually similar
   cross-split pairs.

## Current data limitation

The first baseline contains empty blue-background frames and people who are not
wearing a vest, including people near the blue wall and door frame. It does not
yet contain a clear ordinary-blue-clothing hard-negative set. This is an
accepted first-experiment limitation, not evidence that the model can safely
distinguish blue clothing from the staff vest.

## Future runtime integration points

The current `BehaviorVisionAdapter` calls `.track()` on one detection model and
expects that result to contain both `person` and vest classes. The trained
single-class model cannot satisfy that contract by itself. A later, separately
scoped runtime change must:

- give `BehaviorVisionAdapter` distinct person-tracking and vest-detection
  model dependencies;
- call `.track()` only on the person model and `.predict()` on the vest model;
- associate vest boxes with tracked person torso regions in `RoleResolver`;
- split the current `BEHAVIOR_VEST_MODEL_PATH` startup wiring into explicit
  person-model and vest-model configuration;
- update `create_behavior_runtime()`, `run_behavior_runtime.py`, and the vision
  and runtime tests without changing the greeting thresholds.

This training-preparation task does not make those runtime changes.
