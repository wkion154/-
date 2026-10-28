import pytest
from app.search_check import check
from app.search_service import SearchService
from test_provided_files import files, allow_file


def pipeline(online, identity):
    class Provider:
        name = 'synthetic-pipeline'
        def search_images(self, query, cursor, cancelled):
            return ['web:' + identity], None
    return SearchService(online, [Provider()])


def test_pipeline_uses_existing_gallery_and_preview_without_granting_file_rights(files):
    online, identity, client, _, _, cdn = files
    report = check(online, '장원영', search=pipeline(online, identity))
    assert report['checks_passed'] and report['gallery_results'] == 1
    assert report['items'][0]['preview_decoded']
    assert not report['items'][0]['file_verified'] and cdn not in client.calls
    assert not report['stage1_complete'] and not report['ui_verified']
    assert not online.web.repo.allowed('kpopping-browser', 'original_download', 'web:' + identity)


def test_preview_only_never_passes_file_check_or_saves(files):
    online, identity, client, _, _, cdn = files
    report = check(online, '장원영', search=pipeline(online, identity), check_files=True, save=True)
    assert not report['checks_passed'] and report['items'][0]['preview_decoded']
    assert not report['items'][0]['file_verified'] and not report['items'][0]['saved']
    assert cdn not in client.calls and online.db.query()[1] == 0


def test_permitted_file_can_be_verified_without_permanent_save(files):
    online, identity, _, _, _, _ = files
    allow_file(online, identity)
    report = check(online, '장원영', search=pipeline(online, identity), check_files=True)
    assert report['checks_passed'] and report['items'][0]['file_verified']
    assert report['items'][0]['provided_file_only'] and report['items'][0]['width'] == 1200
    assert not report['items'][0]['saved'] and online.db.query()[1] == 0


def test_permanent_save_requires_explicit_option_and_keeps_search_identity(files):
    online, identity, _, _, _, _ = files
    allow_file(online, identity)
    report = check(online, '장원영', search=pipeline(online, identity), check_files=True, save=True)
    assert report['checks_passed'] and report['items'][0]['saved']
    assert online.db.query()[1] == 1
    assert report['items'][0]['entry_id'] == 'web:' + identity


def test_source_failure_is_in_report_instead_of_a_false_pass(files):
    online, identity, _, _, _, _ = files
    search = pipeline(online, identity)
    search.providers = {}
    report = check(online, '장원영', search=search)
    assert not report['checks_passed'] and report['errors'] and not report['items']


@pytest.mark.parametrize('options', [dict(limit=0), dict(limit=11), dict(batches=4), dict(save=True)])
def test_invalid_or_unbounded_checks_fail_before_search(files, options):
    online, identity, client, *_ = files
    with pytest.raises(ValueError):
        check(online, '장원영', search=pipeline(online, identity), **options)
    assert not client.calls
