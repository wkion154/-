import threading
import pytest
from app.providers.kpopping_scroll import capture_batch,capture_replayed_batch
from app.providers.http import RemoteError,Cancelled

class Page:
    url='https://kpopping.com/kpics?idol=fixture&idolName=Wonyoung'
    def __init__(self):self.position=0
    def title(self):return 'Fixture, not actual online evidence'
    def locator(self,_):return self
    def evaluate_all(self,_):
        return [dict(currentSrc=f'https://kpopping.com/api/kpics-image?url=https%3A%2F%2Fcdn.kpopping.com%2Fkpics%2Ffixture-{self.position}.jpg&size=thumb',alt='fixture')]
    def evaluate(self,_):self.position+=1
    def wait_for_timeout(self,_):pass
    def get_by_role(self,role,name):
        self.badge_matches=bool(name.fullmatch(getattr(self,'badge','Filtering: Wonyoung')));return self
    def count(self):return int(self.badge_matches)
    def nth(self,i):return self
    def is_visible(self):return getattr(self,'badge_visible',True)


def test_virtualized_scroll_keeps_previous_photos_without_duplicate():
    page=Page();rows,cursor,_=capture_batch(page,'장원영',None,threading.Event(),rounds=4)
    assert len(rows)==4
    more,cursor,status=capture_batch(page,'장원영',cursor,threading.Event(),rounds=4)
    assert len(more)==3 and len(cursor['seen'])==7 and not status['exhaustion_verified']


def test_all_feed_or_changed_artist_is_not_mixed():
    page=Page();_,cursor,_=capture_batch(page,'장원영',None,threading.Event(),rounds=1)
    page.url='https://kpopping.com/kpics'
    with pytest.raises(RemoteError):capture_batch(page,'장원영',cursor,threading.Event())
    page.url='https://kpopping.com/kpics?idol=different'
    with pytest.raises(RemoteError):capture_batch(page,'장원영',cursor,threading.Event())


def test_cancel_stops_before_page_access():
    cancel=threading.Event();cancel.set()
    with pytest.raises(Cancelled):capture_batch(Page(),'장원영',None,cancel)


def test_replay_collects_intermediate_virtualized_photos_and_skips_known_keys():
    cancel=threading.Event()
    first,cursor=capture_replayed_batch(Page(),'장원영',0,None,cancel)
    assert len(first)==4
    # A new browser process reopens at zero; intermediate new cards must survive.
    more,next_cursor=capture_replayed_batch(Page(),'장원영',2,cursor,cancel)
    assert len(more)==6 and len(next_cursor['seen'])==10
    assert not {item.logical_key for item in first}&{item.logical_key for item in more}


@pytest.mark.parametrize('badge,visible',[('All',True),('Filtering: Other',True),('Filtering: Wonyoung',False)])
def test_valid_url_without_visible_matching_filter_never_collects(badge,visible):
    page=Page();page.badge=badge;page.badge_visible=visible
    with pytest.raises(RemoteError,match='실제 화면'):
        capture_batch(page,'장원영',None,threading.Event())
    assert page.position==0


def test_filter_disappearing_without_url_change_stops_next_batch():
    page=Page();_,cursor,_=capture_batch(page,'장원영',None,threading.Event(),rounds=1)
    page.badge='All'
    with pytest.raises(RemoteError,match='실제 화면'):
        capture_batch(page,'장원영',cursor,threading.Event())


def test_group_filter_is_collected_and_cannot_replay_as_person():
    page=Page();page.url='https://kpopping.com/kpics?group=fixture&groupName=IVE';page.badge='Filtering: IVE'
    rows,cursor,_=capture_batch(page,'IVE',None,threading.Event(),rounds=1)
    assert len(rows)==1 and cursor['artist_filter']==[['group:fixture'],['IVE']]
    page.url='https://kpopping.com/kpics?idol=fixture&idolName=IVE'
    with pytest.raises(RemoteError,match='바뀌었습니다'):
        capture_batch(page,'IVE',cursor,threading.Event())


@pytest.mark.parametrize('query',[
    'group=fixture&groupName=IVE&idol=person&idolName=Wonyoung',
    'group=fixture&group=other&groupName=IVE',
    'idol=fixture&idolName=Wonyoung&idolName=Other',
])
def test_ambiguous_filter_never_collects(query):
    page=Page();page.url='https://kpopping.com/kpics?'+query
    with pytest.raises(RemoteError):capture_batch(page,'IVE',None,threading.Event())
