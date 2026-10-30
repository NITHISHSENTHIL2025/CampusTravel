"""All synthetic records live in pytest's isolated temporary database and are never shipped."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
import app.store as store
from app.main import app,ph

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'isolated-test.sqlite3')
    store.init_db()
    with store.db(True) as c:
        c.execute('INSERT INTO users(name,email,role,password_hash,created_at,must_change_password) VALUES(?,?,?,?,?,0)',
         ('Test Administrator','admin@test.invalid','admin',ph.hash('OnlyForLocalTests_123!'),datetime.now().isoformat()))
    clients={role:TestClient(app) for role in ['admin','driver','student']}
    r=clients['admin'].post('/api/login',json={'email':'admin@test.invalid','password':'OnlyForLocalTests_123!'})
    assert r.status_code==200,r.text
    csrf={'admin':r.json()['csrf']}
    def do(role,method,path,payload=None):
        args={'headers':{'X-CSRF-Token':csrf[role]}}
        if payload is not None:args['json']=payload
        return getattr(clients[role],method.lower())('/api'+path,**args)
    yield clients,csrf,do
    for c in clients.values():c.close()

def test_full_actual_workflows_in_isolated_database(environment):
    clients,csrf,do=environment
    assert do('admin','GET','/overview').json()['students']==0
    assert do('admin','GET','/buses').json()==[]
    assert do('admin','GET','/live').json()==[]
    assert do('admin','GET','/forecast').json()['ready'] is False
    assert do('admin','POST','/buses',{'plate':'TESTBUS-001','label':'Bench test vehicle','capacity':1}).status_code==200
    assert do('admin','POST','/buses',{'plate':'TESTBUS-001','label':'Duplicate','capacity':1}).status_code==409
    route=do('admin','POST','/routes',{'code':'TEST-01','name':'Isolated Test Route'}).json()['id']
    stop=do('admin','POST',f'/routes/{route}/stops',{'name':'Device test point','stop_order':1,'lat':17.54,'lon':78.38}).json()['id']
    assert do('admin','POST','/users',{'name':'Test Driver','email':'driver@test.invalid','role':'driver','password':'OnlyForLocalTests_123!'}).status_code==200
    student=do('admin','POST','/users',{'name':'Test Student','email':'student@test.invalid','role':'student','student_number':'TEST123','password':'OnlyForLocalTests_123!'}).json()['id']
    us=do('admin','GET','/users').json()
    did=next(u['id'] for u in us if u['role']=='driver')
    bid=do('admin','GET','/buses').json()[0]['id']
    alloc=do('admin','POST','/allocations',{'student_id':student,'route_id':route,'stop_id':stop})
    assert alloc.status_code==200,alloc.text
    assert do('admin','POST','/allocations',{'student_id':student,'route_id':route,'stop_id':stop}).status_code==409
    today=datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()
    trip=do('admin','POST','/schedules',{'route_id':route,'bus_id':bid,'driver_id':did,'service_date':today,'departure_time':'08:00','duration_minutes':120,'is_test':True})
    assert trip.status_code==200,trip.text
    sid=trip.json()['id']
    assert do('admin','POST','/schedules',{'route_id':route,'bus_id':bid,'driver_id':did,'service_date':today,'departure_time':'08:30'}).status_code==409
    for role in ['driver','student']:
        response=clients[role].post('/api/login',json={'email':f'{role}@test.invalid','password':'OnlyForLocalTests_123!'})
        assert response.status_code==200,response.text
        csrf[role]=response.json()['csrf']
    assert do('student','GET','/buses').status_code==200
    assert do('student','POST','/buses',{'plate':'BAD','label':'Bad','capacity':40}).status_code==403
    assert do('student','POST',f'/schedules/{sid}/start').status_code==403
    assert do('driver','POST',f'/schedules/{sid}/start').status_code==200
    assert do('driver','POST',f'/schedules/{sid}/start').status_code==409
    assert do('student','GET','/my/pass').json()['allocation']['route_id']==route
    assert do('student','GET','/my/pass-qr.svg').status_code==200
    assert do('student','GET','/live').json()[0]['last_location'] is None
    passcode=do('student','GET','/my/pass').json()['allocation']['pass_code']
    assert do('driver','POST',f'/schedules/{sid}/board',{'pass_code':passcode}).status_code==200
    assert do('driver','POST',f'/schedules/{sid}/board',{'pass_code':passcode}).status_code==409
    assert do('driver','GET',f'/schedules/{sid}/boardings').json()[0]['student_id']==student
    assert do('student','GET',f'/schedules/{sid}/boardings').status_code==403
    gps={'lat':17.54,'lon':78.38,'accuracy':12,'timestamp':int(datetime.now().timestamp()*1000)}
    assert do('student','POST',f'/schedules/{sid}/location',gps).status_code==403
    assert do('driver','POST',f'/schedules/{sid}/location',gps).status_code==200
    assert do('student','GET','/live').json()[0]['last_location']['accuracy']==12
    assert do('driver','POST','/incidents',{'schedule_id':sid,'category':'delay','description':'Safety pause during device test'}).status_code==200
    assert len(do('student','GET','/incidents').json())==1
    assert do('driver','POST',f'/schedules/{sid}/finish').status_code==200
    assert do('driver','POST',f'/schedules/{sid}/location',{**gps,'timestamp':gps['timestamp']+5000}).status_code==409
    # Test journeys remain distinct from production analytics/training.
    assert do('admin','GET','/analytics').json()['daily']==[]
    assert do('admin','GET','/overview').json()['boardings']==0
    assert do('admin','GET','/forecast').json()['ready'] is False
    # No unauthenticated mutation or missing CSRF.
    assert clients['admin'].post('/api/buses',json={'plate':'BAD2','label':'Bad','capacity':10}).status_code==403
    assert clients['student'].get('/api/audit').status_code==403

def test_account_and_validation_rules(environment):
    _,_,do=environment
    assert do('admin','POST','/users',{'name':'Short','email':'t@test.invalid','role':'student','student_number':'A','password':'short'}).status_code==400
    assert do('admin','POST','/routes',{'code':'R1','name':'<b>Unsafe route</b>'}).status_code==200
    assert do('admin','POST','/buses',{'plate':'X-001','label':'Test','capacity':0}).status_code==400
    assert do('admin','POST','/buses',{'plate':'X-001','label':'Test','capacity':20}).status_code==200
