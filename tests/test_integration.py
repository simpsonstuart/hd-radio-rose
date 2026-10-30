import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "container"))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


audio = module("audio_server", "container/audio_server.py")
studio = module("patch_studio", "scripts/patch-studio.py")
entrypoint = module("entrypoint", "container/entrypoint.py")
gateway = module("gateway", "container/gateway.py")


class AudioTests(unittest.TestCase):
    def test_multiple_listeners_receive_same_stream(self):
        stream = audio.Broadcast()
        first, second = stream.subscribe(), stream.subscribe()
        stream.publish(b"frame")
        self.assertEqual(first.get_nowait(), b"frame")
        self.assertEqual(second.get_nowait(), b"frame")
        stream.unsubscribe(first)
        self.assertNotIn(first, stream.listeners)

    def test_slow_listener_does_not_block_fast_listener(self):
        stream = audio.Broadcast(backlog=1)
        slow, fast = stream.subscribe(), stream.subscribe()
        stream.publish(b"one")
        fast.get_nowait()
        stream.publish(b"two")
        self.assertIsNone(slow.get_nowait())
        self.assertNotIn(slow, stream.listeners)
        self.assertEqual(fast.get_nowait(), b"two")

    def test_connection_limit_and_failure(self):
        stream = audio.Broadcast(limit=1)
        listener = stream.subscribe()
        self.assertIsNone(stream.subscribe())
        stream.unsubscribe(listener)
        stream.failed = True
        self.assertIsNone(stream.subscribe())

    def test_health_requires_recent_audio(self):
        stream = audio.Broadcast()
        self.assertFalse(stream.healthy())
        stream.publish(b"data")
        self.assertTrue(stream.healthy())
        stream.last_data = time.monotonic() - 11
        self.assertFalse(stream.healthy())
        stream.publish(b"data")
        stream.failed = True
        self.assertFalse(stream.healthy())


class BuildTests(unittest.TestCase):
    def test_patch_rejects_missing_and_ambiguous_anchors(self):
        self.assertEqual(studio.replace_once('one two', 'one', 'three'), 'three two')
        for text in ('changed upstream', 'one one'):
            with self.assertRaises(RuntimeError):
                studio.replace_once(text, 'one', 'three')

    def test_missing_password_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Set WEB_PASSWORD"):
                entrypoint.authentication()

    def test_username_cannot_inject_password_file(self):
        with patch.dict("os.environ", {"WEB_USER": "radio:hash\nother", "WEB_PASSWORD": "test"}, clear=True):
            with self.assertRaisesRegex(ValueError, "WEB_USER"):
                entrypoint.authentication()

    def test_password_cannot_inject_openssl_input(self):
        with patch.dict("os.environ", {"WEB_PASSWORD": "one\ntwo"}, clear=True):
            with self.assertRaisesRegex(ValueError, "line breaks"):
                entrypoint.authentication()


class GatewayTests(unittest.TestCase):
    def test_rejects_unbounded_presets_and_programs(self):
        for kind, value in [('SavePreset', 1000000), ('SelectProgram', 8), ('SelectProgram', -1), ('SelectProgram', True)]:
            with self.assertRaises(ValueError):
                gateway.validate_command({'type': kind, 'value': value})

    def test_rejects_nonfinite_numeric_inputs(self):
        for value in [float('nan'), float('inf'), '101.1', None]:
            with self.assertRaises(ValueError):
                gateway.validate_command({'type': 'TuneMhz', 'value': value})

    def test_rejects_native_dialog_and_unknown_commands(self):
        for kind in ['ExportLogCsv', 'ShowAbout', 'shutdown', 'self_test']:
            with self.assertRaises(ValueError):
                gateway.validate_command({'type': kind, 'value': '/etc'})

    def test_recording_folder_cannot_escape_storage(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, patch.object(gateway, 'DATA', Path(directory)):
            path = gateway.validate_command({'type': 'SetRecordingDir', 'value': 'My shows'})['value']
            self.assertEqual(Path(path), Path(directory) / 'recordings' / 'My shows')
            with self.assertRaises(ValueError):
                gateway.validate_command({'type': 'SetRecordingDir', 'value': '../../outside'})

    def test_preserves_64_bit_artwork_hash(self):
        result = gateway.validate_command({'type': 'BlockCover', 'value': '18446744073709551615'})
        self.assertEqual(result['value'], 2**64 - 1)

    def test_preset_schema(self):
        preset = [2, {'name': 'Local FM', 'frequency_mhz': 101.1, 'program': 1}]
        self.assertEqual(gateway.validate_command({'type': 'SetPreset', 'value': preset})['value'], preset)
        preset[1]['program'] = 9
        with self.assertRaises(ValueError):
            gateway.validate_command({'type': 'SetPreset', 'value': preset})

    def test_confines_downloads_and_rejects_config_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            media_dir = root / 'media'
            media_dir.mkdir()
            (media_dir / 'cover.png').write_bytes(b'image')
            (root / 'outside.png').write_bytes(b'image')
            (media_dir / 'config.toml').write_text('secret')
            self.assertEqual(gateway.confined_file(media_dir, 'cover.png', {'.png'}), media_dir / 'cover.png')
            for name in ['../outside.png', str(root / 'outside.png'), 'config.toml']:
                with self.assertRaises(ValueError):
                    gateway.confined_file(media_dir, name, {'.png'})


if __name__ == "__main__":
    unittest.main()
