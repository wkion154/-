"""Fetch authoritative policy evidence before activating Wikimedia.
Downloading documents does not itself establish policy acceptance. A reviewer must
read them and supply --record-review --review-note after checking the current API,
robots, source reuse conditions, vocabulary labels and contact/User-Agent needs.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.providers.http import HttpClient,RemoteError
from app.providers.wikimedia import POLICY_URLS

VOCABULARY={'Q5':'human','Q6581072':'female','Q641066':'girl group','Q213665':'K-pop'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='docs/source-check-v0.3.json')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--observed-reason',default='Live access has not been validated.')
    parser.add_argument('--record-review',action='store_true')
    parser.add_argument('--review-note')
    args=parser.parse_args()
    result=dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),policy_reviewed=False,
                status='not_run' if args.offline else 'checking',documents=[],vocabulary_checked=False)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    if args.offline:
        result.update(reason=args.observed_reason,required_policy_urls=list(POLICY_URLS))
    else:
        http=HttpClient();cancel=threading.Event()
        directory=output.parent/'source-review';directory.mkdir(exist_ok=True)
        try:
            for number,url in enumerate(POLICY_URLS):
                with http.open(url,cancel,accept='text/html,text/plain') as response:
                    body=response.read(2*1024*1024+1)
                    if len(body)>2*1024*1024:raise RemoteError('Policy document exceeds review capture limit.')
                path=directory/f'policy-{number+1}.txt';path.write_bytes(body)
                result['documents'].append(dict(url=url,path=str(path),sha256=hashlib.sha256(body).hexdigest()))
            data=http.json('https://www.wikidata.org/w/api.php',dict(action='wbgetentities',ids='|'.join(VOCABULARY),props='labels',languages='en',format='json'),cancel)
            for identity,expected in VOCABULARY.items():
                actual=data.get('entities',{}).get(identity,{}).get('labels',{}).get('en',{}).get('value')
                if actual!=expected:raise RemoteError(f'Vocabulary mismatch: {identity}: {actual!r}')
            result.update(status='evidence_downloaded_review_required',vocabulary_checked=True)
            if args.record_review:
                if not args.review_note:raise RemoteError('Provide a review note after reading the downloaded documents.')
                from app.core import data_root,Settings
                from app.database import Database
                from app.online_repository import OnlineRepository
                root=data_root();Settings(root)
                result.update(policy_reviewed=True,review_note=args.review_note)
                OnlineRepository(Database(root/'library.sqlite3')).record_review(result)
                result['status']='review_recorded'
        except Exception as error:
            result.update(status='blocked_or_failed',reason=str(error),policy_reviewed=False)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status'] in ('evidence_downloaded_review_required','review_recorded') else 2

if __name__=='__main__':raise SystemExit(main())
