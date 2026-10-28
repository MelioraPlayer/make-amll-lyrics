"""Offline regression cases using synthetic text/audio and mocked HTTP responses."""
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import wave
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import align_lyrics as align
import build_release
import fetch_meting as fetch
import prepare_audio as prepare
import ttml_tools as ttml


class Response(io.BytesIO):
    def __init__(self, payload, mime):
        super().__init__(payload)
        self.headers = {'Content-Type': mime}


class TemporaryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)


class MetingTests(TemporaryCase):
    def test_endpoint_preserves_existing_query_and_encodes_search(self):
        url = fetch.endpoint('https://example.invalid/api?custom=a%2Bb&type=old', 'netease',
                             'search', '曲名 & artist', 3)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query, {'custom': ['a+b'], 'server': ['netease'], 'type': ['search'],
                                 'id': ['曲名 & artist'], 'limit': ['3']})

    def test_requires_explicit_service_without_network(self):
        env = {k: v for k, v in os.environ.items() if k != 'AMLL_METING_BASE'}
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/fetch_meting.py'), 'search',
                                 '--query', 'test', '--out', str(self.folder / 'search.json')],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('AMLL_METING_BASE', result.stderr)
        self.assertFalse((self.folder / 'search.json').exists())

    def test_download_follows_json_validates_and_hashes(self):
        target = self.folder / 'nested' / 'source.audio'
        payload = b'synthetic-media-bytes'
        responses = [Response(b'{"data":{"url":"https://example.invalid/audio"}}', 'application/json'),
                     Response(payload, 'audio/mpeg')]
        probe = {'streams': [{'codec_type': 'audio', 'codec_name': 'mp3', 'channels': 2}],
                 'format': {'duration': '2.5'}}
        with patch.object(fetch, 'request', side_effect=responses) as request_mock, \
                patch.object(fetch.shutil, 'which', return_value='ffprobe'), \
                patch.object(fetch.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(probe))):
            result = fetch.download_audio('https://example.invalid/url', target, False)
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result['duration'], 2.5)
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(list(target.parent.glob('*.partial')), [])

    def test_invalid_download_keeps_old_file_and_cleans_partial(self):
        target = self.folder / 'source.audio'
        target.write_bytes(b'existing-audio')
        with patch.object(fetch, 'request', return_value=Response(b'invalid-media', 'audio/mpeg')), \
                patch.object(fetch.shutil, 'which', return_value='ffprobe'), \
                patch.object(fetch.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['ffprobe'])):
            with self.assertRaises(subprocess.CalledProcessError):
                fetch.download_audio('https://example.invalid/url', target, True)
        self.assertEqual(target.read_bytes(), b'existing-audio')
        self.assertEqual(list(self.folder.glob('*.partial')), [])

    def test_save_and_download_refuse_overwrite(self):
        target = self.folder / 'source.audio'
        target.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            fetch.save(target, b'new')
        with patch.object(fetch, 'request') as request_mock:
            with self.assertRaises(FileExistsError):
                fetch.download_audio('https://example.invalid/url', target, False)
            request_mock.assert_not_called()


class LrcTests(unittest.TestCase):
    def test_preserves_credit_blank_and_multiple_stamps(self):
        result = prepare.parse_lrc('[offset:250]\n[00:00.000] 作词 : Example\n[00:01][00:03.500]Hello\n[00:02.000]\n[ar:Example]', 5, 100)
        self.assertEqual(result['declaredLrcOffsetsMs'], [250])
        self.assertEqual(result['appliedOffsetMs'], 100)
        self.assertEqual([row['sourceStart'] for row in result['rows']], [0.1, 1.1, 2.1, 3.6])
        self.assertIn('作词', result['rows'][0]['text'])
        self.assertEqual(result['rows'][2]['text'], '')
        self.assertEqual(result['rows'][-1]['text'], 'Hello')
        self.assertEqual(result['unparsedLines'][0]['text'], '[ar:Example]')

    def test_rejects_bad_time_and_duration(self):
        for source, duration in [('[00:61]Hello', 90), ('[00:10]Hello', 3), ('[00:01]Hello', float('nan'))]:
            with self.subTest(source=source, duration=duration), self.assertRaises(ValueError):
                prepare.parse_lrc(source, duration)


class AlignmentTests(TemporaryCase):
    def row(self):
        return {'id': 'L1', 'text': 'away', 'units': ['away'], 'cropStart': 1, 'cropEnd': 3}

    def test_unknown_and_duplicate_ids_are_errors(self):
        row = self.row()
        with self.assertRaises(ValueError):
            align.select_rows([row], 'L9')
        with self.assertRaises(ValueError):
            align.select_rows([row, row])
        self.assertEqual(align.select_rows([row], ' L1 '), [row])

    def test_bad_inputs_fail_preflight(self):
        with self.assertRaises(ValueError):
            align.validate_rows([dict(self.row(), cropEnd=50)], 60, 'ctc', {})
        with self.assertRaises(ValueError):
            align.validate_rows([self.row()], 5, 'ctc', {'L1': {'0': ['a', 'why']}})
        with self.assertRaises(ValueError):
            align.validate_rows([dict(self.row(), modelUnits=['你好'])], 5, 'ctc', {})

    def test_syllable_projection_preserves_spelling_and_whole_range(self):
        units = [{'text': 'away', 'start': 1, 'end': 3,
                  'chars': [{'text': c, 'start': s} for c, s in zip('AWAY', [1, 1.3, 1.6, 2])]}]
        align.project_syllables(units, {'0': ['a', 'way']}, 'ctc')
        self.assertEqual(units[0]['syllableCandidates'], [
            {'text': 'a', 'start': 1, 'end': 1.3}, {'text': 'way', 'start': 1.3, 'end': 3}])
        units[0]['chars'][0]['text'] = 'B'
        with self.assertRaises(ValueError):
            align.project_syllables(units, {'0': ['a', 'way']}, 'ctc')
        with self.assertRaises(ValueError):
            align.project_syllables(units, {'-1': ['away']}, 'ctc')

    def test_existing_result_is_protected_before_model_import(self):
        (self.folder / 'alignment.json').write_text('existing result', encoding='utf-8')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/align_lyrics.py'),
                                 '--audio', 'missing.wav', '--rows', 'missing.json', '--out', str(self.folder)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('alignment.json exists', result.stderr)
        self.assertEqual((self.folder / 'alignment.json').read_text(), 'existing result')


class TtmlTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.data = json.loads((ROOT / 'examples/reviewed.json').read_text(encoding='utf-8'))
        self.path = self.folder / 'example.ttml'

    def test_export_preserves_syllables_background_translation_and_agent(self):
        report = ttml.export(self.data, self.path)
        parsed, _ = ttml.inspect(self.path, 12)
        self.assertTrue(report['ok'])
        self.assertEqual(report['counts'], {'mainLines': 2, 'timedUnits': 4, 'translations': 2,
                                            'backgrounds': 1, 'backgroundTranslations': 1})
        self.assertEqual(parsed['lines'][0]['agent'], 'v2')
        line = parsed['lines'][1]
        self.assertEqual(line['text'], 'Stay awake')
        self.assertEqual([unit['text'] for unit in line['units']], ['Stay', 'a', 'wake'])
        self.assertEqual(line['backgrounds'][0]['translations'], {'zh-Hans': '噢'})
        self.assertLess(line['start'], line['units'][0]['start'])
        self.assertGreater(line['end'], line['units'][-1]['end'])
        xml = self.path.read_text(encoding='utf-8')
        self.assertIn('<translations>', xml)
        self.assertNotIn('<itunes:translations>', xml)
        self.assertFalse(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_invalid_export_preserves_destination_and_cleans_temp(self):
        self.path.write_bytes(b'keep original')
        self.data['lines'][1]['agent'] = 'v9'
        with self.assertRaises(ValueError):
            ttml.export(self.data, self.path)
        self.assertEqual(self.path.read_bytes(), b'keep original')
        self.assertEqual(list(self.folder.glob('*.checking')), [])

    def test_changed_voice_or_translation_is_not_semantically_equal(self):
        ttml.export(self.data, self.path)
        before, _ = ttml.inspect(self.path)
        changed = copy.deepcopy(self.data)
        changed['lines'][0]['agent'] = 'v1'
        changed['lines'][1]['translation'] = 'changed'
        other = self.folder / 'other.ttml'
        ttml.export(changed, other)
        after, _ = ttml.inspect(other)
        self.assertNotEqual(before, after)

    def test_independent_space_span_is_semantically_equivalent(self):
        ttml.export(self.data, self.path)
        before, _ = ttml.inspect(self.path)
        text = self.path.read_text(encoding='utf-8')
        self.path.write_text(text.replace('</span> <span', '</span><span begin="0.000" end="0.000"> </span><span'), encoding='utf-8')
        after, report = ttml.inspect(self.path)
        self.assertTrue(report['ok'], report)
        self.assertEqual(before, after)

    def test_duplicate_translation_and_missing_line_id_are_rejected(self):
        ttml.export(self.data, self.path)
        root = ET.fromstring(self.path.read_bytes())
        line = root.find(f'.//{{{ttml.TT}}}p')
        line.attrib.pop(f'{{{ttml.IT}}}key')
        ET.SubElement(line, f'{{{ttml.TT}}}span', {f'{{{ttml.TM}}}role': 'x-translation',
                                               f'{{{ttml.XL}}}lang': 'zh-Hans'}).text = 'duplicate'
        self.path.write_bytes(ttml.serialize(root))
        _, report = ttml.inspect(self.path)
        self.assertFalse(report['ok'])
        self.assertTrue(any('line ID' in error for error in report['errors']))
        line.set(f'{{{ttml.IT}}}key', 'L1')
        self.path.write_bytes(ttml.serialize(root))
        _, report = ttml.inspect(self.path)
        self.assertTrue(any('duplicate translations' in error for error in report['errors']))
        # A missing ID on a line with background vocals must produce diagnostics, not crash.
        root.findall(f'.//{{{ttml.TT}}}p')[1].attrib.pop(f'{{{ttml.IT}}}key')
        self.path.write_bytes(ttml.serialize(root))
        _, report = ttml.inspect(self.path)
        self.assertFalse(report['ok'])

    def test_time_parser_and_audio_range(self):
        self.assertEqual(ttml.milliseconds('1:02.003'), 62003)
        self.assertEqual(ttml.milliseconds('90s'), 90000)
        for value in ['1:62.000', '1:02s', '-0.1', 'NaN']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ttml.milliseconds(value)
        ttml.export(self.data, self.path)
        self.assertFalse(ttml.inspect(self.path, 6)[1]['ok'])


class ReleaseTests(TemporaryCase):
    def test_package_is_repeatable_and_excludes_work_data(self):
        import zipfile
        first = build_release.build(ROOT, self.folder / 'first')
        second = build_release.build(ROOT, self.folder / 'second')
        self.assertEqual(first['sha256'], second['sha256'])
        with zipfile.ZipFile(first['archive']) as archive:
            names = archive.namelist()
            self.assertIn('amll-lyrics/SKILL.md', names)
            self.assertIn('amll-lyrics/LICENSE', names)
            self.assertFalse(any('/work/' in name or '__pycache__' in name or '/.venv' in name for name in names))
        with self.assertRaises(FileExistsError):
            build_release.build(ROOT, self.folder / 'first')

    def test_local_markdown_links_resolve(self):
        import re
        for document in [ROOT / 'README.md', ROOT / 'SKILL.md', *(ROOT / 'references').glob('*.md')]:
            for link in re.findall(r'\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                if '://' in link or link.startswith('#'):
                    continue
                self.assertTrue((document.parent / link.split('#')[0]).exists(), f'{document.name}: {link}')

    def test_all_script_help_without_model_dependencies(self):
        for script in (ROOT / 'scripts').glob('*.py'):
            with self.subTest(script=script.name):
                result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg not installed')
class AudioIntegrationTests(TemporaryCase):
    def test_prepare_synthetic_wav_without_changing_duration(self):
        audio, lrc = self.folder / 'source.wav', self.folder / 'source.lrc'
        with wave.open(str(audio), 'wb') as handle:
            handle.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            handle.writeframes(b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * i / 48000)))
                                       for i in range(48000)))
        lrc.write_text('[00:00.100]Hello', encoding='utf-8')
        out = self.folder / 'prepared'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_audio.py'),
                                 '--audio', str(audio), '--lrc', str(lrc), '--out', str(out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with wave.open(str(out / 'mix-16k.wav'), 'rb') as handle:
            self.assertEqual(handle.getframerate(), 16000)
            self.assertEqual(handle.getnchannels(), 1)
            self.assertAlmostEqual(handle.getnframes() / handle.getframerate(), 1, places=3)
        self.assertEqual(json.loads((out / 'rows.json').read_text(encoding='utf-8'))['audioSha256'],
                         hashlib.sha256(audio.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
