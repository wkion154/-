"""Read-only live metadata diagnosis. Never grants rights or downloads photos."""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import Settings
from app.database import Database
from app.providers.flickr_feed import SPEC as FLICKR, ENDPOINT, parse_feed
from app.providers.kpopping_browser import SPEC as KPOPPING, OBSERVED_PAGES
from app.providers.http import RemoteError
from app.web_service import WebService


class PageSummary(HTMLParser):
    def __init__(self):
        super().__init__(); self.in_title = False; self.title = ''; self.images = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'title': self.in_title = True
        if tag == 'img': self.images += 1

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False

    def handle_data(self, data):
        if self.in_title: self.title += data


def probe(web, spec, url, kind, cancelled):
    result = dict(source=spec['id'], url=url, metadata_access=False,
                  artist_filter_verified=False, viewer_verified=False,
                  save_verified=False, stage1_complete=False)
    try:
        # WebService checks the destination's robots before requesting metadata.
        raw, _ = web.read(spec['id'], url, cancelled, 2 * 1024 * 1024,
                          'application/json' if kind == 'feed' else 'text/html')
        result['metadata_access'] = True
        if kind == 'feed':
            result.update(status='feed_metadata_only', candidates=len(parse_feed(raw)))
        else:
            page = PageSummary(); page.feed(raw.decode('utf-8', 'replace'))
            result.update(status='page_only_filter_unverified', title=page.title,
                          html_image_elements=page.images,
                          note='A query string and HTTP 200 do not prove the selected artist filter. Windows browser validation remains required.')
    except RemoteError as error:
        result.update(status='blocked' if error.blocked else 'error',
                      error=str(error), http_status=error.status)
    except Exception as error:
        result.update(status='error', error=str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--query', default='jangwonyoung', help='Flickr tag hint only')
    args = parser.parse_args()
    cancel = threading.Event()
    with tempfile.TemporaryDirectory(prefix='kidol-readiness-') as directory:
        root = Path(directory)
        web = WebService(Database(root / 'probe.sqlite3'), Settings(root / 'settings'))
        for spec in (KPOPPING, FLICKR): web.repo.save_spec(spec)
        report = dict(checked_at=datetime.now(timezone.utc).isoformat(),
                      photo_downloads_attempted=False, permissions_changed=False,
                      stage1_complete=False, sources=[
                          probe(web, KPOPPING, OBSERVED_PAGES['장원영'], 'page', cancel),
                          probe(web, FLICKR, ENDPOINT + '?' + urlencode(dict(
                              tags=args.query, format='json', nojsoncallback=1)), 'feed', cancel)])
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(output)
    # Metadata checks cannot establish completion of the app's end-to-end flow.
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
