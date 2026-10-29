"""Real FFmpeg regression for indexing variable-PTS footage in the demo renderer."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.render_cam03_member_demo import source_decode_command


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class OriginalFrameDecodeTests(unittest.TestCase):
    def test_decoder_preserves_each_original_frame_across_pts_gap(self):
        # Distinct solid frames, with an eight-frame-sized timestamp gap.
        # The former default decoder duplicates earlier frames into this gap;
        # subsequent original-frame indices then select the wrong image.
        width, height = 16, 16
        frames = b"".join(bytes(rgb) * (width * height) for rgb in (
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        ))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "variable_pts.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", "16x16", "-r", "25", "-i", "-",
                "-vf", "setpts='if(lt(N,2),N,N+8)*0.04/TB'",
                "-fps_mode", "passthrough", "-c:v", "ffv1", str(source),
            ], input=frames, capture_output=True, check=True, timeout=15)
            probe = json.loads(subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "frame=pts_time", "-of", "json", str(source),
            ], capture_output=True, check=True, timeout=15).stdout)
            self.assertEqual([0.0, 0.04, 0.4, 0.44],
                             [float(f["pts_time"]) for f in probe["frames"]])
            decoded = subprocess.run(source_decode_command(source, width, height),
                                     capture_output=True, check=True, timeout=15).stdout
            self.assertEqual(len(frames), len(decoded),
                             "decoder inserted frames that have no original PTS entry")
            self.assertEqual(frames, decoded, "original PTS index selected a different image")


if __name__ == "__main__":
    unittest.main()
