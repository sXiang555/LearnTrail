#!/usr/bin/env python3
"""Seed explicitly labelled research demos through Java APIs, never through SQL.

Default: validate the catalog only. --apply requires an existing private backup.
Credentials and created IDs stay in the ignored .local directory. No engagement,
wallet, order or progress APIs are called. Requires Pillow and configured FFmpeg
only when generating video. Existing content is never overwritten or deleted.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'database/demo/V20261113_01__rag_agent_catalog.json'
PRIVATE = ROOT / '.local/run/research-demo-state.json'
BASE = 'http://127.0.0.1:10010'


def save_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.curation-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def article_edit(paper):
    body = (f"# {paper['title']}\n\n"
            f"原论文：{paper['paperTitle']}（{paper['year']}）\n\n"
            f"## 核心思路\n\n{paper['idea']}\n\n"
            "## 阅读时关注\n\n" + '\n'.join('- ' + item for item in paper['focus']) +
            f"\n\n## 延伸实践（本站建议）\n\n{paper['exercise']}\n\n"
            "## 来源与边界\n\n"
            f"原作者：{paper['sourceAuthor']}。"
            f"[阅读论文原文]({paper['sourceUrl']})。\n\n"
            "本文为 LearnTrail 演示策展空间整理的导读，不是论文全文、官方译文或作者演讲。"
            "本站不因阅读外链导读发放全文阅读积分，论文结论以原文实验条件为准。")
    return dict(title=paper['title'], summary=paper['summary'], body=body,
                contentFormat='markdown', sourceType='link',
                sourceAuthor=paper['sourceAuthor'], sourceUrl=paper['sourceUrl'], licenseNote='')


def validate(catalog):
    creators = catalog['creators']
    keys = {item['key'] for item in creators}
    assert len(keys) == len(creators) == 4, 'Expected four distinct demo spaces'
    assert len({p['key'] for p in catalog['papers']}) == len(catalog['papers']) == 12
    assert len({p['sourceUrl'] for p in catalog['papers']}) == 12
    for creator in creators:
        assert '演示' in creator['displayName'] and '演示' in creator['bio']
        assert len(creator['video']['scenes']) == len(creator['video']['nodes']) == 5
        assert creator['tone'] in {'forest', 'clay', 'violet', 'mint'}
    for paper in catalog['papers']:
        assert paper['creator'] in keys
        parsed = urllib.parse.urlsplit(paper['sourceUrl'])
        assert parsed.scheme == 'https' and parsed.hostname == 'arxiv.org'
        assert parsed.path.startswith('/abs/') and not parsed.username
        edit = article_edit(paper)
        assert len(edit['title']) <= 100 and len(edit['summary']) <= 240
        assert 100 <= len(edit['body']) <= 5000
    return hashlib.sha256(CATALOG.read_bytes()).hexdigest()


class Api:
    def __init__(self, credentials):
        self.credentials = credentials
        self.token = None
        self.expires_at = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, method='GET', body=None, auth=True, raw=None, content_type=None):
        if auth and time.monotonic() >= self.expires_at:
            self.token = self.call('/as/accounts/login', 'POST',
                                   dict(type=1, username=self.credentials['username'],
                                        password=self.credentials['password']), auth=False)
            self.expires_at = time.monotonic() + 150
        headers = {'Content-Type': content_type or 'application/json'}
        if auth:
            headers['authorization'] = self.token
        data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=150)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            try:
                payload = json.load(response)
            except (ValueError, UnicodeError):
                raise RuntimeError(f'{method} {path}: invalid JSON, HTTP {response.status}') from None
            code = payload.get('code')
            if response.status != 200 or code != 200:
                raise RuntimeError(f'{method} {path}: HTTP {response.status}, code {code}')
            return payload.get('data')

    def upload(self, path):
        boundary = 'LearnTrail' + secrets.token_hex(12)
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="{path.name}"\r\nContent-Type: video/mp4\r\n\r\n').encode()
        raw += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
        return self.call('/ms/medias/studio/upload', 'POST', raw=raw,
                         content_type=f'multipart/form-data; boundary={boundary}')


def font_path():
    choices = [Path('/System/Library/Fonts/PingFang.ttc'),
               Path('/System/Library/Fonts/STHeiti Light.ttc'),
               Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
    for path in choices:
        if path.is_file():
            return str(path)
    raise RuntimeError('A CJK font is required to render the video')


def render_video(creator, directory, ffmpeg, fingerprint):
    from PIL import Image, ImageDraw, ImageFont
    path = directory / (creator['key'] + '.mp4')
    marker = path.with_suffix('.sha256')
    if path.is_file() and marker.is_file() and marker.read_text() == fingerprint:
        return path
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fonts = {size: ImageFont.truetype(font_path(), size) for size in [18, 22, 26, 32, 44]}
    colors = {'forest': '#28604c', 'clay': '#a85d43', 'violet': '#6b5b9b', 'mint': '#32796d'}
    accent = colors[creator['tone']]
    frames = []
    for index, scene in enumerate(creator['video']['scenes']):
        frame = Image.new('RGB', (1280, 720), '#f5f2e9')
        d = ImageDraw.Draw(frame)
        d.text((66, 40), 'LearnTrail  /  学途研究小课', font=fonts[22], fill='#243e34')
        d.text((965, 44), creator['video']['label'], font=fonts[18], fill=accent)
        d.line((66, 89, 1214, 89), fill='#d3d8ce', width=2)
        d.text((66, 120), scene['title'], font=fonts[44], fill='#203d31')
        for j, node in enumerate(creator['video']['nodes']):
            left = 70 + j * 242
            active = j == scene['active']
            d.rounded_rectangle((left, 218, left + 166, 286), radius=18,
                                fill=accent if active else '#e5e9df')
            d.text((left + 83, 252), node, anchor='mm', font=fonts[26],
                   fill='white' if active else '#53675b')
            if j < 4:
                d.line((left + 178, 252, left + 220, 252), fill=accent, width=3)
                d.polygon([(left + 220, 252), (left + 210, 247), (left + 210, 257)], fill=accent)
        for j, line in enumerate(scene['lines']):
            d.text((78, 342 + j * 68), f'{j + 1:02d}', font=fonts[22], fill=accent)
            d.text((134, 336 + j * 68), line, font=fonts[32], fill='#304c3d')
        d.rounded_rectangle((66, 575, 1214, 630), radius=12, fill='#e7eadf')
        d.text((85, 588), f"{creator['displayName']}  ·  原创无声知识动画  ·  非论文作者演讲", font=fonts[22], fill='#506255')
        d.text((66, 660), f"演示素材 / 场景 {index + 1} / 5  ·  关联论文见专栏文章", font=fonts[18], fill='#6b796d')
        frames.append(frame)
    temporary = path.with_suffix('.rendering.mp4')
    log = directory / (creator['key'] + '.render.log')
    fd = os.open(log, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as output:
        process = subprocess.Popen([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                    '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '1280x720',
                                    '-framerate', '12', '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                                    '-preset', 'veryfast', '-crf', '24', '-threads', '2', '-pix_fmt', 'yuv420p',
                                    '-movflags', '+faststart', str(temporary)], stdin=subprocess.PIPE,
                                   stdout=output, stderr=output)
        try:
            for number in range(720):
                frame = frames[number // 144].copy()
                d = ImageDraw.Draw(frame)
                d.rounded_rectangle((66, 698, 1214, 704), radius=3, fill='#d7ddcf')
                d.rectangle((66, 698, 66 + int(1148 * (number + 1) / 720), 704), fill=accent)
                # Moving indicator makes this an actual animated explainer, not a blank clip.
                x = 70 + (number // 144) * 242 + 83 + int(10 * math.sin(number / 12))
                d.ellipse((x - 4, 305, x + 4, 313), fill=accent)
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            if process.wait(timeout=90) != 0:
                raise RuntimeError('Video encoding failed; private log retained')
        except BaseException:
            process.kill()
            process.wait()
            raise
    temporary.replace(path)
    path.chmod(0o600)
    marker.write_text(fingerprint)
    marker.chmod(0o600)
    frames[0].save(directory / (creator['key'] + '.preview.png'))
    return path


def apply(catalog, fingerprint, backup):
    if not backup.is_file() or backup.stat().st_size == 0 or not backup.resolve().is_relative_to(ROOT / '.local/backups'):
        raise ValueError('An existing backup under .local/backups is required')
    from run_local import read_env
    config = read_env(ROOT / '.env.local')
    ffmpeg = config.get('LEARNTRAIL_FFMPEG', 'ffmpeg')
    state = json.loads(PRIVATE.read_text()) if PRIVATE.exists() else {'version': catalog['version'], 'creators': {}}
    if state['version'] != catalog['version']:
        raise ValueError('Existing import state belongs to a different catalog')
    state['catalog_sha256'] = fingerprint
    save_private(PRIVATE, state)
    for creator in catalog['creators']:
        key = creator['key']
        if key not in state['creators']:
            state['creators'][key] = {'username': 'lt_demo_' + key + '_20261113',
                                      'password': secrets.token_urlsafe(24), 'articles': {}}
            save_private(PRIVATE, state)
        item = state['creators'][key]
        api = Api(item)
        if not item.get('registered'):
            api.call('/us/students/register/account', 'POST',
                     dict(username=item['username'], password=item['password'], nickname=creator['displayName']), auth=False)
            item['registered'] = True
            save_private(PRIVATE, state)
        if not item.get('profile'):
            profile = api.call('/cs/studio/workspace', 'POST',
                               {field: creator[field] for field in ['displayName', 'headline', 'bio']})
            item['profile'] = {'id': profile['id'], 'slug': profile['slug']}
            save_private(PRIVATE, state)
        for paper in [p for p in catalog['papers'] if p['creator'] == key]:
            entry = item['articles'].get(paper['key'])
            if entry is None:
                article_id = api.call('/ls/studio-articles', 'POST', article_edit(paper))
                entry = item['articles'][paper['key']] = {'id': article_id, 'published': False}
                save_private(PRIVATE, state)
            if not entry['published']:
                api.call(f"/ls/studio-articles/{entry['id']}/state/published", 'PUT')
                entry['published'] = True
                save_private(PRIVATE, state)
        if not item.get('asset'):
            video = render_video(creator, ROOT / '.local/demo-media/research-20261113', ffmpeg, fingerprint)
            item['asset'] = api.upload(video)
            save_private(PRIVATE, state)
        if not item.get('series_id'):
            categories = api.call('/cs/studio/workspace/categories')
            category = next((c for c in categories if '人工智能' in c['name']), None)
            if category is None:
                raise ValueError('Existing AI category is required; no category is created automatically')
            description = (f"{creator['subtitle']}\n\n"
                           "本专栏为 LearnTrail 演示策展空间发布的原创无声知识动画，含中文字幕和流程图，"
                           "用于入门导览，不是论文作者演讲或完整课程。关联文章提供自写导读、原作者与原文入口。"
                           "演示身份不代表真实独立用户，不包含虚构互动或学习记录。")
            form = dict(title=creator['seriesTitle'], subtitle=creator['subtitle'], description=description,
                        topic='RAG / Agent', coverTone=creator['tone'], unlockPoints=0, price=0,
                        categoryId=category['id'], articleIds=[a['id'] for a in item['articles'].values()])
            item['series_id'] = api.call('/cs/studio/workspace/series', 'POST', form)
            save_private(PRIVATE, state)
        if not item.get('episodes_saved'):
            api.call(f"/cs/studio/workspace/series/{item['series_id']}/episodes", 'PUT',
                     dict(episodes=[dict(mediaId=item['asset']['id'], title=creator['seriesTitle'] + ' · 知识动画')],
                          previewMediaId=item['asset']['id'], previewMinutes=1))
            item['episodes_saved'] = True
            save_private(PRIVATE, state)
        if not item.get('published'):
            item['slug'] = api.call(f"/cs/studio/workspace/series/{item['series_id']}/publish", 'POST')
            item['published'] = True
            save_private(PRIVATE, state)
        print(f"Imported {key}: 3 paper guides, 1 playable demo series", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    try:
        catalog = json.loads(CATALOG.read_text())
        fingerprint = validate(catalog)
        if not args.apply:
            print('Catalog valid: 4 labelled demo creators, 12 external paper guides, 4 original animations. No writes.')
            return 0
        if not args.backup:
            raise ValueError('--backup is required with --apply')
        apply(catalog, fingerprint, args.backup.resolve())
        return 0
    except (OSError, ValueError, AssertionError, RuntimeError, subprocess.SubprocessError) as error:
        # Never print external response bodies, tokens or private state.
        message = str(error) if isinstance(error, (ValueError, AssertionError, RuntimeError)) else type(error).__name__
        print('Research demo import failed: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
