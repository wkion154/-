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
