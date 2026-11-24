from datetime import datetime, timezone

from app.diagnostics import RampError


def _now():
    return datetime.now(timezone.utc).isoformat()


def revoke(book, batch, request_id, reason):
    """撤销已签发证据单的结算资格。

    幂等键为 request_id，请求标识在首次到达时就与目标批次绑定（即使当时批次不存在或尚未签发，
    绑定也会保留为 PENDING）。因此失败后重试只能继续打原批次：针对旧批次的撤销重试，
    绝不会撤掉更正错票后重新归档签发的新单。
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise RampError('REVOKE_REQUEST', 400)
    request_id = request_id.strip()
    reason = reason if isinstance(reason, str) and reason.strip() else '错票撤销'
    with book.write_lock() as db:
        prior = db.execute('SELECT digest,status,checksum,reason,revoked_at FROM revoke_requests '
                           'WHERE request_id=?', (request_id,)).fetchone()
        if prior is not None:
            if prior[0] != batch:
                raise RampError('REVOKE_BATCH_MISMATCH', 409)
            if prior[1] == 'DONE':
                return {'batch': batch, 'request_id': request_id, 'checksum': prior[2],
                        'reason': prior[3], 'revoked_at': prior[4], 'reused': True}
        else:
            # 先绑定再处理：后续无论成功失败，这个请求都只能作用于该批次
            db.execute('INSERT INTO revoke_requests(request_id,digest,status,reason,opened_at) '
                       "VALUES (?,?, 'PENDING', ?, ?)", (request_id, batch, reason, _now()))

        def fail(code, status):
            # 校验失败也要保住请求绑定，使失败后的重试仍被钉在原批次上
            db.commit()
            raise RampError(code, status)

        if db.execute('SELECT 1 FROM shifts WHERE digest=?', (batch,)).fetchone() is None:
            fail('NO_BATCH', 404)
        signature = db.execute('SELECT checksum FROM signatures WHERE digest=?', (batch,)).fetchone()
        if signature is None:
            fail('NOT_SIGNED', 409)
        checksum = signature[0]
        revoked_at = _now()
        db.execute("UPDATE revoke_requests SET status='DONE',checksum=?,revoked_at=? WHERE request_id=?",
                   (checksum, revoked_at, request_id))
    return {'batch': batch, 'request_id': request_id, 'checksum': checksum,
            'reason': reason, 'revoked_at': revoked_at, 'reused': False}


def revocation_record(book, batch):
    with book.connection() as db:
        row = db.execute('SELECT request_id,checksum,reason,revoked_at FROM revoke_requests '
                         "WHERE digest=? AND status='DONE' ORDER BY rowid DESC LIMIT 1", (batch,)).fetchone()
    if row is None:
        return None
    return {'request_id': row[0], 'checksum': row[1], 'reason': row[2], 'revoked_at': row[3]}
