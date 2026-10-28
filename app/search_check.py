"""Opt-in diagnosis using the application's configured search and library paths."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading

from .analysis_filters import AnalysisFilter
from .library import LibraryService
from .search_service import SearchService


def check(online, query, *, search=None, limit=3, batches=2, check_files=False, save=False):
    if not 1 <= limit <= 10 or not 1 <= batches <= 3:
        raise ValueError('검사 사진은 1~10개, 검색 배치는 1~3개입니다.')
    if save and not check_files:
        raise ValueError('영구 보관 검사는 제공 파일 검사와 함께 요청하세요.')
    search = search if search is not None else SearchService(online)
    cancel = threading.Event()
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), query=query,
                  scope='configured search and library pipeline; not GUI interaction',
                  sources=list(search.providers), pages=[], items=[], errors=[], failure_details=[],
                  file_check_requested=check_files, save_requested=save,
                  provided_preview_enabled=online.settings.values.get('kpopping_provided_preview') is True,
                  search_browser_visible=online.settings.values.get('kpopping_show_browser') is True,
                  permissions_changed=False, ui_verified=False, stage1_complete=False,
                  checks_passed=False)
    try:
        identity = search.start(query, AnalysisFilter())
        for _ in range(batches):
            page = search.page(identity, cancel)
            report['pages'].append(page)
            if page.get('error'): report['errors'].append(page['error'])
            if page['state'] != 'ready': break
        library = LibraryService(online)
        rows, total = library.repo.query(scope=('search', identity), sort='discovery', limit=limit)
        report['gallery_results'] = total
        for row in rows:
            item = dict(entry_id=row['id'], kind=row['kind'], preview_decoded=False,
                        file_verified=False, saved=False, errors=[])
            report['items'].append(item)
            try:
                preview = library.preview(row, cancel)
                if preview.isNull(): raise ValueError('미리보기 디코딩 결과가 비어 있습니다.')
                item.update(preview_decoded=True, preview_width=preview.width(), preview_height=preview.height())
            except Exception as error:
                item['errors'].append(str(error))
            if check_files:
                try:
                    version = library.original(row, cancel)
                    if version.get('preview_only'):
                        raise ValueError('미리보기만 확인됐습니다. 제공 파일/원본 검사는 통과하지 않았습니다.')
                    from .images import inspect_image
                    measured = inspect_image(version['path'])
                    item.update(file_verified=True, width=measured['width'], height=measured['height'],
                                provided_file_only=bool(version.get('provided_file_only')))
                    if save:
                        if row['kind'] == 'web':
                            result = online.web.download(row['ref_id'], cancel)
                            item.update(saved=True, asset_id=result['asset_id'])
                        elif row['kind'] == 'asset':
                            item.update(saved=True, already_saved=True)
                        else:
                            raise ValueError('이 진단의 영구 보관 검사는 웹 검색 사진만 지원합니다.')
                except Exception as error:
                    item['errors'].append(str(error))
        report['checks_passed'] = bool(report['items']) and not report['errors'] and all(
            item['preview_decoded'] and not item['errors'] and
            (not check_files or item['file_verified']) and (not save or item['saved'])
            for item in report['items'])
    except Exception as error:
        report['errors'].append(str(error))
        report['failure_details'].append(dict(stage=getattr(error,'stage','search'),
                                             http_status=getattr(error,'status',None),
                                             page_url=getattr(error,'page_url',None),
                                             blocked=getattr(error,'blocked',False)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # The batch launcher passes typed text through the environment so shell
    # metacharacters in an artist's name are never interpolated as commands.
    query = os.environ.get('KIDOL_CHECK_QUERY')
    parser.add_argument('--query', default=query, required=not bool(query))
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--batches', type=int, default=2)
    parser.add_argument('--check-files', action='store_true')
    parser.add_argument('--save', action='store_true', help='Explicitly store inspected files using existing permissions')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    from PySide6.QtCore import QCoreApplication
    from .core import data_root, Settings
    from .database import Database
    from .instance import LibraryInstance
    from .online_service import OnlineService
    app = QCoreApplication.instance() or QCoreApplication([])
    root = data_root()
    output = args.output or root / 'search-reports' / ('pipeline-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    try:
        if not (root / 'library.sqlite3').is_file():
            raise RuntimeError('먼저 앱을 실행해 검색 출처를 설정한 뒤 앱을 닫고 검사하세요.')
        with LibraryInstance(root):
            online = OnlineService(Database(root / 'library.sqlite3'), Settings(root), None)
            report = check(online, args.query, limit=args.limit, batches=args.batches,
                           check_files=args.check_files, save=args.save)
    except Exception as error:
        report = dict(checks_passed=False, stage1_complete=False, errors=[str(error)])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(output)
    return 0 if report['checks_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
