"""Share explicitly reviewed preview permission without replacing item decisions."""


def allow_reviewed_previews(repo, source, rows):
    if not repo.allowed(source, 'thumbnail'):
        return
    evidence = repo.permissions(source)['thumbnail']['evidence']
    for row in rows:
        with repo.db.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM resource_permissions WHERE resource_id=? AND operation='thumbnail'",
                ('web:' + row['id'],),
            ).fetchone()
        if not existing:
            repo.grant(source, 'thumbnail', 'allowed', evidence, 'web:' + row['id'])
