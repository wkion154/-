import threading
from app.browser_search_worker import BrowserSearchFailure,failure_payload,remote_failure
from app.analysis_filters import AnalysisFilter
from test_search import searches


def test_http_rejection_keeps_status_location_and_stops_aliases(searches):
    search,provider=searches;calls=[]
    search.db.add_entity('person','이서',aliases='Leeseo')
    def rejected(*args):
        calls.append(1)
        raise remote_failure(failure_payload(BrowserSearchFailure(
            'Site denied',stage='listing',status=403,url='https://kpopping.com/kpics')))
    provider.search_images=rejected
    from app.search_check import check
    report=check(search.online,'이서',search=search)
    assert len(calls)==1 and not report['checks_passed']
    assert report['failure_details']==[dict(stage='listing',http_status=403,
        page_url='https://kpopping.com/kpics',blocked=True)]


def test_missing_name_can_try_registered_alias_without_disabling_source(searches):
    search,provider=searches;calls=[]
    search.db.add_entity('person','이서',aliases='Leeseo')
    def missing(query,*args):
        calls.append(query)
        if query=='이서':
            raise remote_failure(failure_payload(BrowserSearchFailure(
                'Name not found',stage='entity_selection',blocked=False,url='https://kpopping.com/kpics')))
        return [],None
    provider.search_images=missing
    session=search.start('이서',AnalysisFilter())
    assert search.page(session,threading.Event())['state']=='ready'
    assert search.page(session,threading.Event())['state']=='exhausted'
    assert calls==['이서','Leeseo']


def test_legacy_worker_error_remains_blocked():
    assert remote_failure(dict(error='Unknown older worker failure')).blocked
