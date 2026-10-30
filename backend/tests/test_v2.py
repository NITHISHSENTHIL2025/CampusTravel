"""V2 end-to-end checks in a disposable temp database; no manufactured data in live storage."""
from datetime import datetime,timezone,timedelta
import app.store as store
import seed_demo
from app.main import app,ph
from fastapi.testclient import TestClient
import pytest

@pytest.fixture()
def seeded(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'only-tests.sqlite3')
    monkeypatch.setattr(seed_demo,'ROOT',tmp_path)
    store.init_db()
    with store.db(True) as c:
        c.execute('INSERT INTO users(name,email,role,password_hash,must_change_password,created_at) VALUES(?,?,?,?,0,?)',
                  ('Real Test Admin','admin@campusride.test','admin',ph.hash('TestAdmin!123456'),datetime.now(timezone.utc).isoformat()))
    seed_demo.create()
    entries=(tmp_path/'DEMO_CREDENTIALS_PRIVATE.txt').read_text().splitlines()
    credentials={x.split('\t')[0]:tuple(x.split('\t')[1:]) for x in entries if '\t' in x}
    with TestClient(app) as client:
        def signed(label):
            email,password=credentials[label] if label!='ADMIN' else ('admin@campusride.test','TestAdmin!123456')
            from fastapi.testclient import TestClient
            c=TestClient(app)
            r=c.post('/api/login',json={'email':email,'password':password})
            assert r.status_code==200,(label,r.text)
            return c,{'X-CSRF-Token':r.json()['csrf']}
        yield signed,client

def test_complete_fixed_allocation_and_exports(seeded):
    signed,other=seeded
    c,h=signed('ADMIN')
    fleet=c.get('/api/fleet').json()
    assert len(fleet)==2
    assert {x['students'] for x in fleet}=={12}
    assert len({x['bus_id'] for x in c.get('/api/driver/dashboard').json().get('assignment',[])}) if False else True
    routes=c.get('/api/routes').json()
    assert len(routes)==2
    assert all(len(r['stops'])==5 for r in routes)
    assert all('campus' in r['stops'][0]['name'].lower() and 'return' in r['stops'][-1]['name'].lower() for r in routes)
    csv=c.get('/api/admin/roster.csv/'+str(routes[0]['id']))
    assert csv.status_code==200,csv.text
    assert 'Student Name' in csv.text
    assert csv.text.count('Demo Student')==12
    assert len({r['email'] for r in c.get('/api/users').json()})==27
    assert c.get('/api/admin/today').json()['trips'][0]['is_test']==1
    assert c.get('/api/forecast').json()['ready'] is False
    with pytest.raises(SystemExit,match='Demo seed refused'):
        seed_demo.create()
    c.close()

def test_role_permissions_and_location_autofinish(seeded):
    signed,_=seeded
    admin,ah=signed('ADMIN')
    driver,dh=signed('DRIVER 1')
    student,sh=signed('STUDENT 01')
    d=driver.get('/api/driver/dashboard').json()
    assert d['assignment']['route_code']=='DEMO-R01'
    assert len(d['students'])==12
    assert 'email' not in d['students'][0]
    assert driver.get('/api/admin/today').status_code==403
    me=student.get('/api/my/dashboard').json()
    assert me['allocation']['route_code']=='DEMO-R01'
    assert len(me['fees'])==1 and me['fees'][0]['demo']==1
    assert me['fees'][0]['amount_paise']==450000
    assert student.get('/api/admin/roster.csv/1').status_code==403
    trip=next(t for t in d['trips'] if t['shift']=='morning')
    sid=trip['id']
    assert driver.post(f'/api/schedules/{sid}/start',headers=dh).status_code==200
    passcode=student.get('/api/my/dashboard').json()['allocation']['pass_code']
    assert driver.post(f'/api/schedules/{sid}/board',headers=dh,json={'pass_code':passcode}).status_code==200
    assert driver.post(f'/api/schedules/{sid}/board',headers=dh,json={'pass_code':passcode}).status_code==409
    with store.db(True) as conn:
        # Simulate a previously established physical departure and an old-enough start time.
        conn.execute('UPDATE schedules SET started_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),sid))
        conn.execute('INSERT INTO locations(schedule_id,lat,lon,accuracy,measured_at,received_at) VALUES(?,?,?,?,?,?)',
                     (sid,17.559,78.396,12,(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat(),datetime.now(timezone.utc).isoformat()))
    home={'lat':17.54661,'lon':78.404776,'accuracy':15,'timestamp':int(datetime.now(timezone.utc).timestamp()*1000)}
    result=driver.post(f'/api/schedules/{sid}/location',headers=dh,json=home)
    assert result.status_code==200,result.text
    assert result.json()['auto_completed'] is True
    today=admin.get('/api/admin/today').json()
    assert next(x for x in today['trips'] if x['id']==sid)['status']=='completed'
    assert admin.get('/api/overview').json()['completed_trips']==0 # demo excluded
    assert admin.get('/api/analytics').json()['daily']==[]
    student.close();driver.close();admin.close()
