"""Fetch from an explicitly configured Meting service and validate downloaded audio."""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


def request(url):
    if urllib.parse.urlsplit(url).scheme not in ('https', 'http'):
        raise ValueError('Only HTTP(S) media URLs are supported')
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent': 'AMLL-Lyrics-Preparation/1.0'}), timeout=45)


def endpoint(base, server, kind, ident, limit=None):
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        raise ValueError('Meting base must be an absolute HTTP(S) URL')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Meting base must not contain URL credentials or a fragment')
    query = {'server': server, 'type': kind, 'id': ident}
    if limit is not None:
        query['limit'] = limit
    retained = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if k not in query]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(retained + list(query.items()))))


def save(path, content, force=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb' if force else 'xb') as handle:
        handle.write(content)


def text_response(url):
    with request(url) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Unexpectedly large metadata response')
    return raw.decode('utf-8-sig')


def find_lyric(obj):
    if isinstance(obj, str):
        return obj if re.search(r'\[\d+:\d+(?:\.\d+)?\]', obj) else None
    if isinstance(obj, dict):
        for key in ('lrc', 'lyric', 'data'):
            if key in obj:
                found = find_lyric(obj[key])
                if found:
                    return found
    if isinstance(obj, list):
        for item in obj:
            found = find_lyric(item)
            if found:
                return found
    return None


def find_url(obj):
    if isinstance(obj, str) and obj.strip().startswith(('https://', 'http://')):
        return obj.strip()
    if isinstance(obj, dict):
        for key in ('url', 'data'):
            if key in obj:
                result = find_url(obj[key])
                if result:
                    return result
    if isinstance(obj, list):
        for item in obj:
            result = find_url(item)
            if result:
                return result
    return None


def download_audio(url, destination, force):
    destination = Path(destination)
    if destination.exists() and not force:
        raise FileExistsError(destination)
    if not shutil.which('ffprobe'):
        raise RuntimeError('ffprobe is required to validate the download')
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(4):
        with request(url) as response:
            mime = response.headers.get('Content-Type', '').lower()
            prefix = response.read(1024)
            if ('json' in mime or 'text/' in mime or prefix.lstrip().startswith((b'{', b'['))):
                raw = prefix + response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError('Unexpectedly large media URL response')
                value = raw.decode('utf-8-sig')
                try:
                    obj = json.loads(value)
                except json.JSONDecodeError:
                    obj = value
                next_url = find_url(obj)
                if not next_url:
                    raise ValueError('Audio endpoint returned no usable media URL; response was not audio')
                url = next_url
                continue
            part = None
            try:
                total, digest = len(prefix), hashlib.sha256(prefix)
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + '.',
                                                 suffix='.partial', delete=False) as output:
                    part = Path(output.name)
                    output.write(prefix)
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 512 * 1024 * 1024:
                            raise ValueError('Audio exceeds the 512 MiB download limit')
                        output.write(chunk)
                        digest.update(chunk)
                result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                         '-of', 'json', str(part)], capture_output=True, text=True,
                                        check=True, timeout=60)
                probe = json.loads(result.stdout)
                streams = [s for s in probe.get('streams', []) if s.get('codec_type') == 'audio']
                duration = float(probe.get('format', {}).get('duration', 0))
                if not streams or not math.isfinite(duration) or duration <= 0:
                    raise ValueError('Downloaded file contains no valid audio stream/duration')
                if destination.exists() and not force:
                    raise FileExistsError(destination)
                part.replace(destination)
                return {'path': str(destination), 'bytes': total, 'sha256': digest.hexdigest(),
                        'duration': duration, 'codec': streams[0].get('codec_name'),
                        'sampleRate': streams[0].get('sample_rate'), 'channels': streams[0].get('channels')}
            finally:
                if part is not None:
                    part.unlink(missing_ok=True)
    raise ValueError('Too many nested media URL responses')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['search', 'fetch'])
    parser.add_argument('--base', default=os.environ.get('AMLL_METING_BASE'),
                        help='Meting endpoint; falls back to AMLL_METING_BASE (no built-in service)')
    parser.add_argument('--server', default='netease')
    parser.add_argument('--query')
    parser.add_argument('--id')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--lyrics-only', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if not args.base:
        parser.error('Set --base or AMLL_METING_BASE to your authorized Meting endpoint')
    if args.limit < 1:
        parser.error('--limit must be positive')
    if args.action == 'search':
        if not args.query:
            parser.error('search requires --query')
        value = json.loads(text_response(endpoint(args.base, args.server, 'search', args.query, args.limit)))
        save(args.out, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'), args.force)
        print(json.dumps({'saved': str(args.out), 'resultType': type(value).__name__}, ensure_ascii=False))
        return
    if not args.id:
        parser.error('fetch requires --id')
    args.out.mkdir(parents=True, exist_ok=True)
    targets = ['lyric-response.txt', 'original.lrc', 'download.json']
    if not args.lyrics_only:
        targets.append('source.audio')
    if not args.force and any((args.out / name).exists() for name in targets):
        raise FileExistsError('Output exists; use a new directory or explicit --force')
    lrc_url = endpoint(args.base, args.server, 'lrc', args.id)
    raw = text_response(lrc_url)
    save(args.out / 'lyric-response.txt', raw.encode('utf-8'), args.force)
    try:
        lyric_obj = json.loads(raw)
    except json.JSONDecodeError:
        lyric_obj = raw
    lyric = find_lyric(lyric_obj)
    if lyric:
        save(args.out / 'original.lrc', lyric.encode('utf-8'), args.force)
    manifest = {'server': args.server, 'id': args.id, 'lyricEndpoint': lrc_url,
                'lyricFound': bool(lyric)}
    if not args.lyrics_only:
        manifest['audio'] = download_audio(endpoint(args.base, args.server, 'url', args.id),
                                           args.out / 'source.audio', args.force)
    save(args.out / 'download.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'), args.force)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
