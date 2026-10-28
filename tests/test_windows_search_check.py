from tools.windows_search_check.check_search import candidates,write_report


def test_diagnostic_deduplicates_and_labels_images_without_claiming_original():
    rows=[dict(src='https://example.com/a.jpg',currentSrc='https://example.com/a.jpg',width=100,height=200,alt='sample'),dict(src='data:image/png,abc'),dict(src='javascript:alert(1)'),dict(src='https://user:password@example.com/secret')]
    result=candidates(rows)
    assert len(result)==1 and result[0]['width']==100 and 'not verified original' in result[0]['status']


def test_report_escapes_page_data_and_does_not_embed_remote_images(tmp_path):
    report=dict(state='error',stage1_complete=False,error='<script>unsafe</script>',images=[dict(url='https://example.com/photo.png',width=100,height=100,alt='<img src=x>')])
    write_report(tmp_path,report)
    text=(tmp_path/'RESULT.html').read_text()
    assert '<script>unsafe' not in text and '<img src=' not in text and '&lt;script&gt;' in text
    assert (tmp_path/'search-report.json').is_file()
