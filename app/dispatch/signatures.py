from app.diagnostics import RampError
from app.ledger.manifest import lock
from app.ledger.replay import shift
from .document import compile_slip
from .revocation import revocation_record


def sign(book, batch):
    evidence = compile_slip(shift(book,batch))
    checksum = lock(evidence)
    with book.write_lock() as db:
        if db.execute("SELECT 1 FROM revoke_requests WHERE digest=? AND status='DONE' LIMIT 1",(batch,)).fetchone() is not None:
            raise RampError('REVOKED_BATCH',409)
        inserted = db.execute('INSERT INTO signatures VALUES (?,?) ON CONFLICT(digest) DO NOTHING',(batch,checksum)).rowcount
        saved = db.execute('SELECT checksum FROM signatures WHERE digest=?',(batch,)).fetchone()[0]
    return {'batch':batch,'checksum':saved,'reused':not bool(inserted),
            'open_items':sum(n for key,n in evidence['conservation']['states'].items() if key!='MATCH') +
                         len(evidence['conservation']['unmatched_receipts']),
            'held_trucks':evidence['conservation']['held_trucks']}


def evidence_file(book, batch):
    snapshot = shift(book,batch)
    with book.connection() as db:
        signature = db.execute('SELECT checksum FROM signatures WHERE digest=?',(batch,)).fetchone()
    if signature is None:
        raise RampError('NOT_SIGNED',409)
    document = compile_slip(snapshot)
    record = revocation_record(book,batch)
    verified = signature[0]==lock(document)
    # 内容是否属实(verified)与单据能否结算(settleable)分开：撤销只作废结算资格，原件仍可查、可复验。
    return {'checksum':signature[0],'verified':verified,
            'settleable':verified and record is None,'revocation':record,'slip':document}
