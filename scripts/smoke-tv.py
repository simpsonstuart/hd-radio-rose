"""Validate the deployed TV transcoder with a synthetic MPEG-TS broadcast."""
import json
from pathlib import Path
import subprocess
import tempfile
from hdhomerun import encoder_args

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    # MPEG-2 video / AC-3 audio resembles a common ATSC 1.0 service.
    fixture=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',
        '-f','lavfi','-i','testsrc2=size=320x240:rate=30',
        '-f','lavfi','-i','sine=frequency=1000:sample_rate=48000',
        '-t','8','-c:v','mpeg2video','-b:v','600k','-c:a','ac3','-f','mpegts','pipe:1'],
        stdout=subprocess.PIPE,check=True,timeout=40).stdout
    subprocess.run(encoder_args(root),input=fixture,check=True,timeout=60)
    playlist=(root/'index.m3u8').read_text()
    assert '#EXTM3U' in playlist and '#EXT-X-ENDLIST' in playlist
    segments=list(root.glob('segment*.ts'))
    assert 1 <= len(segments) <= 9
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(segments[0])],timeout=10))
    assert {s['codec_name'] for s in info['streams']} == {'h264','aac'}
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    assert video['width'] <= 1280 and video['height'] <= 720
    assert all(not Path(line).is_absolute() and '/' not in line for line in playlist.splitlines() if line and not line.startswith('#'))
print('TV MPEG-2/AC-3 to browser H.264/AAC HLS: passed')
