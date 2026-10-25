import json
import re
from copy import deepcopy

from fastapi.testclient import TestClient
from app import accounts, store
from app.main import app
from app.scheduling.runtime import get_runtime


def csrf(client):
    page = client.get('/schedule?day=2026-10-26')
    assert page.status_code == 200
    data = json.loads(re.search(r'<script id="calendar-week-data" type="application/json">(.*?)</script>', page.text, re.S)[1])
    return {'X-CSRF-Token': data['csrf']}


def test_account_ids_cannot_reuse_a_restarted_demo_counter(monkeypatch):
    monkeypatch.setattr(accounts, '_accounts', deepcopy(accounts._accounts))
    monkeypatch.setattr(accounts, 'next_id', lambda prefix: prefix+'_restarted')
    first=accounts.register('Synthetic One','one-id@example.invalid','synthetic-test-password')
    second=accounts.register('Synthetic Two','two-id@example.invalid','synthetic-test-password')
    assert first['id'] != second['id']


def test_registered_account_can_edit_own_calendar_without_reading_maya(monkeypatch):
    monkeypatch.setattr(accounts, '_accounts', deepcopy(accounts._accounts))
    with TestClient(app) as person, TestClient(app) as maya:
        person.post('/register', data={'name':'Synthetic Student','email':'calendar-regression@example.invalid','password':'synthetic-test-password'})
        headers = csrf(person)
        assert person.get('/api/schedule/busy').json() == []
        assert person.get('/schedule?owner_id=u_maya').status_code == 403
        body = {'kind':'busy','starts_at':'2026-10-26T11:00','ends_at':'2026-10-26T12:00'}
        assert person.post('/api/schedule/entry',json=body,headers=headers).status_code == 200
        entry = next(e for e in person.get('/api/schedule/week?day=2026-10-26').json()['entries'] if e['kind']=='busy')
        maya.post('/login',data={'email':accounts.TEST_EMAIL,'password':accounts.TEST_PASSWORD})
        maya_headers = csrf(maya)
        assert not any(e['id']==entry['id'] for e in maya.get('/api/schedule/week?day=2026-10-26').json()['entries'])
        assert maya.post('/api/schedule/entry',json={**body,'id':entry['id']},headers=maya_headers).status_code == 422
        runtime=get_runtime(); runtime.repository.close(); get_runtime.cache_clear()
        assert any(e['id']==entry['id'] for e in person.get('/api/schedule/week?day=2026-10-26').json()['entries'])
        assert person.delete('/api/schedule/entry/busy/'+entry['id'],headers=headers).status_code == 200
        assert all(e['id']!=entry['id'] for e in person.get('/api/schedule/week?day=2026-10-26').json()['entries'])


def test_registered_account_room_sync_uses_its_own_identity(monkeypatch):
    monkeypatch.setattr(accounts, '_accounts', deepcopy(accounts._accounts))
    monkeypatch.setattr(store, '_rooms', [])
    with TestClient(app) as person:
        person.post('/register',data={'name':'Synthetic Host','email':'calendar-room@example.invalid','password':'synthetic-test-password'})
        csrf(person)
        response=person.post('/session/rooms',data={'course_id':'math54','starts_at':'2026-10-26T13:00',
            'ends_at':'2026-10-26T14:00','place_type':'coffee','group_size':'2'},follow_redirects=False)
        assert 'notice=' in response.headers['location'] and 'error=' not in response.headers['location']
        entries=person.get('/api/schedule/week?day=2026-10-26').json()['entries']
        room=next(e for e in entries if e['kind']=='session')
        assert store.rooms()[0]['host_id'] != store.OWNER_ID
        person.post('/session/rooms/'+room['id']+'/leave',follow_redirects=False)
        assert all(e['kind']!='session' for e in person.get('/api/schedule/week?day=2026-10-26').json()['entries'])
