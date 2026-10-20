"""예약 접수 후 고정 조회 기준·접수일 문맥·동시 세대 변경을 공개 경로로 검증한다."""
import datetime as dt
import json
from dataclasses import replace
from threading import Event, Thread

import pytest

from kis_trader import KISClient, OrderStore
from kis_trader.domestic._engine import reserved_orders as domestic
from kis_trader.order import ChangeActionFingerprint
from kis_trader.overseas._engine import reserved_orders as overseas
from kis_trader.store import Binding
from kis_trader.transport import RawResponse

KST = dt.timezone(dt.timedelta(hours=9))
FIRST = dt.datetime(2024, 5, 30, 10, tzinfo=KST)
LATER = dt.datetime(2024, 6, 3, 10, tzinfo=KST)
CID = 'synthetic-reservation'


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)


def freeze(monkeypatch, moment):
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)
    monkeypatch.setattr(domestic, 'datetime', Clock)
    monkeypatch.setattr(overseas, 'datetime', Clock)


def ack(lane):
    if lane == 'domestic':
        output = {'RSVN_ORD_SEQ': '42401'}
    elif lane == 'us':
        output = {'ODNO': '0031111234'}
    else:
        output = {'OVRS_RSVN_ODNO': '0030138295', 'RSVN_ORD_RCIT_DT': '20240530'}
    return RawResponse('0', 'synthetic', '', {'output': output})


def row(lane, receipt='20240530'):
    if lane == 'domestic':
        return {'rsvn_ord_seq': '42401', 'rsvn_ord_rcit_dt': receipt}
    number = '0031111234' if lane == 'us' else '0030138295'
    return {'ovrs_rsvn_odno': number, 'rsvn_ord_rcit_dt': receipt}


class Wire:
    def __init__(self, lane, rows=None, expected=None):
        self.lane = lane
        self.rows = rows or []
        self.expected = expected
        self.calls = []
        self.entered = None
        self.release = None

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs['method'] == 'POST':
            return ack(self.lane)
        assert kwargs['method'] == 'GET'
        assert kwargs['path'].endswith('order-resv-ccnl' if self.lane == 'domestic' else 'order-resv-list')
        if self.expected is not None:
            start, end = self.expected
            fields = ('RSVN_ORD_ORD_DT', 'RSVN_ORD_END_DT') if self.lane == 'domestic' else ('INQR_STRT_DT', 'INQR_END_DT')
            assert (kwargs['params'][fields[0]], kwargs['params'][fields[1]]) == (start, end)
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(5), 'test did not release inquiry'
        return RawResponse('0', 'synthetic', '', {'output': self.rows})


def client(wire, store):
    return KISClient(app_key='synthetic', app_secret='synthetic', account='synthetic-01',
                     environment='real', transport=wire, store=store)


def place(instance, lane):
    if lane == 'domestic':
        stock = instance.domestic.stock('005930')
        quantity, price = 1, 70000
    elif lane == 'us':
        stock = instance.overseas.stock('AAPL', exchange='NAS')
        quantity, price = 1, 150
    else:
        stock = instance.overseas.stock('00700', exchange='HKS')
        quantity, price = 100, 350
    return stock.reserve_buy(quantity=quantity, limit_price=price, client_order_id=CID)


@pytest.mark.parametrize('lane', ['domestic', 'us', 'asia'])
def test_public_accepted_date_window_survives_restart_and_refresh(monkeypatch, tmp_path, lane):
    path = tmp_path / 'synthetic-orders.json'
    wire = Wire(lane)
    with OrderStore(path, now=lambda: FIRST, retention_days=0) as store, client(wire, store) as instance:
        accepted = place(instance, lane)
        assert accepted.order_id and store.claim_time_for(CID) is None
        anchor = store.query_anchor_for(CID)
        assert anchor == (FIRST.isoformat(), 'first_claim')
    lookup = dt.datetime(2024, 6, 10, 10, tzinfo=KST) if lane == 'domestic' else LATER
    freeze(monkeypatch, lookup)
    expected = ('20240523', '20240711') if lane == 'domestic' else ('20240529', '20240604')
    wire = Wire(lane, [row(lane)], expected=expected)
    with OrderStore(path, now=lambda: lookup, retention_days=0) as store, client(wire, store) as instance:
        assert instance.orders.reconcile(CID) == accepted
        assert wire.calls == []
        first = instance.orders.reconcile(CID, refresh=True)
        assert first is not None and first.order_id == accepted.order_id
        assert store.query_anchor_for(CID) == anchor
        assert instance.orders.reconcile(CID, refresh=True) is not None
        assert len(wire.calls) == 2 and all(c['method'] == 'GET' for c in wire.calls)
    with OrderStore(path, retention_days=0) as store:
        assert store.query_anchor_for(CID) == anchor


@pytest.mark.parametrize('receipt', ['20240603', '', 'malformed', '20240230', None, '20240530'])
def test_public_asia_refresh_preserves_known_receipt_identity(monkeypatch, receipt):
    freeze(monkeypatch, FIRST)
    wire = Wire('asia')
    with OrderStore(now=lambda: FIRST) as store, client(wire, store) as instance:
        accepted = place(instance, 'asia')
        assert accepted.receipt_date == '20240530'
        freeze(monkeypatch, LATER)
        candidate = row('asia', receipt)
        if receipt is None:
            candidate.pop('rsvn_ord_rcit_dt')
        wire.rows = [candidate]
        result = instance.orders.reconcile(CID, refresh=True)
        if receipt == '20240530':
            assert result is not None and result.recorded_at == LATER
        else:
            assert result is None
            assert store.report_for(CID) == accepted
        assert store.report_for(CID).receipt_date == '20240530'
        assert [call['method'] for call in wire.calls] == ['POST', 'GET']
        instance.orders.cancel(CID, request_id='synthetic-cancel')
        assert wire.calls[-1]['body']['RSVN_ORD_RCIT_DT'] == '20240530'
        assert wire.calls[-1]['body']['OVRS_RSVN_ODNO'] == accepted.order_id


@pytest.mark.parametrize('lane', ['domestic', 'us', 'asia'])
def test_same_clock_explicit_rebind_blocks_stale_inquiry_writeback(monkeypatch, lane):
    freeze(monkeypatch, FIRST)
    wire = Wire(lane)
    with OrderStore(now=lambda: FIRST) as store, client(wire, store) as instance:
        prior = place(instance, lane)
        fingerprint = store.fingerprint_for(CID)
        wire.rows = [row(lane)]
        freeze(monkeypatch, LATER)
        wire.entered, wire.release = Event(), Event()
        results, errors = [], []
        def refresh():
            try:
                results.append(instance.orders.reconcile(CID, refresh=True))
            except BaseException as error:  # noqa: BLE001 - join 뒤 모든 작업 오류를 단언한다.
                errors.append(error)
        worker = Thread(target=refresh)
        worker.start()
        try:
            assert wire.entered.wait(5), 'inquiry never reached'
            action = ChangeActionFingerprint(original_client_order_id=CID, action='cancel',
                side=fingerprint.side, order_type=fingerprint.order_type,
                quantity=fingerprint.quantity, limit_price=fingerprint.limit_price,
                time_in_force='day', region='domestic' if lane == 'domestic' else 'overseas',
                asset_class='stock', exchange=fingerprint.exchange)
            store.try_claim_change('synthetic-change', action, original_client_order_id=CID)
            store.record_change(replace(prior, client_order_id='synthetic-change'), action,
                                rebind=Binding(prior, fingerprint))
            rebound = store._recovery_snapshot_for(CID)
        finally:
            wire.release.set()
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert store._recovery_snapshot_for(CID) == rebound
        assert results == [prior]


@pytest.mark.parametrize('lane', ['domestic', 'us', 'asia'])
@pytest.mark.parametrize('known', [False, True])
def test_rebind_uses_current_anchor_or_honest_unknown_fallback(monkeypatch, lane, known):
    freeze(monkeypatch, FIRST)
    clock = [FIRST]
    wire = Wire(lane)
    with OrderStore(now=lambda: clock[0]) as store, client(wire, store) as instance:
        prior = place(instance, lane)
        fingerprint = store.fingerprint_for(CID)
        action = ChangeActionFingerprint(original_client_order_id=CID, action='cancel',
                side=fingerprint.side, order_type=fingerprint.order_type,
                quantity=fingerprint.quantity, limit_price=fingerprint.limit_price,
                time_in_force='day', region='domestic' if lane == 'domestic' else 'overseas',
                asset_class='stock', exchange=fingerprint.exchange)
        clock[0] = dt.datetime(2024, 6, 2, 10, tzinfo=KST)
        if known:
            store.try_claim_change('synthetic-change', action, original_client_order_id=CID)
        changed = replace(prior, client_order_id='synthetic-change')
        store.record_change(changed, action, rebind=Binding(prior, fingerprint))
        if known:
            expected = ('20240526', '20240704') if lane == 'domestic' else ('20240601', '20240607')
        else:
            assert store.query_anchor_for(CID) == (None, 'unknown')
            expected = ('20240527', '20240704') if lane == 'domestic' else ('20240602', '20240608')
        freeze(monkeypatch, LATER)
        wire.expected = expected
        assert instance.orders.reconcile(CID, refresh=True) is None
        assert wire.calls[-1]['method'] == 'GET'


def test_public_us_missing_receipt_does_not_invent_one_from_anchor(monkeypatch):
    freeze(monkeypatch, FIRST)
    wire = Wire('us')
    with OrderStore(now=lambda: FIRST) as store, client(wire, store) as instance:
        accepted = place(instance, 'us')
        assert accepted.receipt_date is None
        freeze(monkeypatch, LATER)
        wire.rows = [row('us', '')]
        refreshed = instance.orders.reconcile(CID, refresh=True)
        assert refreshed is not None and refreshed.receipt_date is None
        assert store.query_anchor_for(CID) == (FIRST.isoformat(), 'first_claim')


@pytest.mark.parametrize('lane', ['domestic', 'us', 'asia'])
def test_legacy_recorded_anchor_is_weak_frozen_query_plan(monkeypatch, tmp_path, lane):
    freeze(monkeypatch, FIRST)
    path = tmp_path / 'synthetic-legacy-orders.json'
    wire = Wire(lane)
    with OrderStore(path, now=lambda: FIRST, retention_days=0) as store, client(wire, store) as instance:
        place(instance, lane)
    payload = json.loads(path.read_text())
    payload['schema_version'] = 11
    payload.pop('submission_anchors')
    path.write_text(json.dumps(payload))
    freeze(monkeypatch, LATER)
    expected = ('20240523', '20240704') if lane == 'domestic' else ('20240529', '20240604')
    wire = Wire(lane, [row(lane)], expected=expected)
    with OrderStore(path, retention_days=0) as store, client(wire, store) as instance:
        assert store.query_anchor_for(CID) == (FIRST.isoformat(), 'legacy_recorded')
        assert instance.orders.reconcile(CID, refresh=True) is not None
        assert store.query_anchor_for(CID) == (FIRST.isoformat(), 'legacy_recorded')
