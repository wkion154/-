import io
import json
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image,ImageDraw
from app.source_specs import SourceSpec,OPERATIONS
from app.source_repository import SourceRepository
from app.page_discovery import discover_html,srcset_values,choose_version,clean_url
from app.web_service import WebService
from app.providers.http import RemoteError,Cancelled,validate_url
from app.online_service import OnlineService

EVIDENCE={'reference':'fixture://owned-test-data','note':'Owned synthetic fixture; this is not a live site policy review.'}

class Response(io.BytesIO):
    def __init__(self,body,mime):super().__init__(body);self.headers={'Content-Type':mime,'Content-Length':str(len(body))}

class FixtureHttp:
    allowed_hosts=frozenset({'photos.example','cdn.example'})
    def __init__(self,resources):self.resources=resources;self.calls=[]
    def open(self,url,cancelled,accept=None):
        self.calls.append(url)
        if cancelled.is_set():raise Cancelled()
        if url.endswith('/robots.txt'):return Response(b'User-agent: *\nAllow: /','text/plain')
        if url not in self.resources:raise RemoteError('Fixture resource unavailable',status=404)
        body,mime=self.resources[url]
        return Response(body,mime)

@pytest.fixture
def web(db,settings):
    resources={}
    client=FixtureHttp(resources)
    service=WebService(db,settings,http_factory=lambda spec:client)
    service.repo.save_spec(dict(id='owned',name='Owned synthetic source',category='official',adapter='html',domains=['photos.example','cdn.example'],original_attributes=['data-original']))
    return service,client,resources


def permit(repo,operation,resource=None,source='owned'):
    repo.grant(source,operation,'allowed',EVIDENCE,resource)
    repo.enable(source,True)


def image_bytes(size=(800,1200),color='purple',fmt='PNG'):
    out=io.BytesIO();image=Image.new('RGB',size,color)
    draw=ImageDraw.Draw(image)
    for i in range(0,size[0],50):draw.rectangle((i,0,i+25,size[1]-1),fill=(i%255,100,180))
    image.save(out,format=fmt);return out.getvalue()


def indexed(web,html='<img src="https://cdn.example/a.png" alt="Artist A">'):
    service,client,resources=web
    permit(service.repo,'metadata_search')
    page=service.repo.add_page('owned','https://photos.example/page')
    resources['https://photos.example/page']=(html.encode(),'text/html; charset=utf-8')
    service.scan(page,threading.Event())
    return page,service.repo.images()[0]


def test_html_srcset_picture_metadata_no_url_guessing():
    html='''<title>Artist A</title><base href="https://evil.example/">
    <picture><source srcset="/wide.webp 1600w, /huge.webp 3200w"><img src="/small.jpg" srcset="/small.jpg 400w, /large.jpg 2400w" data-original="/provided.jpg" alt="Artist A"></picture>
    <meta property="og:image" content="https://cdn.example/og.jpg"><img src="data:image/png;base64,AAAA"><img src="http://photos.example/insecure.jpg">'''
    result=discover_html(html,'https://photos.example/page',('data-original',))
    urls={x.url for x in result.candidates}
    assert 'https://photos.example/large.jpg' in urls
    assert 'https://photos.example/provided.jpg' in urls
    assert 'https://cdn.example/og.jpg' in urls
    assert not any('evil.example' in x or 'insecure' in x for x in urls)
    assert not any('original-large' in x for x in urls)
    assert len({x.logical_key for x in result.candidates if x.role=='responsive'})==1
    assert next(x for x in result.candidates if x.url.endswith('provided.jpg')).role=='source_declared_original'


def test_srcset_data_payload_does_not_become_fake_relative_url():
    values=srcset_values('data:image/png;base64,AAAA 1x, /large.jpg 2000w, //cdn.example/x.jpg 2x')
    assert values==[('/large.jpg','2000w'),('//cdn.example/x.jpg','2x')]
    assert srcset_values('a.jpg, b.jpg')==[('a.jpg',''),('b.jpg','')]


def test_jsonld_publication_is_not_capture_date():
    html='''<script type="application/ld+json">{"@type":"Article","datePublished":"2026-10-01T11:00:00+09:00","image":{"@type":"ImageObject","contentUrl":"/a.jpg","width":4000,"height":6000}}</script>'''
    parsed=discover_html(html,'https://photos.example/story')
    assert parsed.published=='2026-10-01T11:00:00+09:00'
    assert len(parsed.candidates)==1
    assert parsed.candidates[0].declared_width==4000
    assert not hasattr(parsed,'captured')
    assert discover_html('<img src="">','https://photos.example/story').candidates==[]


@pytest.mark.parametrize('html',['<input type=password><img src=/a.jpg>','<iframe src="https://hcaptcha.com/frame"></iframe>','<div class="captcha-widget"></div>'])
def test_access_barriers_block_without_attempted_bypass(html):
    with pytest.raises(RemoteError):discover_html(html,'https://photos.example/page')


def test_discovery_has_bounded_candidate_count():
    parsed=discover_html(''.join(f'<img src="/{i}.jpg">' for i in range(2000)),'https://photos.example/page')
    assert len(parsed.candidates)==300


def test_manifest_is_data_only_and_policy_invalidation(db):
    repo=SourceRepository(db)
    data=dict(id='owned',name='Owned',category='official',adapter='html',domains=['photos.example'])
    repo.save_spec(data);permit(repo,'metadata_search')
    assert repo.allowed('owned','metadata_search')
    repo.save_spec(data|{'domains':['photos.example','cdn.example']})
    assert not repo.allowed('owned','metadata_search')
    with pytest.raises(ValueError):SourceSpec.load(data|{'python_module':'os'})
    with pytest.raises(ValueError):SourceSpec.load(data|{'domains':['*.example']})
    with pytest.raises(ValueError):repo.enable('instagram',True)


def test_permissions_are_independent_and_batches_atomic(db):
    repo=SourceRepository(db)
    repo.save_spec(dict(id='owned',name='Owned',category='user',adapter='html',domains=['photos.example']))
    permit(repo,'metadata_search')
    assert not repo.allowed('owned','thumbnail')
    assert not repo.allowed('owned','original_download')
    permit(repo,'original_download')
    permit(repo,'original_download','web:item')
    assert repo.allowed('owned','original_download','web:item')
    assert not repo.allowed('owned','local_analysis','web:item')
    assert not repo.allowed('owned','ai_restore_input','web:item')
    assert not repo.allowed('owned','result_storage','web:item')
    with pytest.raises(ValueError):repo.grant_many('owned',{'metadata_search':'denied','thumbnail':'allowed'}, {})
    assert repo.allowed('owned','metadata_search')
    repo.grant('owned','original_download','denied',EVIDENCE)
    assert not repo.allowed('owned','original_download','web:item')


def test_metadata_only_scan_makes_no_image_requests(web):
    service,client,resources=web
    page,item=indexed(web)
    assert client.calls==['https://photos.example/robots.txt','https://photos.example/page']
    assert item['width'] is None and item['cache_path'] is None
    assert service.db.query()[1]==0
    with pytest.raises(RemoteError):service.probe(item['id'],threading.Event())
    assert not any('cdn.example' in x for x in client.calls)


def test_robots_denial_blocks_page_but_not_other_providers(web):
    service,client,resources=web
    permit(service.repo,'metadata_search')
    original=client.open
    def blocked(url,cancelled,accept=None):
        if url.endswith('/robots.txt'):return Response(b'User-agent: *\nDisallow: /','text/plain')
        return original(url,cancelled,accept)
    client.open=blocked
    page=service.repo.add_page('owned','https://photos.example/page')
    with pytest.raises(RemoteError,match='robots'):service.scan(page,threading.Event())
    assert service.repo.images()==[]
    assert not service.repo.allowed('wikimedia','metadata_search')


def test_metadata_fetch_rejects_image_body_without_binary_permission(web):
    service,client,resources=web
    permit(service.repo,'metadata_search')
    page=service.repo.add_page('owned','https://photos.example/not-html')
    resources['https://photos.example/not-html']=(image_bytes(),'image/png')
    with pytest.raises(RemoteError,match='텍스트'):service.scan(page,threading.Event())
    assert service.repo.images()==[]


def test_measured_versions_choose_actual_dimensions_not_url_names(web):
    service,client,resources=web
    page,item=indexed(web,'<img srcset="https://cdn.example/original-8K.png 8000w, https://cdn.example/ordinary.png 1200w">')
    images=service.repo.images(page_id=page)
    by_url={x['url']:x for x in images}
    resources['https://cdn.example/original-8K.png']=(image_bytes((200,300)),'image/png')
    resources['https://cdn.example/ordinary.png']=(image_bytes((1200,1800)),'image/png')
    permit(service.repo,'original_download')
    for candidate in images:
        permit(service.repo,'original_download','web:'+candidate['id'])
        service.probe(candidate['id'],threading.Event())
    best=service.best(item['id'])
    assert best['url']=='https://cdn.example/ordinary.png'
    assert best['width']==1200 and best['height']==1800
    assert service.db.query()[1]==0
    with pytest.raises(RemoteError):service.preview(best['id'],threading.Event())
    permit(service.repo,'thumbnail');permit(service.repo,'thumbnail','web:'+best['id'])
    assert service.preview(best['id'],threading.Event()).height()==512


def test_same_bytes_multiple_sources_dedupe_without_losing_provenance(web):
    service,client,resources=web
    page,item=indexed(web,'<img src="https://cdn.example/a.png"><img src="https://cdn.example/b.png">')
    png=image_bytes((160,240))
    resources.update({'https://cdn.example/a.png':(png,'image/png'),'https://cdn.example/b.png':(png,'image/png')})
    permit(service.repo,'original_download')
    images=service.repo.images(page_id=page)
    ids=[]
    for row in images:
        permit(service.repo,'original_download','web:'+row['id'])
        service.probe(row['id'],threading.Event())
        ids.append(service.download(row['id'],threading.Event())['asset_id'])
    assert ids[0]==ids[1] and service.db.query()[1]==1
    assert service.repo.matches(images[0]['id'])[0]['kind']=='exact_bytes'
    with service.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM asset_sources WHERE asset_id=? AND item_id<>''",(ids[0],)).fetchone()[0]==2
    assert service.db.query(source_filter=('category','official'))[1]==1
    assert service.db.query(source_filter=('site','owned'))[1]==1


def test_changed_remote_bytes_never_replace_saved_original_and_reset_rights(web):
    service,client,resources=web
    page,item=indexed(web)
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+item['id'])
    first=image_bytes((120,180),'purple');resources[item['url']]=(first,'image/png')
    service.probe(item['id'],threading.Event())
    saved=service.download(item['id'],threading.Event())
    original=Path(service.db.query()[0][0]['path']);assert original.read_bytes()==first
    resources[item['url']]=(image_bytes((240,360),'blue'),'image/png')
    result=service.probe(item['id'],threading.Event())
    assert result['rights_review_required']
    assert service.repo.image(item['id'])['local_asset_id'] is None
    assert not service.repo.allowed('owned','original_download','web:'+item['id'])
    assert original.read_bytes()==first
    with service.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM web_revisions WHERE item_id=?',(item['id'],)).fetchone()[0]==2
        assert conn.execute('SELECT asset_id FROM web_revisions WHERE item_id=? AND asset_id IS NOT NULL',(item['id'],)).fetchone()[0]==saved['asset_id']


def test_metadata_name_matches_are_proposals_and_multiple_confirmed_people_share_file(web):
    service,client,resources=web
    a=service.db.add_entity('person','Artist A',gender='female')
    b=service.db.add_entity('person','Artist B',gender='female')
    page,item=indexed(web,'<title>Artist A and Artist B interview</title><img src="https://cdn.example/a.png" alt="Stage">')
    assert {x['entity_id'] for x in service.repo.proposals(item['id'])}=={a,b}
    assert all(x['status']=='unconfirmed' for x in service.repo.proposals(item['id']))
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+item['id'])
    resources[item['url']]=(image_bytes((80,120)),'image/png')
    service.probe(item['id'],threading.Event())
    identity=service.download(item['id'],threading.Event(),[a,b])['asset_id']
    assert {x['id'] for x in service.db.associations(identity)[0]}=={a,b}
    assert service.db.query(('person',a))[1]==service.db.query(('person',b))[1]==1
    assert service.db.query()[1]==1


def test_confirmed_male_only_is_hidden_by_default_but_retained_in_local_library(web):
    service,client,resources=web
    male=service.db.add_entity('person','Male Member',gender='male')
    _,item=indexed(web)
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+item['id'])
    resources[item['url']]=(image_bytes((80,120)),'image/png')
    service.probe(item['id'],threading.Event());service.download(item['id'],threading.Event(),[male])
    assert service.db.query()[1]==0
    assert service.db.query(('local',None))[1]==1
    # A later explicit female association must immediately restore the default view.
    female=service.db.add_entity('person','Female Member',gender='female')
    from app.images import inspect_image
    row=service.db.query(('local',None))[0][0]
    service.db.register(inspect_image(Path(row['path'])),person=female)
    assert service.db.query()[1]==1


def test_perceptual_match_requires_separate_analysis_grants_and_never_merges(web):
    service,client,resources=web
    page,item=indexed(web,'<img src="https://cdn.example/a.jpg"><img src="https://cdn.example/b.jpg">')
    png=image_bytes((180,240),fmt='JPEG')
    im=Image.open(io.BytesIO(png));out=io.BytesIO();im.save(out,format='JPEG',quality=65)
    resources.update({'https://cdn.example/a.jpg':(png,'image/jpeg'),'https://cdn.example/b.jpg':(out.getvalue(),'image/jpeg')})
    permit(service.repo,'original_download')
    rows=service.repo.images(page_id=page)
    for row in rows:
        permit(service.repo,'original_download','web:'+row['id']);service.probe(row['id'],threading.Event())
        with pytest.raises(RemoteError):service.analyze(row['id'],threading.Event())
    permit(service.repo,'local_analysis')
    for row in rows:permit(service.repo,'local_analysis','web:'+row['id']);service.analyze(row['id'],threading.Event())
    assert any(x['kind']=='perceptual_candidate' for x in service.repo.matches(rows[0]['id']))
    assert len(service.repo.images())==2 and service.db.query()[1]==0


def test_analysis_and_network_claims_are_independent(db):
    from app.online_repository import OnlineRepository
    queue=OnlineRepository(db)
    analysis=queue.enqueue('web_analyze',{'item_id':'x'})
    network=queue.enqueue('web_scan',{'page_id':'y'})
    assert queue.claim()['id']==network
    assert queue.claim('analysis')['id']==analysis


def test_web_source_does_not_depend_on_wikimedia_review(db,settings):
    class Provider:
        http=None
    service=OnlineService(db,settings,Provider())
    service.web.repo.save_spec(dict(id='direct',name='Direct fixture',category='user',adapter='direct',domains=['photos.example']))
    permit(service.web.repo,'metadata_search',source='direct')
    page=service.web.repo.add_page('direct','https://photos.example/declared.png','direct')
    service.repo.enqueue('web_scan',{'page_id':page})
    result=service.run_one(threading.Event())
    assert result['state']=='done' and len(service.web.repo.images())==1
    assert not service.repo.reviewed()


def test_thumbnail_permission_only_fetches_declared_thumbnail_not_original(web):
    service,client,resources=web
    html='<script type="application/ld+json">{"@type":"ImageObject","contentUrl":"https://cdn.example/original.png","thumbnailUrl":"https://cdn.example/thumb.png"}</script>'
    page,_=indexed(web,html)
    rows=service.repo.images(page_id=page)
    thumb=next(x for x in rows if x['role']=='thumbnail')
    original=next(x for x in rows if x['role']!='thumbnail')
    resources[thumb['url']]=(image_bytes((200,300)),'image/png')
    permit(service.repo,'thumbnail');permit(service.repo,'thumbnail','web:'+thumb['id'])
    assert service.preview(thumb['id'],threading.Event()).height()==300
    assert not service.repo.allowed('owned','original_download','web:'+thumb['id'])
    assert original['url'] not in client.calls
    assert service.db.query()[1]==0


def test_scan_keeps_published_date_separate_from_capture_date(web):
    service,client,resources=web
    page,item=indexed(web,'<meta property="article:published_time" content="2026-10-28T10:00:00+09:00"><img src="https://cdn.example/a.png">')
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+item['id'])
    resources[item['url']]=(image_bytes((100,150)),'image/png')
    service.probe(item['id'],threading.Event());service.download(item['id'],threading.Event())
    row=service.db.query()[0][0]
    assert row['captured'] is None
    assert row['published']=='2026-10-28T10:00:00+09:00'


def test_manual_analysis_does_not_start_unrelated_network_when_automatic_is_off(qtbot,web,settings):
    from app.online_repository import OnlineRepository
    from app.online_runner import OnlineRunner
    service,client,resources=web
    settings.values['online_enabled']=False
    repo=OnlineRepository(service.db)
    network=repo.enqueue('web_scan',{'page_id':'unrelated'})
    analysis=repo.enqueue('web_analyze',{'item_id':'requested'})
    class FakeService:
        def __init__(self):self.repo=repo;self.calls=[]
        def run_one(self,cancelled,queue='network',job_id=None):
            job=self.repo.claim(queue,job_id)
            if not job:return None
            self.calls.append(job['id']);self.repo.finish(job['id'],'done')
            return dict(id=job['id'],state='done',library_changed=False)
    backend=FakeService();runner=OnlineRunner(backend,settings)
    runner.request(analysis,'analysis')
    qtbot.waitUntil(lambda:not runner.analysis_running)
    assert backend.calls==[analysis]
    assert repo.job_state(network)=='queued'
    # Re-requesting a completed ID must not create a permanent polling loop.
    runner.request(analysis,'analysis');qtbot.waitUntil(lambda:not runner.analysis_running)
    assert not runner.requests['analysis']
    runner.stop()


def test_main_gallery_source_filter_and_local_visibility(qtbot,web,settings,tmp_path):
    from app.main_window import MainWindow
    from app.images import inspect_image
    service,client,resources=web
    _,item=indexed(web)
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+item['id'])
    resources[item['url']]=(image_bytes((90,140)),'image/png')
    service.probe(item['id'],threading.Event());service.download(item['id'],threading.Event())
    local=tmp_path/'local.png';Image.new('RGB',(30,50),'green').save(local);service.db.register(inspect_image(local))
    window=MainWindow(service.db,settings);qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.model.total==2)
    index=window.source_filter.findData('category:official')
    assert index>=0
    window.source_filter.setCurrentIndex(index)
    qtbot.waitUntil(lambda:window.model.total==1)
    assert tuple(window.model.source_filter)==('category','official')
    window.source_filter.setCurrentIndex(window.source_filter.findData('category:user'))
    qtbot.waitUntil(lambda:window.model.total==1 and window.model.rows[0]['name']=='local.png')
    window.close()


def test_source_pane_link_registration_and_shutdown(qtbot,db,settings):
    from app.online_runner import OnlineRunner
    from app.source_pane import SourcePane
    class Provider: http=None
    service=OnlineService(db,settings,Provider());runner=OnlineRunner(service,settings)
    pane=SourcePane(service,runner);qtbot.addWidget(pane);pane.show()
    qtbot.waitUntil(lambda:not pane.loading)
    pane.url.setText('https://photos.example/my-page')
    pane.add_url()
    qtbot.waitUntil(lambda:len(service.web.repo.pages())==1)
    assert service.web.repo.pages()[0]['status']=='linked'
    assert not service.web.repo.allowed(service.web.repo.pages()[0]['source_id'],'metadata_search')
    pane.shutdown();runner.changed.emit();runner.stop()
    assert pane.closed and pane.tasks.stopped


def test_declared_euc_kr_metadata_decoding_keeps_korean_names(web):
    service,client,resources=web
    permit(service.repo,'metadata_search')
    page=service.repo.add_page('owned','https://photos.example/korean')
    resources['https://photos.example/korean']=('<title>테스트 인물</title><img src="/photo.jpg" alt="테스트 인물">'.encode('euc-kr'),'text/html; charset=euc-kr')
    service.scan(page,threading.Event())
    assert service.repo.page(page)['title']=='테스트 인물'
    assert service.repo.images(page_id=page)[0]['title']=='테스트 인물'


def test_builtin_api_profiles_cannot_be_replaced_with_arbitrary_html(db):
    repo=SourceRepository(db)
    data=repo.spec('wikimedia').dictionary()
    with pytest.raises(ValueError):repo.save_spec(data|dict(adapter='html',domains=['unapproved.example']))
    assert repo.spec('wikimedia').adapter=='wikimedia'


def test_missing_confirmed_person_fails_before_publishing_file(web):
    service,client,resources=web
    _,item=indexed(web)
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+item['id'])
    with pytest.raises(ValueError):service.download(item['id'],threading.Event(),['missing-person'])
    assert service.db.query()[1]==0
    assert not service.settings.storage.exists()


def test_v2_to_v3_migration_rolls_back_all_ddl_and_retains_backup(tmp_path,monkeypatch):
    import sqlite3
    from app.database import Database,SCHEMA
    from app.online_schema import SCHEMA_V2
    import app.source_schema as schema
    path=tmp_path/'v2.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA);conn.executescript(SCHEMA_V2)
        conn.execute("INSERT INTO groups VALUES('kept','User Group','mixed','')")
        conn.execute('INSERT INTO schema_migrations VALUES(1,0)');conn.execute('INSERT INTO schema_migrations VALUES(2,0)')
        conn.execute('PRAGMA user_version=2')
    original=schema.SCHEMA_V3
    monkeypatch.setattr(schema,'SCHEMA_V3',original+'; NOT VALID SQL;')
    with pytest.raises(sqlite3.OperationalError):Database(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0]==2
        assert conn.execute('SELECT name FROM groups').fetchone()[0]=='User Group'
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_profiles'").fetchone()
    assert len(list(tmp_path.glob('*.v2-*.bak')))==1
    monkeypatch.setattr(schema,'SCHEMA_V3',original)
    assert Database(path).entities()['groups'][0]['name']=='User Group'


def test_commons_refresh_never_overwrites_explicit_file_permission_denial(db):
    from app.online_repository import OnlineRepository
    queue=OnlineRepository(db);sources=SourceRepository(db)
    queue.candidate(dict(external_id='Q1234',kind='group',name='Synthetic Group'))
    queue.accept('Q1234');target=queue.targets()[0]['id']
    image=dict(source_id='1234-fixture',source_url='https://commons.wikimedia.org/wiki/File:Fixture.png',original_url='https://upload.wikimedia.org/fixture.png',thumbnail_url=None,title='Fixture',width=100,height=100,size=100,mime='image/png',source_sha1=None,license_name='CC0',license_url='https://creativecommons.org/publicdomain/zero/1.0/',attribution='',restrictions='',license_status='eligible',captured=None,published=None)
    queue.index_images(target,[image],None)
    resource='commons:'+queue.images()[0]['id']
    sources.grant('wikimedia','original_download','denied',EVIDENCE,resource)
    queue.index_images(target,[image],None)
    assert sources.permissions('wikimedia',resource)['original_download']['state']=='denied'
    assert sources.permissions('wikimedia',resource)['thumbnail']['state']=='allowed'
    assert sources.permissions('wikimedia',resource)['ai_restore_input']['state']=='review'
