import threading
from scripts.check_search_readiness import probe
from app.providers.http import RemoteError


def test_blocked_metadata_is_never_reported_as_working_search():
    class Web:
        def read(self, *args):
            raise RemoteError('robots denied', blocked=True)
    result = probe(Web(), {'id': 'fixture'}, 'https://fixture.example/feed',
                   'feed', threading.Event())
    assert result['status'] == 'blocked'
    assert not result['metadata_access'] and not result['stage1_complete']


def test_http_200_with_artist_query_is_not_filter_or_viewer_validation():
    class Web:
        def read(self, *args):
            return b'<title>All artists</title><img src="/logo.svg">', {}
    result = probe(Web(), {'id': 'fixture'}, 'https://fixture.example/?idol=123',
                   'page', threading.Event())
    assert result['metadata_access'] and result['title'] == 'All artists'
    assert not result['artist_filter_verified'] and not result['viewer_verified']
    assert not result['save_verified'] and not result['stage1_complete']
