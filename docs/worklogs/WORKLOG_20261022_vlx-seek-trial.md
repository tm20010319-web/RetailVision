# VLX zero-shot grounding feasibility trial

Date: 2026-10-22
Status: `STATIC INFERENCE COMPLETE / LIVE BRIDGE VALIDATION IN PROGRESS`

User confirmed the goal: names alone should locate the bee plush and white
guitar on behavior-detection-1, support continuous tracking and save annotated
video, avoiding a new object-specific annotation/training cycle.

Official source inspection:
- VLX-Seek has released 1.5-10B inference code and weights. Its architecture
  retrieves regions from automatic proposals; it is not a built-in persistent
  object tracker.
- VLX-Flow describes streaming understanding, but public checkpoints remain
  "Coming soon". VLX-VR's repository exposes video reasoning/SDK materials,
  rather than an immediately runnable local streaming bounding-box tracker.
- Public Seek uses WeDetect-Uni proposals because its internal OPN is not
  released. This trial uses that automatic path, not user annotations or the
  custom YOLO model as proposal inputs.

Pinned official repository:
`D:\RetailVisionDev\external\VLX-Seek`, commit
`f30eff457ed3620cb7074ea8f8d8d7ed0be7096f`.
Main checkpoint `omlab/VLX-Seek-1.5-10B`, revision
`6d1925f932ee6f0a8790b15420f403ee8f8e8f6a`, 20,033,961,788 bytes,
SHA-256 `abbbc2d103d76bf00b032bb09a9b833ff089890a763c4874a7f28f98c62af351`.
Proposal checkpoint `fushh7/WeDetect` / `wedetect_base_uni.pth`, revision
`3b9d0670cbf300d0b48f900ae87a6c4785d88939`, 433,889,662 bytes.

Dedicated environment `D:\RetailVisionDev\envs\vlx-seek-py311` inherits existing
PyTorch 2.11 CUDA 12.8 but locally pins transformers 5.13.0, accelerate 1.4.0,
bitsandbytes 0.50.2, timm 1.0.9 and einops 0.6.1. Existing environments were
not upgraded. Imports and actual NF4 CUDA matrix multiplication passed on the
RTX 5070 Ti 16GB. Native Windows/SDPA/NF4 is an experimental compatibility path,
not the official unquantized benchmark setup.

Files: `config/vlx_seek_trial.yaml`, `scripts/probe_vlx_seek.py`.
Cases: pinned previous miss, back-facing bee, upright bee/held guitar, infrared.
Queries include both target nouns and an absent red bicycle. Reference boxes
are evaluated after proposal generation; never supplied to VLX inference.
Artifacts go under `D:\RetailVisionDev\runtime\vlx-seek-eval`.

The official Xet transfer stalled; HTTP worked but single-stream throughput was
low. The main download now uses verified disjoint HTTP ranges with resumable
range metadata and a mandatory complete-checkpoint SHA-256 before activation.
Raw incomplete downloads remain recoverable. WeDetect weights downloaded, and
the actual proposal-generation stage ran on all four cases. Full VLX inference,
recognition accuracy, measured throughput and live/recording acceptance remain
pending; no end-to-end success is claimed from README descriptions.

Prepared a separate optional camera bridge, not yet accepted or activated:
`config/toy_guitar_tracking_vlx.yaml` and
`scripts/start_toy_guitar_tracking_vlx.ps1`. It captures an exact initialization
window, runs automatic WeDetect + text-based VLX in a separate process, validates
the source digest and both noun references, then releases that process before
loading the original SAM3.1 pipeline. Future camera sequences are offset from
the pinned seed window. This keeps the two large models out of VRAM together.
The default V2 initialization path is retained.

User confirmed both objects were placed in view. A warm, identity-verified
snapshot was saved at `runtime/vlx-seek-eval/live_objects_ready/source.png`
outside the repository and added as a fifth case. Its automatic proposal
coverage is approximately 0.94 IoU for bee / 0.89 for guitar; these are proposal
metrics, not semantic recognition results. The source camera itself embeds red
person/edge marks; this snapshot does not derive its product boxes from YOLO.

Focused contracts for absent/ambiguous target rejection, exact seed context and
monotonic future sequence, plus existing integration/realtime checks: 21 passed.

## Actual inference, 2026-10-22 morning

All 20,033,961,788 checkpoint bytes downloaded and full SHA-256 matched the
pinned Hub digest above. An initial Windows native access violation in
`torch_cpu.dll` was reproduced during Transformers safetensors materialization.
The supported `pread` backend resolves it; the trial scopes this loader override
to `from_pretrained` and restores it afterward. Original vendor files and shared
environments remain unchanged. A second explicit error (`GeluCUDAKernelImpl`
with Byte inputs) exposed module skip matching: Transformers requires complete
`model.vision_tower` / `model.mm_projector_aux` prefixes to exclude descendants.
The corrected loader checks that every NF4 linear belongs to the language model.
Actual model load and inference now complete with 248 quantized language modules.

Actual results: `D:\RetailVisionDev\runtime\vlx-seek-eval\five_case_actual`.
Every positive query used `bee plush toy; white guitar`, generated from the
official detection template. No manual boxes or custom YOLO inputs were supplied.

| Case | Bee IoU | Guitar IoU | VLX query seconds |
| --- | ---: | ---: | ---: |
| Previous YOLO bee miss | 0.905 | 0.914 | 1.411 |
| Back-facing bee | missed | 0.904 | 1.182 |
| Held guitar | 0.940 | 0.899 | 1.135 |
| Infrared | missed | 0.921 | 1.538 |
| Current objects-ready frame | 0.944 | 0.894 | 1.133 |

Five absent `red bicycle` queries all returned no regions (0.73–1.13 seconds).
Model load: 20.849 seconds. Peak PyTorch allocation: 10,653,800,960 bytes.
These are five selected feasibility images, not a dataset-level accuracy claim.
The local NF4/SDPA path is not an official BF16 benchmark. Seek alone is still
not a persistent tracker and bee recognition has clear pose/night limitations.

Initial live localization correctly selected both automatic proposals, but the
trial's new initialization overlap gate rejected the bee standing in front of
the guitar neck. The real rectangles legitimately overlap. Added a failing
regression for this setup, changed rejection to essentially identical regions
(IoU > 0.8), and verified that identical boxes are still rejected. Rerun of the
21 focused checks passed. The first rejected run is retained at
`toy-guitar-tracking-vlx-camera1/20261022_110847_345854`; it is not a successful
tracking result. A fresh bounded live bridge run is in progress.

Sources:
- https://github.com/om-ai-lab/VLX-Seek
- https://github.com/om-ai-lab/VLX-Flow
- https://github.com/om-ai-lab/VLX-VR
- https://huggingface.co/omlab/VLX-Seek-1.5-10B
