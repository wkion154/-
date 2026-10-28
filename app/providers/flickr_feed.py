"""Experimental public tag feed, not Flickr's authenticated full search API.

Only supplied media URLs are used. A feed is bounded to its recent items and
has no verified pagination. Live availability remains separately unverified.
"""
import json
import time
from urllib.parse import urlencode, urlsplit

from .http import Cancelled, RemoteError, validate_url
from .search_previews import allow_reviewed_previews
from ..page_discovery import ImageCandidate, ParsedPage

SPEC = dict(
    id='flickr-public-feed', name='Flickr · 공개 태그 피드 실험', category='fan',
    adapter='html', domains=['www.flickr.com', 'live.staticflickr.com'] +
    [f'farm{i}.staticflickr.com' for i in range(1, 10)],
    policy_urls=['https://www.flickr.com/services/feeds/docs/photos_public/'],
)
ENDPOINT = 'https://www.flickr.com/services/feeds/photos_public.gne'


def parse_feed(raw):
    try:
        feed = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RemoteError('Flickr 공개 피드의 JSON을 읽지 못했습니다.') from None
    if not isinstance(feed, dict) or not isinstance(feed.get('items'), list):
        raise RemoteError('Flickr 공개 피드 형식이 변경됐습니다.')
    photos = []
    seen = set()
    for item in feed['items'][:20]:
        if not isinstance(item, dict) or not isinstance(item.get('media'), dict):
            continue
        page, preview = item.get('link'), item['media'].get('m')
        if not isinstance(page, str) or not isinstance(preview, str):
            continue
        try:
            validate_url(page, ['www.flickr.com'])
            validate_url(preview, SPEC['domains'][1:])
        except (RemoteError, ValueError):
            continue
        parts = urlsplit(page).path.strip('/').split('/')
        if len(parts) != 3 or parts[0] != 'photos' or not parts[2].isdigit():
            continue
        key = parts[2]
        if key in seen:
            continue
        seen.add(key)
        photos.append(dict(page=page, preview=preview, key=key,
                           title=str(item.get('title', ''))[:500],
                           published=str(item.get('published', ''))[:100],
                           author=str(item.get('author', ''))[:300]))
    return photos


class FlickrFeedProvider:
    name = SPEC['id']

    def __init__(self, online):
        self.online = online
        online.web.repo.save_spec(SPEC)

    def search_images(self, query, cursor, cancelled):
        if cursor is not None:
            raise RemoteError('공개 태그 피드에는 검증된 다음 페이지가 없습니다.')
        web = self.online.web
        web.repo.require(self.name, 'metadata_search')
        if cancelled.is_set():
            raise Cancelled()
        # The literal term is a tag hint, not proof of depicted identity or pose.
        url = ENDPOINT + '?' + urlencode(dict(tags=query, tagmode='all',
                                              format='json', nojsoncallback=1))
        try:
            raw, _ = web.read(self.name, url, cancelled, 1024 * 1024, 'application/json')
            entries = []
            for photo in parse_feed(raw):
                if cancelled.is_set():
                    raise Cancelled()
                web.repo.require(self.name, 'metadata_search')
                page_id = web.repo.add_page(self.name, photo['page'])
                parsed = ParsedPage(photo['title'], [ImageCandidate(
                    photo['preview'], photo['key'], photo['title'], 'thumbnail',
                    provenance=[dict(source='public_feed.media.m', author=photo['author'],
                                     original_verified=False, person_confirmed=False)],
                )], metadata=dict(query=query, tag_search=True, search_managed=True, original_verified=False,
                                  published_declared=photo['published'],
                                  full_search=False))
                web.repo.index_page(web.repo.page(page_id), parsed)
                rows = web.repo.images(page_id=page_id)
                allow_reviewed_previews(web.repo, self.name, rows)
                entries.extend('web:' + row['id'] for row in rows)
            self._audit(dict(query=query, state='feed_received', candidate_count=len(entries),
                             original_verified=False, stage1_complete=False))
            return list(dict.fromkeys(entries)), None
        except Cancelled:
            raise
        except Exception as error:
            self._audit(dict(query=query, state='error', error=str(error), stage1_complete=False))
            raise

    def _audit(self, data):
        folder = self.online.settings.root / 'search-reports'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'flickr-{time.time_ns()}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
