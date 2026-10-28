import pytest
from urllib.parse import quote
from app.providers.kpopping import parse_observation
from app.source_repository import SourceRepository


def observed():
    cdn='https://cdn.kpopping.com/kpics/2026/10/example.jpg';base='https://kpopping.com/api/kpics-image?url='+quote(cdn,safe='')
    return dict(query='장원영',state='diagnostic_completed',search_submitted=False,page_url='https://kpopping.com/kpics?idol=observed-id',images=[dict(url='https://kpopping.com/logo/kpopping-logo.svg'),dict(url='https://kpopping.com/_next/image?url=profile.jpg'),dict(url=base+'&size=thumb',alt='Wonyoung event',width=190,height=240),dict(url=base+'&size=medium',alt='Wonyoung event',width=190,height=240)])


def test_photo_variants_deduplicate_and_profiles_are_excluded():
    result=parse_observation(observed());assert len(result.candidates)==1
    photo=result.candidates[0];assert 'size=medium' in photo.url
    assert photo.declared_width is None and not photo.provenance[0]['original_verified']
    assert len(photo.provenance[0]['observed_variants'])==2
    assert result.metadata['ignored_non_photo_urls']==2 and not result.metadata['automated_search_verified']


def test_observed_endpoint_never_accepts_arbitrary_nested_source():
    data=observed();data['images']=[dict(url='https://kpopping.com/api/kpics-image?url=https%3A%2F%2Flocalhost%2Fsecret&size=medium')]
    assert not parse_observation(data).candidates
    data['page_url']='https://other.example/kpics'
    with pytest.raises(ValueError):parse_observation(data)


def test_observations_enter_existing_web_repository_without_granting_image_or_ai_rights(db):
    repo=SourceRepository(db);repo.save_spec(dict(id='kpopping-observation',name='Observed metadata only',category='user',adapter='html',domains=['kpopping.com','cdn.kpopping.com']))
    parsed=parse_observation(observed());page=repo.add_page('kpopping-observation',parsed.metadata['page_url'])
    repo.index_page(repo.page(page),parsed);repo.index_page(repo.page(page),parsed)
    rows=repo.images(page_id=page);assert len(rows)==1
    assert not repo.allowed('kpopping-observation','thumbnail','web:'+rows[0]['id'])
    assert not repo.allowed('kpopping-observation','ai_restore_input','web:'+rows[0]['id'])
