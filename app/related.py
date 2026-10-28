"""Bounded, explainable metadata relationships. No identity or event inference from pixels."""
import json
import time
from datetime import date
from .source_repository import SourceRepository
from .providers.http import Cancelled

CONFIRMED = ('user_confirmed', 'confirmed')
VERSION = 'metadata-related-v1'


def calendar_day(value):
    """Use only a supplied calendar date; registration time is never a substitute."""
    try:
        text = str(value)
        if len(text) < 10 or (len(text) > 10 and text[10] not in ('T', ' ')):
            return None
        return date.fromisoformat(text[:10]).isoformat()
    except (ValueError, TypeError):
        return None


class RelatedRepository:
    PER_REASON = 256
    MAX_CANDIDATES = 1024
    MAX_RESULTS = 120

    def __init__(self, db):
        self.db = db
        self.sources = SourceRepository(db)

    def asset(self, identity):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM assets WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('사진을 찾을 수 없습니다.')
            return dict(row)

    def _origins(self, identity):
        with self.db.connect() as conn:
            origins = []
            for r in conn.execute("SELECT source_id,item_id FROM asset_sources WHERE asset_id=? AND source_id<>'local'", (identity,)):
                item, separator, revision = r['item_id'].partition('@')
                current = conn.execute('SELECT sha256 FROM web_images WHERE id=?', (item,)).fetchone() if separator else None
                resource = 'web:'+item if current and current['sha256']==revision else ''
                origins.append((r['source_id'],resource))
            origins += [('wikimedia', 'commons:'+r['id']) for r in conn.execute(
                'SELECT id FROM remote_images WHERE local_asset_id=?', (identity,))]
            local = conn.execute("SELECT 1 FROM asset_sources WHERE asset_id=? AND source_id='local'", (identity,)).fetchone()
        if origins:
            return list(dict.fromkeys(origins))
        return [('local', 'asset:'+identity)] if local else []

    def is_local(self, identity):
        return self._origins(identity) == [('local', 'asset:'+identity)]

    def allowed(self, identity, operations):
        # Use one individually reviewed origin for all requested operations. A duplicate
        # local marker must not launder an unreviewed remote asset into an AI input.
        for source, resource in self._origins(identity):
            if source == 'local' and operations == ('thumbnail',):
                return True
            if not resource:
                continue
            try:
                if all(self.sources.allowed(source, op, resource) for op in operations):
                    return True
            except (ValueError, KeyError):
                pass
        return False

    def override(self, left, right, kind, note):
        if left == right or kind not in ('confirmed_event', 'related', 'excluded'):
            raise ValueError('서로 다른 사진과 유효한 관계를 선택하세요.')
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
            raise ValueError('확인 근거나 제외 이유를 1~2,000자로 기록하세요.')
        left, right = sorted((left, right))
        with self.db.connect() as conn:
            conn.execute('INSERT INTO related_overrides VALUES(?,?,?,?,?) ON CONFLICT(left_id,right_id) DO UPDATE SET kind=excluded.kind,note=excluded.note,updated=excluded.updated',
                         (left, right, kind, note.strip(), time.time()))

    def clear_override(self, left, right):
        with self.db.connect() as conn:
            conn.execute('DELETE FROM related_overrides WHERE left_id=? AND right_id=?', tuple(sorted((left, right))))

    def search(self, identity, cancelled, reference=False, person=None):
        def check():
            if cancelled.is_set():
                raise Cancelled()
        check()
        seed = self.asset(identity)
        candidates = {}
        truncated = False
        def add(rows):
            nonlocal truncated
            rows = list(rows)
            if len(rows) > self.PER_REASON:
                truncated = True
            for row in rows[:self.PER_REASON]:
                value = dict(row)
                if value['id'] != identity:
                    if len(candidates) >= self.MAX_CANDIDATES and value['id'] not in candidates:
                        truncated = True
                        continue
                    candidates[value['id']] = value
        with self.db.connect() as conn:
            def people(asset):
                return {r['id']:r['name'] for r in conn.execute("SELECT p.id,p.name FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=? AND ip.status IN ('user_confirmed','confirmed') LIMIT 33", (asset,))}
            seed_people = people(identity)
            if len(seed_people) > 32:
                seed_people = dict(list(seed_people.items())[:32]);truncated = True
            if person and person not in seed_people:
                raise ValueError('기준 사진에 실제 등장이 확인된 인물을 선택하세요.')
            if reference and not person:
                if len(seed_people) == 1:
                    person = next(iter(seed_people))
                else:
                    return dict(seed=seed, people=seed_people, rows=[], truncated=False, blocked=0,
                                message='참조할 인물을 선택하세요.' if seed_people else '기준 사진에 확인된 인물 연결이 없습니다.')
            if reference and not self.allowed(identity, ('local_analysis', 'ai_restore_input')):
                return dict(seed=seed, people=seed_people, rows=[], truncated=False, blocked=1, message='기준 사진의 로컬 분석·AI 입력 권한을 먼저 확인하세요.')
            overrides = {}
            for row in conn.execute('SELECT * FROM related_overrides WHERE left_id=? OR right_id=? LIMIT 257', (identity, identity)):
                other = row['right_id'] if row['left_id'] == identity else row['left_id']
                overrides[other] = dict(row)
            if len(overrides) > 256:
                truncated = True
            for other in list(overrides)[:256]:
                row = conn.execute('SELECT * FROM assets WHERE id=?', (other,)).fetchone()
                if row:add([row])
            if not reference:
                for column in ('captured', 'published'):
                    day = calendar_day(seed[column])
                    if day:
                        # Half-open text range uses the persistent date index, not substr/full scan.
                        end = date.fromordinal(date.fromisoformat(day).toordinal()+1).isoformat() if day != '9999-12-31' else '9999-12-32'
                        add(conn.execute(f'SELECT * FROM assets WHERE {column}>=? AND {column}<? ORDER BY {column},id LIMIT ?', (day, end, self.PER_REASON+1)))
            visual = {}
            source_pages = set()
            seed_web = conn.execute('SELECT id,page_id,dhash,sha256 FROM web_images WHERE local_asset_id=? AND sha256=? LIMIT 8', (identity,seed['sha256'])).fetchall()
            for web in seed_web:
                source_pages.add(web['page_id'])
                if not reference:
                    add(conn.execute('SELECT a.* FROM web_images w JOIN assets a ON a.id=w.local_asset_id AND a.sha256=w.sha256 WHERE w.page_id=? ORDER BY w.id LIMIT ?', (web['page_id'],self.PER_REASON+1)))
                if web['dhash'] and self.allowed(identity, ('local_analysis',)):
                    pairs = conn.execute("SELECT w.id,w.local_asset_id,w.dhash,w.sha256,a.* FROM image_relations r JOIN web_images w ON w.id=CASE WHEN r.left_id=? THEN r.right_id ELSE r.left_id END JOIN assets a ON a.id=w.local_asset_id AND a.sha256=w.sha256 WHERE (r.left_id=? OR r.right_id=?) AND r.kind='perceptual_candidate' AND w.dhash IS NOT NULL LIMIT ?", (web['id'],web['id'],web['id'],self.PER_REASON+1)).fetchall()
                    for pair in pairs[:self.PER_REASON]:
                        other = pair['local_asset_id']
                        if not self.allowed(other, ('local_analysis',)):continue
                        distance = (int(web['dhash'],16)^int(pair['dhash'],16)).bit_count()
                        if distance<=4:
                            visual[other]=distance
                            # Duplicate column names from w/a must never replace asset IDs.
                            asset = conn.execute('SELECT * FROM assets WHERE id=?',(other,)).fetchone()
                            if asset:add([asset])
                    if len(pairs)>self.PER_REASON:truncated=True
            if not reference and len(seed_people)>8:truncated=True
            for pid in ([person] if reference else list(seed_people)[:8]):
                check()
                add(conn.execute("SELECT a.* FROM (SELECT image_id FROM image_person WHERE person_id=? AND status IN ('user_confirmed','confirmed') ORDER BY image_id LIMIT ?) i JOIN assets a ON a.id=i.image_id", (pid, self.PER_REASON+1)))
            seed_groups = {r[0] for r in conn.execute('SELECT group_id FROM image_group WHERE image_id=? LIMIT 8', (identity,))}
            if not reference:
                for gid in seed_groups:
                    check()
                    add(conn.execute('SELECT a.* FROM (SELECT image_id FROM image_group WHERE group_id=? ORDER BY image_id LIMIT ?) i JOIN assets a ON a.id=i.image_id', (gid, self.PER_REASON+1)))
            results, blocked = [], 0
            for row in candidates.values():
                check()
                other = row['id']
                override = conn.execute('SELECT * FROM related_overrides WHERE left_id=? AND right_id=?',tuple(sorted((identity,other)))).fetchone()
                if override and override['kind'] == 'excluded':continue
                # Keep the default remote male-only visibility policy in this alternate entry point.
                hidden = conn.execute("SELECT 1 FROM asset_visibility v WHERE v.asset_id=? AND v.hidden=1 AND EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=v.asset_id AND p.gender='male') AND NOT EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=v.asset_id AND p.gender<>'male') AND NOT EXISTS(SELECT 1 FROM asset_sources s WHERE s.asset_id=v.asset_id AND s.source_id='local')", (other,)).fetchone()
                if hidden:continue
                matched = set(seed_people) & set(people(other))
                if reference and person not in matched:continue
                if not self.allowed(other, ('thumbnail',)):
                    blocked += 1;continue
                if reference and not self.allowed(other, ('local_analysis', 'ai_restore_input')):
                    blocked += 1;continue
                reasons, score, relation = [], 0, 'metadata'
                if override:
                    relation = override['kind'];score += 100 if relation == 'confirmed_event' else 70
                    reasons.append('사용자 확인: '+override['note'])
                if other in visual:
                    score += 15;reasons.append(f'시각적 유사 후보 · dHash 거리 {visual[other]}/64 · 동일 인물/촬영 증거 아님')
                    if not override:relation = 'visual_candidate'
                page_match = conn.execute('SELECT page_id FROM web_images WHERE local_asset_id=? AND sha256=? LIMIT 8',(other,row['sha256'])).fetchall()
                if source_pages & {r[0] for r in page_match}:
                    score += 8;reasons.append('같은 출처 페이지에 게시됨 · 같은 촬영 확정 아님')
                if matched:
                    score += 30;reasons.append('등장 확인 인물 일치: '+', '.join(seed_people[p] for p in sorted(matched)))
                groups = {r[0] for r in conn.execute('SELECT group_id FROM image_group WHERE image_id=? LIMIT 32', (other,))}
                if seed_groups & groups:
                    score += 5;reasons.append('연결 그룹 일치 · 같은 촬영 근거는 아님')
                for column, label, weight in [('captured','기록된 촬영 날짜 일치',20),('published','기록된 게시 날짜 일치',3)]:
                    day = calendar_day(seed[column])
                    if day and day == calendar_day(row[column]):
                        score += weight;reasons.append(label+': '+day)
                if not score:continue
                if reference:
                    reasons.append('로컬 분석·AI 입력 권한 확인 · 얼굴 크기/각도/선명도는 미평가')
                    if row['width']*row['height'] > seed['width']*seed['height']:
                        score += 5;reasons.append('기준보다 전체 픽셀 수가 큼 · 얼굴 품질 보장 아님')
                origins = [dict(r) for r in conn.execute("SELECT COALESCE(p.name,s.source_id) AS name,s.page_url,s.license_url FROM asset_sources s LEFT JOIN source_profiles p ON p.id=s.source_id WHERE s.asset_id=? AND (s.item_id<>'' OR s.source_id='local') LIMIT 8", (other,))]
                origins += [dict(name='Wikimedia Commons',page_url=r['source_url'],license_url=r['license_url']) for r in conn.execute('SELECT source_url,license_url FROM remote_images WHERE local_asset_id=? LIMIT 8',(other,))]
                row.update(reasons=reasons, score=score, relation=relation, origins=origins)
                results.append(row)
        results.sort(key=lambda r: (-r['score'], -r['width']*r['height'], r['id']))
        if len(results) > self.MAX_RESULTS:truncated = True
        results = results[:self.MAX_RESULTS]
        check()
        # Audit snapshot, not an authorization token or a stale result cache.
        payload = dict(seed_sha256=seed['sha256'], reference=reference, person=person,
                       candidates=[dict(id=r['id'],sha256=r['sha256'],score=r['score'],relation=r['relation'],reasons=r['reasons']) for r in results])
        with self.db.connect() as conn:
            conn.execute('INSERT INTO related_searches VALUES(?,?,?,?) ON CONFLICT(seed_id) DO UPDATE SET version=excluded.version,created=excluded.created,payload=excluded.payload', (identity, VERSION, time.time(), json.dumps(payload, ensure_ascii=False)))
        return dict(seed=seed,people=seed_people,rows=results,truncated=truncated,blocked=blocked,
                    message='후보 없음' if not results else '')
