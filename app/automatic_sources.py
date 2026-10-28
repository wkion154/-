"""Unified public-source selection; missing accounts are status, not fake results."""
import platform
from .providers.dbkpop import DBKpopProvider
from .providers.kpopping_browser import KpoppingBrowserProvider
from .providers.naver_images import NaverImageProvider
from . import naver_credentials


def configured(online):
    providers=[];status=[];repo=online.web.repo
    def enable(provider_type,files=False):
        provider=provider_type(online)
        operations=dict(metadata_search='allowed',thumbnail='allowed')
        if files:operations['original_download']='allowed'
        repo.grant_many(provider.name,operations,dict(reference=repo.spec(provider.name).policy_urls[0],
            note='Application automatic search and display enabled. Not a copyright determination. No automatic permanent storage, analysis or AI.'),enabled=True)
        providers.append(provider);status.append(dict(source=provider.name,state='selected'))
    enable(DBKpopProvider)
    if platform.system()=='Windows':enable(KpoppingBrowserProvider,files=True)
    else:status.append(dict(source='kpopping-browser',state='requires_windows'))
    try:naver_credentials.load(online.settings.root)
    except (RuntimeError,ValueError):status.append(dict(source='naver-image-search',state='requires_api_credentials'))
    else:enable(NaverImageProvider)
    status.append(dict(source='flickr-public-feed',state='site_policy_blocked'))
    for source in ('instagram','x','pinterest','dispatch'):
        status.append(dict(source=source,state='adapter_not_connected'))
    return providers,status
