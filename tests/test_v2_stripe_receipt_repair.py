"""Receipt routing is independent of conversational readiness, not financial trust."""
import json
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from deploy.canary_router import build_app
from tests.native_stripe_helpers import close_lab, make_lab, signature
from tests.test_v2_native_stripe import NOW
from v2_application.financial_webhooks import FinancialWebhookInvalid, FinancialWebhookUnauthorized
from v2_application.native_stripe import NativeStripeIgnored, NativeStripeIngress
from v2_host.api_main import build_api_app


def native_lab(tmp_path, unit='hostel', *, livemode=False):
    lab = make_lab(tmp_path, unit, livemode=livemode)
    lab.ingress = NativeStripeIngress(paths=lab.paths,
        accounts=lab.settings.stripe_native_accounts,
        result_key=lab.settings.stripe_native_result_key,
        livemode=livemode, client=lab.client)
    return lab


def relay(*, enabled=True, identity=True, status=202, error=None, limit=65536, forward_impl=None):
    calls, ready_calls = [], []

    async def forward(target, body, headers):
        calls.append((target, body, headers))
        if error:
            raise error
        if forward_impl:
            return await forward_impl(target, body, headers)
        return status, b'{"status":"receipt-result"}', 'application/json'

    async def ready(target):
        ready_calls.append(target)
        return False

    async def identity_probe():
        return identity() if callable(identity) else identity

    app = build_app(shared_secret='conversation-secret', allowed_subscriber_id=None,
                    v2_url='http://owner-api:8080/webhook/manychat',
                    v2_ready_url='http://owner-api:8080/readyz',
                    legacy_url='http://legacy:8080/webhook/manychat',
                    cutover_deadline=None, forward=forward, ready_probe=ready,
                    identity_probe=identity_probe, clock=lambda: NOW,
                    max_body_bytes=limit, stripe_receipts_enabled=enabled)
    return TestClient(app), calls, ready_calls


@pytest.mark.parametrize('unit', ['hostel', 'agency'])
@pytest.mark.parametrize('status', [200, 202, 401, 409, 422, 503])
def test_receipt_preserves_raw_body_signature_status_without_worker_readiness(unit, status):
    client, calls, ready = relay(status=status)
    body = b'{ "unchanged" : "\\u00e1", "signed_whitespace": true }\n'
    sig = 't=1,v1=first,v1=second'
    response = client.post('/webhook/payments/stripe/' + unit, content=body,
                           headers={'Stripe-Signature': sig, 'Content-Type': 'application/json',
                                    'X-Hermes-Webhook-Secret': 'must-not-be-forwarded'})
    assert response.status_code == status
    assert calls == [('http://owner-api:8080/webhook/payments/stripe/' + unit, body,
                      {'Stripe-Signature': sig, 'Content-Type': 'application/json'})]
    assert ready == []
    assert response.content == b'{"status":"receipt-result"}'


@pytest.mark.parametrize('enabled,identity,status', [(False, True, 503), (True, False, 503)])
def test_disabled_or_unidentified_receiver_never_forwards(enabled, identity, status):
    client, calls, ready = relay(enabled=enabled, identity=identity)
    response = client.post('/webhook/payments/stripe/hostel', content=b'{}', headers={'Stripe-Signature': 'invalid'})
    assert response.status_code == status
    assert calls == ready == []


def test_identity_change_during_body_read_blocks_forward():
    values = iter([True, False])
    client, calls, ready = relay(identity=lambda: next(values))
    response = client.post('/webhook/payments/stripe/hostel', content=b'{}', headers={'Stripe-Signature': 'invalid'})
    assert response.status_code == 503
    assert calls == ready == []


@pytest.mark.parametrize('unit', ['unknown', 'wise', 'stripe'])
def test_unknown_unit_never_forwards(unit):
    client, calls, ready = relay()
    assert client.post('/webhook/payments/stripe/' + unit, content=b'{}').status_code == 404
    assert calls == ready == []


def test_missing_signature_is_rejected_without_forward():
    client, calls, ready = relay()
    assert client.post('/webhook/payments/stripe/hostel', content=b'{}').status_code == 401
    assert calls == ready == []


@pytest.mark.parametrize('declared,status', [('999', 413), ('-1', 400), ('wrong', 400), ('1', 413), (None, 413)])
def test_bounded_receipt_body_never_forwards(declared, status):
    client, calls, ready = relay(limit=8)
    headers = {'Stripe-Signature': 'invalid'}
    if declared is not None:
        headers['Content-Length'] = declared
    content = (chunk for chunk in [b'12345', b'67890']) if declared is None else b'1234567890'
    response = client.post('/webhook/payments/stripe/hostel', content=content, headers=headers)
    assert response.status_code == status
    assert calls == ready == []


@pytest.mark.parametrize('error', [httpx.ConnectError('down'), httpx.ReadTimeout('late')])
def test_upstream_error_never_falls_back_or_retries(error):
    client, calls, ready = relay(error=error)
    response = client.post('/webhook/payments/stripe/agency', content=b'{}', headers={'Stripe-Signature': 'invalid'})
    assert response.status_code == 502
    assert len(calls) == 1
    assert calls[0][0].startswith('http://owner-api:8080/')
    assert ready == []


def test_receipt_enable_does_not_bypass_manychat_readiness():
    client, calls, ready = relay()
    response = client.post('/webhook/manychat', json={'subscriber_id': '12345', 'message_id': 'msg:synthetic', 'text': 'oi'},
                           headers={'X-V2-Webhook-Secret': 'conversation-secret'})
    assert response.status_code == 503
    assert response.json()['status'] == 'canary_unavailable'
    assert calls == []
    assert ready == ['http://owner-api:8080/readyz']


@pytest.mark.parametrize('livemode', [False, True])
@pytest.mark.parametrize('unit', ['hostel', 'agency'])
def test_authenticated_foreign_checkout_without_link_is_ignored(tmp_path, livemode, unit):
    lab = native_lab(tmp_path, unit, livemode=livemode)
    try:
        lab.session['payment_link'] = None
        with pytest.raises(NativeStripeIgnored):
            lab.ingress.accept(unit, lab.body(), signature(lab.body()), received_at=NOW)
        assert [r.url.path for r in lab.requests] == ['/v1/account', '/v1/checkout/sessions/cs_test_Native']
        assert all(r.method == 'GET' for r in lab.requests)
        assert not lab.paths['followup'].exists()
    finally:
        close_lab(lab)


@pytest.mark.parametrize('mutation', ['signature', 'mode', 'amount', 'account', 'malformed_link'])
def test_foreign_checkout_is_not_a_way_to_bypass_validation(tmp_path, mutation):
    lab = native_lab(tmp_path)
    try:
        lab.session['payment_link'] = None
        body = lab.body()
        headers = signature(body)
        expected = FinancialWebhookInvalid
        if mutation == 'signature':
            headers = {'Stripe-Signature': 't=1,v1=wrong'}
            expected = FinancialWebhookUnauthorized
        elif mutation == 'mode':
            lab.event['livemode'] = True
            body = lab.body(); headers = signature(body)
        elif mutation == 'amount':
            event = json.loads(body); event['data']['object']['amount_total'] += 1
            body = json.dumps(event).encode(); headers = signature(body)
        elif mutation == 'account':
            lab.ingress.accounts['hostel']['account_id'] = 'acct_Different'
        elif mutation == 'malformed_link':
            lab.session['payment_link'] = ''
            body = lab.body(); headers = signature(body)
        with pytest.raises(expected):
            lab.ingress.accept('hostel', body, headers, received_at=NOW)
        assert not lab.paths['followup'].exists()
    finally:
        close_lab(lab)


@pytest.mark.parametrize('enabled,expected', [(None, 503), ('0', 503), ('1', 401)])
def test_environment_factory_requires_explicit_receipt_opt_in(tmp_path, monkeypatch, enabled, expected):
    from deploy.canary_router import create_app_from_env
    sha, digest = 'a' * 40, 'sha256:' + 'b' * 64
    image = 'registry.invalid/v2@' + digest
    metadata = tmp_path / 'identity.json'
    metadata.write_text(json.dumps({'schema': 'v2-runtime-image-metadata-v1',
        'labels': {'org.opencontainers.image.revision': sha}, 'repo_digests': [image]}))
    env = {'CANARY_WEBHOOK_SECRET': 'synthetic', 'CANARY_V2_URL': 'http://api:8080/webhook/manychat',
        'CANARY_V2_READY_URL': 'http://api:8080/readyz', 'CANARY_LEGACY_URL': 'http://old:8080/webhook/manychat',
        'CANARY_CUTOVER_DEADLINE': '', 'CANARY_EXPECTED_GIT_SHA': sha,
        'CANARY_EXPECTED_IMAGE_REF': image, 'CANARY_EXPECTED_IMAGE_DIGEST': digest,
        'CANARY_RUNTIME_IDENTITY_METADATA_PATH': str(metadata)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv('CANARY_STRIPE_RECEIPTS_ENABLED', raising=False)
    if enabled is not None:
        monkeypatch.setenv('CANARY_STRIPE_RECEIPTS_ENABLED', enabled)
    with TestClient(create_app_from_env()) as client:
        assert client.get('/readyz').status_code == 200
        assert client.post('/webhook/payments/stripe/agency', content=b'{}').status_code == expected


@pytest.mark.parametrize('value', ['true', 'yes', '', '2'])
def test_malformed_enable_is_rejected(monkeypatch, value):
    from deploy.canary_router import create_app_from_env
    monkeypatch.setenv('CANARY_STRIPE_RECEIPTS_ENABLED', value)
    with pytest.raises(ValueError, match='CANARY_STRIPE_RECEIPTS_ENABLED'):
        create_app_from_env()


@pytest.mark.parametrize('livemode', [False, True])
@pytest.mark.parametrize('unit', ['hostel', 'agency'])
def test_actual_api_receives_once_and_deduplicates_through_relay(tmp_path, unit, livemode):
    lab = make_lab(tmp_path, unit, livemode=livemode)
    api = build_api_app(lab.settings, clock=lambda: NOW)
    api.state.native_stripe.client = lab.client
    api_client = TestClient(api)

    async def api_forward(target, body, headers):
        r = api_client.post(urlsplit(target).path, content=body, headers=headers)
        return r.status_code, r.content, r.headers['content-type']

    client, calls, ready = relay(forward_impl=api_forward)
    try:
        for expected in [202, 200]:
            r = client.post('/webhook/payments/stripe/' + unit, content=lab.body(), headers=signature(lab.body()))
            assert r.status_code == expected
        assert len(calls) == 2 and ready == []
        import sqlite3
        with sqlite3.connect(lab.paths['followup']) as con:
            assert con.execute('select count(*) from payment_evidence_claims').fetchone()[0] == 1
        assert all(r.method == 'GET' for r in lab.requests)
    finally:
        api.state.v2_container.close()
        api_client.close()
        close_lab(lab)
