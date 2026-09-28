"""Speech transcription is transport context, never semantic/financial authority."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from types import SimpleNamespace

import httpx
import pytest

from tests.test_v2_turn_executor import EVENT
from tests.test_v2_pdf_url_media import HOST, URL
from v2_adapters.proof_media import ProofMediaReader
from v2_contracts.model import ModelAttachment, AttachmentContentStatus as Status, InvalidModelProposal

TEXT = "Entrada em 19/11/2026, saída em 22/11/2026. Três adultos. Não quero reservar ainda."
# Container signature fixture, not a speech/codec validity or real-provider witness.
AUDIO = b'OggS' + b'\x00' * 24 + b'OpusHead' + b'\x00' * 128
SHA = hashlib.sha256(AUDIO).hexdigest()


def stt(tmp_path, handler):
    assert importlib.util.find_spec('v2_adapters.audio_stt') is not None, 'STT adapter missing'
    from v2_adapters.audio_stt import XaiSpeechToText
    return XaiSpeechToText(api_key='synthetic-xai-key', archive=tmp_path,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_transcript_contract_carries_verbatim_event_bound_text_without_pixels():
    assert 'TRANSCRIPT_READY' in Status.__members__, 'typed transcription status missing'
    a = ModelAttachment('audio/ogg', Status.TRANSCRIPT_READY, EVENT.event_id, SHA,
                        transcript_text=TEXT, transcription_model='grok-voice-transcribe-2.0')
    assert a.to_dict()['transcript_text'] == TEXT
    assert a.to_dict()['source_sha256'] == SHA
    assert a.image_data_url is None
    for change in ({'transcript_text':''}, {'source_sha256':None}, {'source_event_id':None},
                   {'transcript_text':'é'*8193}, {'media_type':'image/png'},
                   {'error_code':'error'}, {'content_status':Status.UNAVAILABLE},
                   {'image_data_url':'data:image/png;base64,AAAA'}):
        with pytest.raises(InvalidModelProposal): replace(a, **change)


def test_xai_multipart_is_exact_verbatim_and_cached_after_restart(tmp_path):
    calls=[]
    def send(request):
        calls.append(request)
        assert request.method == 'POST' and str(request.url) == 'https://api.x.ai/v1/stt'
        assert request.headers['Authorization'] == 'Bearer synthetic-xai-key'
        body=request.read()
        assert body.index(b'name="model"') < body.index(b'name="file"')
        assert body.index(b'name="format"') < body.index(b'name="file"')
        assert b'grok-voice-transcribe-2.0' in body and b'false' in body
        assert AUDIO in body and b'audio/ogg' in body
        assert b'name="language"' not in body
        return httpx.Response(200,json={'text':TEXT,'language':'pt','duration':10.0})
    first=stt(tmp_path,send)
    assert first.transcribe(AUDIO,'audio/ogg') == TEXT
    second=stt(tmp_path,send)
    assert second.transcribe(AUDIO,'audio/ogg') == TEXT and len(calls)==1
    assert 'synthetic-xai-key' not in ''.join(p.read_text() for p in tmp_path.rglob('*.json'))


@pytest.mark.parametrize('status,body',[(401,{}),(429,{}),(503,{}),(200,{'text':''}),
    (200,{'text':'  '}),(200,{'text':None}),(200,{'text':'x'*16385}),(200,[])])
def test_failed_or_unbounded_stt_is_not_cached_as_success(tmp_path,status,body):
    api=stt(tmp_path,lambda _:httpx.Response(status,json=body))
    with pytest.raises(ValueError,match='audio_transcription_unavailable'):
        api.transcribe(AUDIO,'audio/ogg')
    assert not list(tmp_path.rglob('*.json'))


def test_timeout_is_unavailable_without_echoing_credentials(tmp_path):
    def fail(_): raise httpx.ReadTimeout('synthetic-xai-key must not escape')
    api=stt(tmp_path,fail)
    with pytest.raises(ValueError) as error: api.transcribe(AUDIO,'audio/ogg')
    assert str(error.value)=='audio_transcription_unavailable'


def audio_reader(tmp_path,*,mime='audio/ogg; codecs=opus',raw=AUDIO,status=200,transcript=TEXT):
    downloads=[]; calls=[]
    def get(request):
        downloads.append(request)
        return httpx.Response(status,content=raw,headers={'content-type':mime})
    def send(request):
        calls.append(request)
        return httpx.Response(200,json={'text':transcript})
    api=stt(tmp_path/'stt',send)
    reader=ProofMediaReader(allowed_hosts=(HOST,),archive=tmp_path/'media',
        client=httpx.Client(transport=httpx.MockTransport(get)),transcriber=api)
    return reader,downloads,calls


@pytest.mark.parametrize('mime',['audio/ogg; codecs=opus','application/ogg','application/octet-stream'])
def test_url_only_audio_delivered_and_retried_without_redownload_or_stt(tmp_path,mime):
    reader,downloads,calls=audio_reader(tmp_path,mime=mime)
    event=replace(EVENT,text=URL,media_url=None,media_type=None)
    (a,)=reader.extract((event,))
    assert a.content_status.value=='transcript_ready' and a.transcript_text==TEXT
    assert a.source_event_id==event.event_id and a.source_sha256==SHA
    assert event.text==URL
    reader2,_,_=audio_reader(tmp_path,mime=mime)
    assert reader2.extract((event,)) == (a,)
    assert len(downloads)==len(calls)==1
    history=reader2.dialogue_text((event,),URL)
    assert URL in history and TEXT in history and 'Transcrição' in history


@pytest.mark.parametrize('status,raw,mime',[(403,AUDIO,'audio/ogg'),(200,b'broken','audio/ogg'),
    (200,AUDIO+b'x'*(4*1024*1024),'audio/ogg'),(302,AUDIO,'audio/ogg')], ids=['expired','corrupt','oversize','redirect'])
def test_invalid_audio_does_not_call_stt(tmp_path,status,raw,mime):
    reader,downloads,calls=audio_reader(tmp_path,status=status,raw=raw,mime=mime)
    event=replace(EVENT,text=URL)
    (a,)=reader.extract((event,))
    assert a.content_status.value=='unavailable' and not calls
    assert reader.dialogue_text((event,),URL)==URL


def test_transcript_budget_cannot_overflow_history_or_silently_clip(tmp_path):
    reader,_,_=audio_reader(tmp_path,transcript='é'*8100)
    event=replace(EVENT,text='legenda '*100+URL)
    (a,)=reader.extract((event,))
    assert a.content_status.value=='unavailable'
    assert reader.dialogue_text((event,),event.text)==event.text


def test_audio_batch_count_is_bounded_and_ordered(tmp_path):
    reader,downloads,calls=audio_reader(tmp_path)
    events=tuple(replace(EVENT,event_id=f'event:audio:{n}',text=URL+str(n)) for n in range(5))
    attachments=reader.extract(events)
    assert [a.source_event_id for a in attachments]==[e.event_id for e in events]
    assert [a.content_status.value for a in attachments]==['transcript_ready']*4+['unavailable']
    assert len(downloads)==4 and len(calls)==1
    assert reader.dialogue_text(events,'\n'.join(e.text for e in events)).count(TEXT)==4


def test_model_child_does_not_inherit_stt_key():
    from v2_adapters.hermes_model import HermesModelAdapter
    model=HermesModelAdapter(command=('synthetic',),system_prompt='Synthetic',timeout=30,
        transcript_key=b't'*32,environ={'V2_XAI_STT_API_KEY':'synthetic-xai-key','PATH':'/usr/bin'})
    assert 'V2_XAI_STT_API_KEY' not in model._child_env


def test_concurrent_duplicate_audio_calls_pay_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(200,json={'text':TEXT})
    apis=[stt(tmp_path,send) for _ in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(lambda api:api.transcribe(AUDIO,'audio/ogg'),apis))==[TEXT]*3
    assert len(calls)==1


def test_audio_disabled_leaves_existing_pdf_reader_available(tmp_path):
    calls=[]
    def get(request):
        calls.append(request)
        return httpx.Response(200,content=AUDIO,headers={'content-type':'audio/ogg'})
    reader=ProofMediaReader(allowed_hosts=(HOST,),archive=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(get)))
    (a,)=reader.extract((replace(EVENT,text=URL),))
    assert a.content_status.value=='unavailable'


def test_executor_exposes_audio_to_same_maya_and_remembers_it_on_replay(tmp_path):
    from datetime import timedelta
    from tests.test_v2_turn_executor import BATCH, AUTHORITY, MappingAuthority, FixedClock, FakeProfile, _install_public_authority, TRANSCRIPT_KEY
    from tests.test_v2_visual_media_turn import response
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from v2_application.turn_executor import V2TurnExecutor
    from v2_application.conversation import V2ConversationReducer
    from v2_application.reads import V2ReadService
    from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
    from v2_adapters.hermes_model import HermesModelAdapter
    media,downloads,stt_calls=audio_reader(tmp_path)
    boundary=SQLiteBoundaryStore.open_memory_v8();_install_public_authority(boundary)
    private=SQLitePrivateCustomerFactStore.open_memory();model_calls=[]
    def run(command,**kwargs):
        wire=json.loads(kwargs['input']);model_calls.append(wire)
        customer=json.loads(wire['messages'][-1][1])
        assert customer['message']==URL
        assert customer['attachments'][0]['transcript_text']==TEXT
        assert not wire.get('images') and 'payment_candidates' not in customer
        assert 'synthetic-xai-key' not in kwargs['input'].decode()
        reply=response(text='Você não quer reservar ainda. São três adultos, de 19 a 22/11/2026.')
        del reply['schema'],reply['payment_proof']
        return SimpleNamespace(returncode=0,stdout=b'PHASE8_RESULT\x00'+json.dumps(reply).encode(),stderr=b'')
    executor=V2TurnExecutor(store=boundary,model=HermesModelAdapter(command=('synthetic','--contract','maya-v8'),system_prompt='Synthetic',timeout=30,transcript_key=TRANSCRIPT_KEY,run=run),
        reads=V2ReadService({}),profile=FakeProfile(boundary),private_customer_facts=private,
        reducer=V2ConversationReducer(),public_authority=MappingAuthority({BATCH.batch_id:AUTHORITY}),
        clock=FixedClock(),locale='pt-BR',turn_timeout=timedelta(seconds=30),max_commit_attempts=2,proof_media=media)
    batch=replace(BATCH,events=(replace(EVENT,text=URL),),combined_text=URL)
    result=executor.execute(batch)
    row=private._connection.execute('select customer_message from private_dialogue_turns').fetchone()
    assert URL in row[0] and TEXT in row[0]
    assert executor.execute(batch).replayed
    assert len(downloads)==len(stt_calls)==len(model_calls)==1
    assert boundary._connection.execute('select count(*) from boundary_commands').fetchone()==(0,)


def test_worker_settings_opt_in_is_scoped_and_configured(tmp_path):
    from v2_host.settings import V2Settings
    assert 'xai_stt_api_key' in V2Settings.__dataclass_fields__, 'worker STT setting missing'
    source={'V2_PROCESS_ROLE':'api','V2_MANYCHAT_WEBHOOK_SECRET':'fixture','V2_SQLITE_PATH':str(tmp_path/'inbox.sqlite3'),'V2_XAI_STT_API_KEY':'synthetic-xai-key','V2_PROOF_MEDIA_HOSTS':HOST}
    from v2_host.settings import V2ProcessRole
    api=V2Settings.from_env(source, process_role=V2ProcessRole.API)
    assert api.xai_stt_api_key==''
    from tests.test_v2_production_composition import _settings
    from v2_host.composition import V2Container,V2Role
    from v2_host.production import build_worker_set
    from v2_host.settings import RuntimeMode
    from v2_host.worker_main import WorkerQueue
    knowledge=tmp_path/'knowledge.yaml';knowledge.write_text('entries:\n  - id: faq\n    topic: geral\n    question: Oi?\n    answer: Olá.\n')
    settings=_settings(tmp_path,runtime_mode=RuntimeMode.GENERAL_AVAILABILITY,hermes_model='openai-codex/gpt-5.6-terra',candidate_git_sha='a'*40,candidate_image_digest='sha256:'+'b'*64,manychat_api_key='synthetic',
        hermes_command=('python','-m','v2_host.hermes_child','hermes'),hermes_system_prompt='Synthetic',hermes_transcript_key=b't'*32,public_authority_hmac_key=b'a'*32,knowledge_base_path=knowledge,proof_media_hosts=(HOST,),xai_stt_api_key='synthetic-xai-key')
    assert 'synthetic-xai-key' not in repr(settings)
    container=V2Container.open(settings=settings,role=V2Role.WORKER)
    try:
        workers=build_worker_set(container=container,settings=settings)
        assert workers[WorkerQueue.INBOX]._executor._proof_media.transcriber is not None
        assert container.readiness().capabilities['audio_transcription']=='ready'
        assert settings.global_kill_switch_engaged
    finally: container.close()
