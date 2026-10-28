import html
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from .http import RemoteError
from ..extensions import ProviderPolicy

POLICY_URLS = (
 'https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_Terms_of_Use',
 'https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy',
 'https://www.mediawiki.org/wiki/API:Etiquette',
 'https://www.wikidata.org/wiki/Wikidata:Licensing',
 'https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia',
 'https://commons.wikimedia.org/robots.txt',
 'https://www.wikidata.org/robots.txt',
)

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[]
    def handle_data(self,data):self.parts.append(data)

def plain(value):
    parser=PlainText();parser.feed(str(value or ''))
    return html.unescape(' '.join(parser.parts)).strip()

def qid(value):
    result=str(value).rsplit('/',1)[-1]
    if not re.fullmatch(r'Q[1-9][0-9]*',result):raise ValueError('Wikidata Q ID를 입력하세요.')
    return result

def reuse_status(name,url,restrictions,attribution=''):
    """Conservative per-file license screening; never grants AI/identity rights."""
    parsed=urlsplit(url)
    path=parsed.path.rstrip('/')
    if parsed.scheme!='https' or parsed.hostname not in ('creativecommons.org','www.creativecommons.org'):
        return 'review'
    accepted=bool(re.fullmatch(r'/licenses/(by|by-sa)/(1\.0|2\.0|2\.5|3\.0|4\.0)',path) or path in ('/publicdomain/zero/1.0','/publicdomain/mark/1.0'))
    requires_credit=path.startswith('/licenses/')
    return 'eligible' if accepted and name and not restrictions and (not requires_credit or attribution) else 'review'

def canonical_sha1(value):
    if not value:return None
    value=str(value).lower()
    if len(value)==40 and all(c in '0123456789abcdef' for c in value):return value
    try:
        number=int(value,36)
        if number<0 or number>=2**160:raise ValueError()
        return f'{number:040x}'
    except ValueError:raise RemoteError('제공처 파일 해시 형식을 확인하세요.') from None

class WikimediaProvider:
    name='wikimedia'
    policy=ProviderPolicy(name='Wikidata / Wikimedia Commons',capabilities=frozenset({'discover','metadata','photos','thumbnail','download'}),metadata_license='Structured data: CC0 expected; current policy audit pending',image_license='Per-file Commons license; no blanket reuse or AI permission',analysis_allowed=False)
    def __init__(self,http):self.http=http

    def discover(self,cursor,cancelled):
        # Deliberately bounded; no claim of complete K-pop coverage. No entity identity
        # is inferred from a name: all results retain the returned external Q ID.
        cursor=qid(cursor) if cursor else None
        seek=f'FILTER(STR(?item)>"http://www.wikidata.org/entity/{cursor}")' if cursor else ''
        query='''PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX bd: <http://www.bigdata.com/rdf#>
SELECT DISTINCT ?item ?itemLabel ?kind ?debut WHERE {
 ?genre rdfs:label "K-pop"@en . ?item wdt:P136 ?genre .
 { ?item wdt:P31/wdt:P279* ?type . ?type rdfs:label "girl group"@en . BIND("group" AS ?kind) }
 UNION { ?item wdt:P31/wdt:P279* ?type . ?type rdfs:label "musical group"@en . ?item wdt:P495 ?country . ?country rdfs:label "South Korea"@en . ?member wdt:P463 ?item ; wdt:P21 ?memberGender . ?memberGender rdfs:label "female"@en . BIND("group" AS ?kind) }
 UNION { ?item wdt:P31 ?human ; wdt:P21 ?gender . ?human rdfs:label "human"@en . ?gender rdfs:label "female"@en . BIND("person" AS ?kind) }
 OPTIONAL { ?item wdt:P571 ?debut . }
 '''+seek+''' SERVICE wikibase:label { bd:serviceParam wikibase:language "ko,en". }
} ORDER BY ?item LIMIT 30'''
        data=self.http.json('https://query.wikidata.org/sparql',{'query':query,'format':'json'},cancelled)
        candidates=[]
        for item in data.get('results',{}).get('bindings',[]):
            identity=qid(item['item']['value'])
            candidates.append(dict(external_id=identity,kind=item['kind']['value'],name=item.get('itemLabel',{}).get('value',identity),debut=item.get('debut',{}).get('value'),source_url='https://www.wikidata.org/wiki/'+identity))
        return candidates,(candidates[-1]['external_id'] if len(candidates)==30 else None)

    def entities(self,identities,cancelled):
        identities=[qid(x) for x in identities]
        if len(identities)>20:raise ValueError('한 번에 최대 20개 인물/그룹을 갱신합니다.')
        data=self.http.json('https://www.wikidata.org/w/api.php',dict(action='wbgetentities',ids='|'.join(identities),props='labels|aliases|claims|sitelinks',languages='ko|en',format='json',maxlag=5),cancelled)
        result=[]
        for identity,item in data.get('entities',{}).items():
            if 'missing' in item:continue
            claims=item.get('claims',{})
            def values(prop):
                return [x.get('mainsnak',{}).get('datavalue',{}).get('value') for x in claims.get(prop,[]) if x.get('rank')!='deprecated' and x.get('mainsnak',{}).get('snaktype')=='value']
            label=item.get('labels',{}).get('ko',item.get('labels',{}).get('en',{})).get('value',identity)
            category=next((x for x in values('P373') if isinstance(x,str)),None)
            sitelink=item.get('sitelinks',{}).get('commonswiki',{}).get('title','')
            if not category and sitelink.startswith('Category:'):category=sitelink[9:]
            aliases=[x['value'] for lang in ('ko','en') for x in item.get('aliases',{}).get(lang,[])]
            result.append(dict(external_id=qid(identity),name=label,aliases=aliases,category=category,
                members=[x['id'] for x in values('P527') if isinstance(x,dict) and 'id' in x],
                groups=[x['id'] for x in values('P463') if isinstance(x,dict) and 'id' in x],
                genders=[x['id'] for x in values('P21') if isinstance(x,dict) and 'id' in x],
                source_url='https://www.wikidata.org/wiki/'+qid(identity),raw=item))
        return result

    def photos(self,category,cursor,cancelled):
        category=category.removeprefix('Category:').strip()
        if not category or len(category)>300:raise ValueError('Commons 카테고리를 확인하세요.')
        params=dict(action='query',generator='categorymembers',gcmtitle='Category:'+category,gcmtype='file',gcmlimit=30,
            prop='imageinfo',iiprop='url|size|mime|sha1|extmetadata|timestamp',iiurlwidth=512,iiurlheight=512,format='json',formatversion=2,maxlag=5)
        if cursor:
            params.update({k:v for k,v in cursor.items() if k in ('continue','gcmcontinue')})
        data=self.http.json('https://commons.wikimedia.org/w/api.php',params,cancelled)
        images=[]
        for page in data.get('query',{}).get('pages',[]):
            info=(page.get('imageinfo') or [{}])[0]
            if info.get('mime') not in ('image/jpeg','image/png','image/webp'):continue
            meta=info.get('extmetadata',{})
            get=lambda key:plain(meta.get(key,{}).get('value',''))
            license_name,license_url=get('LicenseShortName'),get('LicenseUrl')
            if license_url.startswith('//'):license_url='https:'+license_url
            restrictions=get('Restrictions')
            revision=canonical_sha1(info.get('sha1')) or hashlib.sha256(str(info.get('timestamp','')).encode()).hexdigest()[:16]
            images.append(dict(source_id=str(int(page['pageid']))+'-'+revision,source_url=info.get('descriptionurl',''),original_url=info.get('url',''),thumbnail_url=info.get('thumburl'),title=page['title'],
                width=info.get('width',0),height=info.get('height',0),size=info.get('size',0),mime=info['mime'],source_sha1=info.get('sha1'),
                license_name=license_name,license_url=license_url,attribution=' · '.join(x for x in (get('Artist'),get('Credit'),get('Attribution')) if x),restrictions=restrictions,
                license_status=reuse_status(license_name,license_url,restrictions,get('Artist') or get('Attribution')),captured=get('DateTimeOriginal') or None,
                # Commons timestamp is a file-upload timestamp, never presented as capture time.
                published=info.get('timestamp'),raw=page))
        return images,data.get('continue')
