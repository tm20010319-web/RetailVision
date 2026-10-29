# User-entered target selection for the VLX preview

Date: 2026-10-23
Status: LIVE INPUT WINDOW / 18 FOCUSED TESTS PASS / TARGET-SELECTION REPLAYS PASS

User requested annotations only for entered names and no annotation when the
input is empty. Initial test scope is the existing bee plush and white electric
guitar. Realtime video/tracking and periodic VLX confirmation remain accepted.
Existing YOLO launchers/weights remain intact; production boundaries are unchanged.

## Behavior

- The preview uses a Tk window with a Chinese-IME-compatible text field.
- Start empty: camera playback continues without product annotations or VLX
  recognition calls (the model is preloaded and kept ready).
- Enter `小蜜蜂` or `白色电吉他` and press Enter / click `开始标注`.
  Chinese synonyms and `小蜜蜂、白色电吉他` for both are supported.
- Editing immediately clears the active selection; submit enables the new
  selection. Empty input or `清除标注` immediately removes all object tracks.
- A thread-safe selection revision travels with each recognition result.
  Clearing, switching, or clearing and re-entering the same name invalidates
  all older in-flight results. Outputs are also filtered to selected classes.
- The guitar-only supplementary query only runs when guitar is selected and
  absent in the normal result. Empty selection skips proposals and recognition.
- Esc/window close exits; typing Q in the input no longer closes the preview.
- For offline replay, `--images` now requires explicit `--target`; live preview
  defaults to empty input. `preview_status.json` records selected targets and
  revision independently of the worker's recognition status.

## Verification

18 focused tests pass in the actual VLX Python environment. New tests exercise
default-empty selection, independent queries, Chinese punctuation, invalid
input, in-flight result cancellation (including re-selecting the same target),
and real Tk entry/button callbacks for submit, edit, switch, and clear.
The prior 13 recognition/tracking/initialization tests remain green.

Actual model replays are stored under `input_bee_check_20261023` and
`input_guitar_check_20261023` in the existing external VLX runtime root.
These are functional selection checks, not new accuracy qualification.

Actual bee-only replay: screenshot 1 produces class 0; screenshot 2, which
contains the upright guitar, produces no selected object. Actual guitar-only
replay: screenshots 2 and 3 each produce class 1 only, including the revised
guitar-only supplementary query for the horizontal crop. Both runs exited 0.

The visible input-enabled preview was started with no `--target`. Camera
display advances with empty selected targets and empty tracks. Model loading
does not activate default targets. `current.json` identifies this run.

Live idle check in run `20261023_101734_925150`: worker is
`waiting_for_target`, selected targets and current tracks are both empty;
video advanced 18 frames over one second at about 17.63 FPS. Six earlier
recognition frames had already completed, so this also confirms that an
empty current selection does not retain their boxes. Evidence:
`input_idle_validation.json` in that run.
