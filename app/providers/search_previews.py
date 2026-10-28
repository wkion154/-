"""Share explicitly reviewed preview permission without replacing item decisions."""


def allow_reviewed_previews(repo, source, rows, operation='thumbnail'):
    if operation not in ('thumbnail','original_download'):
        raise ValueError('Unsupported search display operation')
    if not repo.allowed(source, operation):
        return
    evidence = repo.permissions(source)[operation]['evidence']
    for row in rows:
        with repo.db.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM resource_permissions WHERE resource_id=? AND operation=?",
                ('web:' + row['id'],operation),
            ).fetchone()
        if not existing:
            repo.grant(source, operation, 'allowed', evidence, 'web:' + row['id'])
