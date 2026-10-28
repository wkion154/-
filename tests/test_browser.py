"""Actual Chromium, with sandbox on and no external network responses."""
import os
import importlib.util
import shutil
import threading
import pytest
from app.source_specs import SourceSpec
from app.rendered_page import render_page
from app.page_discovery import discover_html

pytestmark=pytest.mark.skipif(os.environ.get('K_IDOL_RUN_BROWSER_TESTS')!='1' or importlib.util.find_spec('playwright') is None,
    reason='Optional sandboxed Chromium integration; set K_IDOL_RUN_BROWSER_TESTS=1 in a browser-capable executor.')


def test_javascript_injected_image_is_discovered_without_image_downloads():
    spec=SourceSpec('fixture','Owned fixture','user','rendered',('fixture.example',))
    requests=[]
    def fetch(url):requests.append(url);raise AssertionError('This fixture needs no network resources')
    html='''<!doctype html><html><body><script>
      const photo=document.createElement('img');photo.src='/provided.jpg';photo.width=300;photo.height=400;photo.alt='Synthetic';document.body.append(photo);
      const socket=new WebSocket('wss://unapproved.example/leak');
      fetch('https://unapproved.example/leak');
    </script></body></html>'''
    result=render_page(html,'https://fixture.example/page',spec,fetch,threading.Event())
    parsed=discover_html(result,'https://fixture.example/page')
    assert [item.url for item in parsed.candidates]==['https://fixture.example/provided.jpg']
    assert requests==[]


def test_approved_external_script_is_mediated_and_injected_images_are_not_fetched():
    spec=SourceSpec('fixture','Owned fixture','user','rendered',('fixture.example',))
    requests=[]
    def fetch(url):
        requests.append(url)
        assert url=='https://fixture.example/app.js'
        return b'const image=document.createElement("img");image.src="/real-declaration.png";image.width=100;image.height=200;document.body.append(image);',{'Content-Type':'application/javascript'}
    html='<!doctype html><html><body><script src="/app.js"></script></body></html>'
    result=render_page(html,'https://fixture.example/page',spec,fetch,threading.Event())
    assert [x.url for x in discover_html(result,'https://fixture.example/page').candidates]==['https://fixture.example/real-declaration.png']
    assert requests==['https://fixture.example/app.js']
