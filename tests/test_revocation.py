import threading
from concurrent.futures import ThreadPoolExecutor

from app.bundles import sample_set
from app.ledger.replay import replay
from app.main import create_app


def archived(client, sample='dirty'):
    return '/api/shifts/' + client.post('/api/shifts', json={'sample': sample}).json['batch']


def test_revoked_slip_still_queryable_but_not_settleable(client):
    addr = archived(client)
    client.post(addr + '/sign')
    answer = client.post(addr + '/revoke', json={'request_id': 'req-1', 'reason': '错票'})
    assert answer.status_code == 200 and answer.json['reused'] is False

    # 旧单查询不能断：原件、快照和下载都还在
    exported = client.get(addr + '/export').json
    assert exported['verified'] is True            # 内容是否属实：复验摘要仍通过
    assert exported['settleable'] is False         # 能否结算：撤销后不可结算
    assert exported['revocation']['request_id'] == 'req-1'
    assert exported['slip']['document'] == '机坪加注证据单'
    assert client.get(addr).status_code == 200


def test_revoked_batch_cannot_sign_settleable_slip(client):
    addr = archived(client)
    client.post(addr + '/sign')
    client.post(addr + '/revoke', json={'request_id': 'req-1'})
    refused = client.post(addr + '/sign')
    assert (refused.status_code, refused.json['error']) == (409, 'REVOKED_BATCH')
    exported = client.get(addr + '/export').json
    assert exported['settleable'] is False


def test_revoke_retry_is_idempotent_same_result(client):
    addr = archived(client)
    client.post(addr + '/sign')
    first = client.post(addr + '/revoke', json={'request_id': 'req-7'}).json
    again = client.post(addr + '/revoke', json={'request_id': 'req-7', 'reason': '别的理由'}).json
    assert again == dict(first, reused=True)
    # 同一批次只留一条撤销留痕
    with client.application.book.connection() as db:
        assert db.execute('SELECT count(*) FROM revoke_requests').fetchone()[0] == 1


def test_retry_of_old_request_cannot_revoke_new_slip(client):
    # 旧批次尚未归档，撤销请求先失败
    ghost = client.post('/api/shifts/ghost-batch/revoke', json={'request_id': 'req-stuck'})
    assert (ghost.status_code, ghost.json['error']) == (404, 'NO_BATCH')

    # 同班更正错票后重新归档：输入哈希变了，得到新批次并正常签发
    new_addr = archived(client)
    client.post(new_addr + '/sign')
    stolen = client.post(new_addr + '/revoke', json={'request_id': 'req-stuck'})
    assert (stolen.status_code, stolen.json['error']) == (409, 'REVOKE_BATCH_MISMATCH')
    exported = client.get(new_addr + '/export').json
    assert exported['settleable'] is True and exported['revocation'] is None


def test_pending_request_retry_succeeds_once_original_batch_ready(client):
    addr = archived(client)
    # 首次撤销时批次存在但尚未签发：请求以 PENDING 钉在该批次
    early = client.post(addr + '/revoke', json={'request_id': 'late-req'})
    assert (early.status_code, early.json['error']) == (409, 'NOT_SIGNED')
    with client.application.book.connection() as db:
        row = db.execute("SELECT status FROM revoke_requests WHERE request_id='late-req'").fetchone()
    assert row[0] == 'PENDING'

    client.post(addr + '/sign')
    done = client.post(addr + '/revoke', json={'request_id': 'late-req'}).json
    assert done['reused'] is False
    again = client.post(addr + '/revoke', json={'request_id': 'late-req'}).json
    assert again['reused'] is True and again['revoked_at'] == done['revoked_at']


def test_verified_and_settleable_are_independent_judgements(client, ramp):
    addr = archived(client)
    client.post(addr + '/sign')
    client.post(addr + '/revoke', json={'request_id': 'req-tamper'})
    with ramp.book.connection() as db:
        db.execute("UPDATE stage_rows SET body='{}' WHERE phase='balance'")
    exported = client.get(addr + '/export').json
    # 原件内容被篡改：内容不属实，且本来就已撤销；两个判定互不掩盖
    assert exported['verified'] is False and exported['settleable'] is False
    assert exported['revocation']['request_id'] == 'req-tamper'


def test_request_id_bound_to_first_batch_blocks_reuse_elsewhere(client):
    first_addr = archived(client, 'dirty')
    other_addr = archived(client, 'clean')
    client.post(first_addr + '/sign')
    client.post(other_addr + '/sign')
    client.post(first_addr + '/revoke', json={'request_id': 'shared-id'})
    mismatch = client.post(other_addr + '/revoke', json={'request_id': 'shared-id'})
    assert (mismatch.status_code, mismatch.json['error']) == (409, 'REVOKE_BATCH_MISMATCH')
    assert client.get(other_addr + '/export').json['settleable'] is True


def test_corrected_ticket_rearchives_as_signable_new_batch(client):
    original = client.post('/api/shifts', json={'sample': 'clean'}).json
    corrected = {name: raw.decode() for name, raw in sample_set('clean').items()}
    corrected['receipts'] = corrected['receipts'].replace('80.4', '80')
    changed = client.post('/api/shifts', json=corrected).json
    assert original['batch'] != changed['batch']

    old_addr = '/api/shifts/' + original['batch']
    new_addr = '/api/shifts/' + changed['batch']
    client.post(old_addr + '/sign')
    client.post(old_addr + '/revoke', json={'request_id': 'fix-1'})
    issued = client.post(new_addr + '/sign')
    assert issued.status_code == 200 and issued.json['reused'] is False
    assert client.get(new_addr + '/export').json['settleable'] is True


def test_revoke_requires_request_id(client):
    addr = archived(client)
    client.post(addr + '/sign')
    for payload in ({}, {'request_id': ''}, {'request_id': '   '}):
        response = client.post(addr + '/revoke', json=payload)
        assert (response.status_code, response.json['error']) == (400, 'REVOKE_REQUEST')
    assert client.get(addr + '/export').json['settleable'] is True


def test_revoke_unsigned_batch_rejected(client):
    addr = archived(client)
    response = client.post(addr + '/revoke', json={'request_id': 'req-x'})
    assert (response.status_code, response.json['error']) == (409, 'NOT_SIGNED')


def test_revoke_unknown_batch_is_404(client):
    response = client.post('/api/shifts/no-such/revoke', json={'request_id': 'req-x'})
    assert (response.status_code, response.json['error']) == (404, 'NO_BATCH')


def test_concurrent_sign_and_revoke_never_settleable_after_revoke(ramp):
    for _ in range(9):
        book = create_app(ramp.book.path).book
        batch = replay(book, sample_set('dirty'))['batch']
        start = threading.Event()

        def client_call(path, payload=None):
            def task():
                start.wait()
                cli = create_app(ramp.book.path).test_client()
                return cli.post(path, json=payload) if payload is not None else cli.post(path)
            return task

        with ThreadPoolExecutor(2) as pool:
            sign_future = pool.submit(client_call(f'/api/shifts/{batch}/sign'))
            revoke_future = pool.submit(
                client_call(f'/api/shifts/{batch}/revoke', {'request_id': f'race-{batch}'}))
            start.set()
            sign_answer, revoke_answer = sign_future.result(), revoke_future.result()

        # 撤销先到时单子尚未签发：按同一请求标识重试，绝不能换批次
        if revoke_answer.status_code == 409 and revoke_answer.json['error'] == 'NOT_SIGNED':
            assert sign_answer.status_code == 200
            retry = create_app(ramp.book.path).test_client().post(
                f'/api/shifts/{batch}/revoke', json={'request_id': f'race-{batch}'})
            assert retry.status_code == 200
        else:
            assert revoke_answer.status_code == 200

        exported = create_app(ramp.book.path).test_client().get(f'/api/shifts/{batch}/export').json
        # 撤销一旦成功，旧批次无论签发先后都不能再处于可结算状态
        assert exported['revocation'] is not None and exported['settleable'] is False
        assert exported['verified'] is True


def test_parallel_revoke_retries_leave_single_record(ramp):
    seed = create_app(ramp.book.path).test_client()
    addr = archived(seed)
    seed.post(addr + '/sign')

    def call(_):
        cli = create_app(ramp.book.path).test_client()
        return cli.post(addr + '/revoke', json={'request_id': 'same-req'}).status_code

    with ThreadPoolExecutor(6) as pool:
        statuses = list(pool.map(call, range(6)))
    assert sorted(set(statuses)) == [200]
    with ramp.book.connection() as db:
        assert db.execute('SELECT count(*) FROM revoke_requests').fetchone()[0] == 1
    listed = create_app(ramp.book.path).test_client().get('/api/shifts').json['shifts'][0]
    assert listed['revoked'] is True and listed['signed'] is True
