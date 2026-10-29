# Seven-minute all-merchandise VLX attempt

Date: 2026-10-28
Status: FULL-LENGTH TEST VIDEO DELIVERED / REQUESTED ALL-ACTION COVERAGE FAILED

User explicitly expanded scope from home monitor/eye massager to every product
people pick up or put down in
`D:/surveillance_video_raw/9f61f8000a5603edfe3f21295de00760.mp4`, with product names
and actions on the video. No entrance counting or model training requested.

Artifacts: `runtime/video-analysis/20261028_9f61f800_all_goods/`, final files under
`delivery/`. This is an unsuccessful accuracy attempt, not an implementation of
reliable arbitrary-product recognition. Do not claim the user goal achieved.

## Executed evidence

- Fresh VLX open-question inventory across 29 frames spanning all 420s; region
  captions often mislabeled small held products as phones. Captions retained.
- Expanded 15-class local catalogue/probes, including four guitar colors,
  toys, uncertain round products, white handheld box, and prior home items.
- Dense broad VLX + CSRT attempt stopped after early missing-target retrigger
  inefficiency; second attempt stopped after approximately 100s due drift.
  An obvious false machine-dog pickup/putdown at 10.68/12.05s followed clothing.
  These events are excluded from final semantic results.
- Box and point SAM3.1 prompts lost propagated masks and were stopped with
  diagnostics preserved. Original semantic toy prompting passed a short test.
- Semantic groups toy/guitar/white box were organized across all 1849 source
  PTS samples using saved VLX region anchors and same-source recorded poses.
  Windows lacking trustworthy anchors stayed empty (no fabricated masks or
  claim of actual model detections in those intervals). Nine candidate classes
  attempted; nonempty masks only for five: white guitar 420 samples, bee 171,
  beige animal 397, brown plush 339, yellow robot dog 289.
- Original action policy replay generated one brown-plush PICK_UP at 93.636364s,
  confirmed 94.090909s, lost at 95.227273s; no putdown, no complete holding duration.
  Visual review supports nearby handling, but exact boundaries and full recall
  are not qualified. No manual actions inserted.

## Delivered qualification

The complete 420s video explicitly says automatic candidates / full accuracy
failed. Sparse generic held-object detections near recorded wrists have unknown
names/actions; they are not forced into completed events. Model count 1/0 is NOT
the true total. Trusted complete counts and holding duration remain null.

Files: annotated.mp4, result.json, events.jsonl, manifest.json,
observations.jsonl, interaction_facts.json/.jsonl, unresolved_objects.jsonl,
review_status.json, validation.json, README.md. Intermediate VLX replies, masks,
initialization attempts, partial outputs and logs remain in the parent folder.

Full ffmpeg decode passed; 420.000s output, monotonic original-relative PTS,
1849 output records, source hash verified. Existing acceptance exporter used
with process-local labels; session/event/fact consistency checks passed.
Accuracy remains failed despite artifact validation. All helper changes were
isolated under runtime/; no production rules, weights, camera config, training,
database or service changes. Do not carry these arbitrary local candidate IDs
into a production catalog.
