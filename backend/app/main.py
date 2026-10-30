import os, secrets, hashlib, hmac, re, io, json, sqlite3, threading, csv, math
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from .store import init_db, connect, db, one, all_rows, audit, ROOT
from .forecast import build_forecast

app = FastAPI(title='CampusRide — Connected Transport', version='2.0.0', docs_url='/api/docs', redoc_url=None)
app.mount('/assets',StaticFiles(directory=ROOT/'app'/'static'),name='assets')
ph=PasswordHasher(time_cost=2,memory_cost=32768,parallelism=2)
COOKIE='campusride_session'
SESSION_DAYS=7
SERVICE_TZ=ZoneInfo(os.getenv('CAMPUSRIDE_TIMEZONE','Asia/Kolkata'))
LOGIN_LIMIT={}
LOGIN_LOCK=threading.Lock()

def now(): return datetime.now(timezone.utc).isoformat()
def problem(status, msg): raise HTTPException(status_code=status, detail=msg)
def need(cond, msg, status=400):
    if not cond: problem(status,msg)
def textval(value, field, low=1, high=200):
    need(isinstance(value,str) and low<=len(value.strip())<=high,f'{field} must contain {low}–{high} characters')
    return value.strip()
def intval(value,field,minimum=None,maximum=None):
    try:
        if isinstance(value,bool): raise ValueError()
        val=int(value)
        if str(val)!=str(value).strip() and not isinstance(value,int): raise ValueError()
    except (TypeError,ValueError): problem(400,f'{field} must be an integer')
    need(minimum is None or val>=minimum,f'{field} too small')
    need(maximum is None or val<=maximum,f'{field} too large')
    return val
def flag(x):return 1 if x is True or x==1 else 0

def service_today(): return datetime.now(SERVICE_TZ).date().isoformat()

def validate_date(d):
    try: return date.fromisoformat(d).isoformat()
    except (ValueError,TypeError): problem(400,'Invalid date (YYYY-MM-DD)')
def validate_time(s):
    need(isinstance(s,str) and re.fullmatch(r'\d{2}:\d{2}',s) is not None,'Time must be HH:MM')
    h,m=map(int,s.split(':'));need(h<24 and m<60,'Invalid time');return s

def session_actor(req):
    token=req.cookies.get(COOKIE)
    if not token: return None
    h=hashlib.sha256(token.encode()).hexdigest()
    with db() as c:
        x=one(c,'''SELECT u.id,u.name,u.email,u.role,u.student_number,u.phone,u.active,u.must_change_password,
        s.csrf,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?''',(h,))
    if not x or x['expires_at']<now() or not x['active']:return None
    return x

def actor(req,*roles):
    u=session_actor(req)
    if not u:problem(401,'Sign in required')
    if roles and u['role'] not in roles:problem(403,'Not authorized for this action')
    if req.method not in ('GET','HEAD','OPTIONS'):
        x=req.headers.get('x-csrf-token','')
        if not hmac.compare_digest(x,u['csrf']):problem(403,'Session security token missing or expired. Reload the page.')
    return u

def public_user(x):return {k:x.get(k) for k in ('id','name','email','role','student_number','phone','active','must_change_password')}

@app.middleware('http')
async def security_headers(req,call_next):
    if req.method not in ('GET','HEAD','OPTIONS'):
        origin=req.headers.get('origin')
        if origin:
            try:
                if (urlsplit(origin).netloc.lower()!=req.headers.get('host','').lower()
                    and origin.rstrip('/').lower()!=os.getenv('CAMPUSRIDE_WEB_ORIGIN','').rstrip('/').lower()):
                    return Response('{"detail":"Cross-origin request rejected"}',status_code=403,media_type='application/json')
            except Exception:
                return Response(status_code=403)
    res=await call_next(req)
    res.headers['X-Content-Type-Options']='nosniff'
    res.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    res.headers['X-Frame-Options']='DENY'
    res.headers['Permissions-Policy']='camera=(self), geolocation=(self), microphone=()'
    res.headers['Cache-Control']='no-store' if req.url.path.startswith('/api/') else 'no-cache'
    res.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'; manifest-src 'self'"
    return res

@app.get('/')
def index():return FileResponse(ROOT/'app'/'static'/'index.html')

@app.get('/admin')
@app.get('/student')
@app.get('/driver')
def role_page(): return FileResponse(ROOT/'app'/'static'/'index.html')

@app.get('/sw.js')
def service_worker():
    return FileResponse(ROOT/'app'/'static'/'service-worker.js',media_type='text/javascript',headers={'Service-Worker-Allowed':'/'})

@app.get('/api/health')
def health():
    with db() as c:c.execute('SELECT 1')
    return {'ok':True,'service':'CampusRide','utc':now()}

@app.post('/api/login')
async def login(req:Request):
    d=await req.json(); email=str(d.get('email','')).strip().lower(); pw=str(d.get('password',''))
    ip=req.client.host if req.client else 'unknown'
    with LOGIN_LOCK:
        attempts=[t for t in LOGIN_LIMIT.get(ip,[]) if (datetime.now(timezone.utc).timestamp()-t)<900]
        if len(attempts)>=12:problem(429,'Too many login attempts. Try again later.')
        LOGIN_LIMIT[ip]=attempts
    with db() as c:u=one(c,'SELECT * FROM users WHERE email=?',(email,))
    valid=False
    if u and u['active']:
        try:valid=ph.verify(u['password_hash'],pw)
        except (VerifyMismatchError,VerificationError): pass
    if not valid:
        with LOGIN_LOCK: LOGIN_LIMIT.setdefault(ip,[]).append(datetime.now(timezone.utc).timestamp())
        problem(401,'Invalid credentials')
    with LOGIN_LOCK:LOGIN_LIMIT.pop(ip,None)
    token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
    with db(True) as c:
        c.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],csrf,(datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)).isoformat(),now()))
        audit(c,u['id'],'login','session')
    res=Response(content=json.dumps({'user':public_user(u),'csrf':csrf}),media_type='application/json')
    res.set_cookie(COOKIE,token,httponly=True,samesite='lax',secure=os.getenv('CAMPUSRIDE_SECURE_COOKIES','0')=='1' or req.url.scheme=='https',max_age=SESSION_DAYS*86400,path='/')
    return res

@app.get('/api/me')
def me(req:Request):
    u=actor(req)
    return {'user':public_user(u),'csrf':u['csrf']}

@app.post('/api/logout')
def logout(req:Request):
    u=actor(req); t=req.cookies.get(COOKIE,'')
    with db(True) as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(t.encode()).hexdigest(),))
    res=Response(content='{"ok":true}',media_type='application/json');res.delete_cookie(COOKIE,path='/');return res

@app.post('/api/me/password')
async def change_password(req:Request):
    u=actor(req); d=await req.json()
    old=str(d.get('old_password','')); new=str(d.get('new_password',''))
    need(len(new)>=12 and len(new)<=128,'New password must be 12–128 characters')
    with db(True) as c:
        current=one(c,'SELECT password_hash FROM users WHERE id=?',(u['id'],))
        try:verified=ph.verify(current['password_hash'],old)
        except (VerifyMismatchError,VerificationError):verified=False
        need(verified,'Current password incorrect',403)
        c.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(ph.hash(new),u['id']))
        c.execute('DELETE FROM sessions WHERE user_id=? AND token_hash!=?',(u['id'],hashlib.sha256(req.cookies[COOKIE].encode()).hexdigest()))
        audit(c,u['id'],'password_change','user',u['id'])
    return {'ok':True}

@app.get('/api/users')
def users(req:Request,role:str|None=None):
    actor(req,'admin')
    need(role is None or role in ('admin','student','driver'),'Unknown role')
    with db() as c:return all_rows(c,'SELECT id,name,email,role,student_number,phone,active,must_change_password,created_at FROM users '+('WHERE role=? ' if role else '')+'ORDER BY name',(role,) if role else ())

@app.post('/api/users')
async def create_user(req:Request):
    u=actor(req,'admin'); d=await req.json()
    name=textval(d.get('name'),'Name',2,100)
    email=textval(d.get('email'),'Email',5,254).lower();need(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) is not None,'Invalid email')
    role=d.get('role');need(role in ('student','driver','admin'),'Role must be student, driver or admin')
    password=str(d.get('password',''));need(12<=len(password)<=128,'Temporary password must be 12–128 characters')
    number=str(d.get('student_number') or '').strip().upper() or None
    if role=='student':need(number is not None and len(number)<=50,'Student ID required')
    else:need(number is None,'Student ID applies only to students')
    phone=str(d.get('phone') or '').strip() or None
    with db(True) as c:
        try:
            cur=c.execute('INSERT INTO users(name,email,role,password_hash,student_number,phone,created_at) VALUES(?,?,?,?,?,?,?)',
             (name,email,role,ph.hash(password),number,phone,now()))
        except sqlite3.IntegrityError:problem(409,'Email or student ID already exists')
        audit(c,u['id'],'create','user',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/users/{id}')
async def update_user(id:int,req:Request):
    u=actor(req,'admin');d=await req.json()
    with db(True) as c:
        target=one(c,'SELECT * FROM users WHERE id=?',(id,));need(target,'User not found',404)
        updates={}
        for key in ('name','phone'):
            if key in d:updates[key]=textval(d[key],key,2 if key=='name' else 0,100)
        if 'active' in d:
            need(not (id==u['id'] and not flag(d['active'])),'Cannot deactivate your own account')
            updates['active']=flag(d['active'])
        need(bool(updates),'No changes provided')
        c.execute('UPDATE users SET '+','.join(f'{k}=?' for k in updates)+' WHERE id=?',(*updates.values(),id))
        if updates.get('active')==0:c.execute('DELETE FROM sessions WHERE user_id=?',(id,))
        audit(c,u['id'],'update','user',id,updates)
    return {'ok':True}

@app.get('/api/buses')
def buses(req:Request):
    actor(req)
    with db() as c:return all_rows(c,'SELECT * FROM buses ORDER BY label')

@app.post('/api/buses')
async def add_bus(req:Request):
    u=actor(req,'admin');d=await req.json()
    plate=textval(d.get('plate'),'Registration',3,30).upper();label=textval(d.get('label'),'Bus label',2,80)
    cap=intval(d.get('capacity'),'Capacity',1,120);notes=str(d.get('notes') or '')[:400]
    with db(True) as c:
        try:cur=c.execute('INSERT INTO buses(plate,label,capacity,notes,created_at) VALUES(?,?,?,?,?)',(plate,label,cap,notes,now()))
        except sqlite3.IntegrityError:problem(409,'Registration number already exists')
        audit(c,u['id'],'create','bus',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/buses/{id}')
async def update_bus(id:int,req:Request):
    u=actor(req,'admin');d=await req.json()
    upd={}
    if 'plate' in d:upd['plate']=textval(d['plate'],'Registration',3,30).upper()
    if 'label' in d:upd['label']=textval(d['label'],'Label',2,80)
    if 'capacity' in d:upd['capacity']=intval(d['capacity'],'Capacity',1,120)
    if 'notes' in d:upd['notes']=str(d['notes'])[:400]
    if 'active' in d:upd['active']=flag(d['active'])
    need(upd,'No changes')
    with db(True) as c:
        need(one(c,'SELECT id FROM buses WHERE id=?',(id,)),'Bus not found',404)
        if upd.get('active')==0: need(not one(c,"SELECT id FROM schedules WHERE bus_id=? AND status='active'",(id,)),'Bus has active trip',409)
        try:c.execute('UPDATE buses SET '+','.join(f'{k}=?' for k in upd)+' WHERE id=?',(*upd.values(),id))
        except sqlite3.IntegrityError:problem(409,'Registration already in use')
        audit(c,u['id'],'update','bus',id,upd)
    return {'ok':True}

@app.get('/api/routes')
def routes(req:Request):
    actor(req)
    with db() as c:
        rr=all_rows(c,'SELECT * FROM routes ORDER BY code')
        for r in rr:r['stops']=all_rows(c,'SELECT * FROM stops WHERE route_id=? ORDER BY stop_order',(r['id'],))
    return rr

@app.post('/api/routes')
async def add_route(req:Request):
    u=actor(req,'admin');d=await req.json()
    code=textval(d.get('code'),'Route code',1,30).upper();name=textval(d.get('name'),'Route name',2,100)
    desc=str(d.get('description') or '')[:300]
    with db(True) as c:
        try:cur=c.execute('INSERT INTO routes(code,name,description,created_at) VALUES(?,?,?,?)',(code,name,desc,now()))
        except sqlite3.IntegrityError:problem(409,'Route code already exists')
        audit(c,u['id'],'create','route',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/routes/{id}')
async def update_route(id:int,req:Request):
    u=actor(req,'admin');d=await req.json();upd={}
    if 'name' in d:upd['name']=textval(d['name'],'Route name',2,100)
    if 'description' in d:upd['description']=str(d['description'])[:300]
    if 'active' in d:upd['active']=flag(d['active'])
    need(upd,'No changes')
    with db(True) as c:
        need(one(c,'SELECT id FROM routes WHERE id=?',(id,)),'Route not found',404)
        if upd.get('active')==0:need(not one(c,"SELECT id FROM schedules WHERE route_id=? AND status='active'",(id,)),'Route has active trip',409)
        c.execute('UPDATE routes SET '+','.join(f'{k}=?' for k in upd)+' WHERE id=?',(*upd.values(),id))
        audit(c,u['id'],'update','route',id,upd)
    return {'ok':True}

@app.post('/api/routes/{id}/stops')
async def add_stop(id:int,req:Request):
    u=actor(req,'admin');d=await req.json()
    name=textval(d.get('name'),'Stop name',2,100);stop_order=intval(d.get('stop_order'),'Stop order',1,200)
    try:lat=float(d.get('lat'));lon=float(d.get('lon'))
    except (TypeError,ValueError):problem(400,'Valid latitude and longitude required')
    need(-90<=lat<=90 and -180<=lon<=180,'Coordinates out of range')
    pickup=d.get('pickup_time') or None
    if pickup:validate_time(pickup)
    with db(True) as c:
        need(one(c,'SELECT id FROM routes WHERE id=?',(id,)),'Route not found',404)
        try:cur=c.execute('INSERT INTO stops(route_id,name,stop_order,lat,lon,pickup_time) VALUES(?,?,?,?,?,?)',(id,name,stop_order,lat,lon,pickup))
        except sqlite3.IntegrityError:problem(409,'Stop name or order already exists for this route')
        audit(c,u['id'],'create','stop',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.delete('/api/stops/{id}')
def delete_stop(id:int,req:Request):
    u=actor(req,'admin')
    with db(True) as c:
        need(one(c,'SELECT id FROM stops WHERE id=?',(id,)),'Stop not found',404)
        try:c.execute('DELETE FROM stops WHERE id=?',(id,))
        except sqlite3.IntegrityError:problem(409,'Stop is in use by a transport allocation or trip record')
        audit(c,u['id'],'delete','stop',id)
    return {'ok':True}

@app.get('/api/allocations')
def allocations(req:Request):
    u=actor(req)
    with db() as c:
        q='''SELECT a.*,u.name student_name,u.student_number,r.code route_code,r.name route_name,
        st.name stop_name FROM allocations a JOIN users u ON a.student_id=u.id
        JOIN routes r ON r.id=a.route_id JOIN stops st ON st.id=a.stop_id'''
        if u['role']=='student':return all_rows(c,q+' WHERE a.student_id=? ORDER BY a.id DESC',(u['id'],))
        if u['role']=='driver':problem(403,'Driver access not permitted')
        return all_rows(c,q+' ORDER BY a.active DESC,a.id DESC')

@app.post('/api/allocations')
async def allocate(req:Request):
    u=actor(req,'admin');d=await req.json();sid=intval(d.get('student_id'),'Student',1)
    rid=intval(d.get('route_id'),'Route',1);stop_id=intval(d.get('stop_id'),'Stop',1)
    with db(True) as c:
        student=one(c,'SELECT * FROM users WHERE id=? AND role=? AND active=1',(sid,'student'))
        need(student,'Active student not found',404)
        r=one(c,'SELECT * FROM routes WHERE id=? AND active=1',(rid,));need(r,'Active route not found',404)
        need(one(c,'SELECT id FROM stops WHERE id=? AND route_id=?',(stop_id,rid)),'Stop does not belong to route')
        need(not one(c,'SELECT id FROM allocations WHERE student_id=? AND active=1',(sid,)),'Student already has an active allocation; deactivate it first',409)
        future=all_rows(c,"SELECT s.id,b.capacity FROM schedules s JOIN buses b ON b.id=s.bus_id WHERE s.route_id=? AND s.status IN ('scheduled','active')",(rid,))
        for trip in future:
            fixed=one(c,'SELECT COUNT(*) n FROM allocations WHERE route_id=? AND active=1',(rid,))['n']
            extras=one(c,"SELECT COUNT(*) n FROM requests WHERE schedule_id=? AND status='approved'",(trip['id'],))['n']
            need(fixed+extras+1<=trip['capacity'],'Adding student would exceed capacity on a published trip',409)
        code=secrets.token_urlsafe(18).replace('-','').replace('_','').upper()[:20]
        try:cur=c.execute('INSERT INTO allocations(student_id,route_id,stop_id,pass_code,created_at) VALUES(?,?,?,?,?)',(sid,rid,stop_id,code,now()))
        except sqlite3.IntegrityError:problem(409,'Student is already allocated')
        audit(c,u['id'],'create','allocation',cur.lastrowid,{'student_id':sid,'route_id':rid})
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/allocations/{id}')
async def update_allocation(id:int,req:Request):
    u=actor(req,'admin');d=await req.json();need('active' in d,'Provide active flag')
    want=flag(d['active']);need(want==0,'Reactivation is not supported; create a fresh allocation')
    with db(True) as c:
        a=one(c,'SELECT * FROM allocations WHERE id=?',(id,));need(a,'Allocation not found',404)
        need(a['active'],'Allocation already inactive',409)
        c.execute('UPDATE allocations SET active=0,ended_at=? WHERE id=?',(now(),id))
        audit(c,u['id'],'deactivate','allocation',id)
    return {'ok':True}

def sched_q():
    return '''SELECT s.*,r.code route_code,r.name route_name,b.label bus_label,b.plate bus_plate,b.capacity,
    u.name driver_name,(SELECT COUNT(*) FROM boardings bo WHERE bo.schedule_id=s.id) boarded,
    (SELECT COUNT(*) FROM allocations a WHERE a.route_id=s.route_id AND a.active=1) assigned,
    (SELECT COUNT(*) FROM requests rq WHERE rq.schedule_id=s.id AND rq.status='approved') extra_approved
    FROM schedules s JOIN routes r ON r.id=s.route_id JOIN buses b ON b.id=s.bus_id JOIN users u ON u.id=s.driver_id'''

def visible_schedule(u,s,c):
    if u['role']=='admin':return True
    if u['role']=='driver':return s['driver_id']==u['id']
    return bool(one(c,'SELECT id FROM allocations WHERE student_id=? AND route_id=? AND active=1',(u['id'],s['route_id'])) or
                one(c,"SELECT id FROM requests WHERE student_id=? AND schedule_id=? AND status='approved'",(u['id'],s['id'])))

@app.get('/api/schedules')
def schedules(req:Request,service_date:str|None=None,browse:bool=False):
    u=actor(req)
    if service_date:validate_date(service_date)
    with db() as c:
        sql=sched_q();args=[];clauses=[]
        if service_date:clauses.append('s.service_date=?');args.append(service_date)
        if u['role']=='driver':clauses.append('s.driver_id=?');args.append(u['id'])
        if u['role']=='student' and not browse:clauses.append("(EXISTS(SELECT 1 FROM allocations a WHERE a.student_id=? AND a.route_id=s.route_id AND a.active=1) OR EXISTS(SELECT 1 FROM requests q WHERE q.student_id=? AND q.schedule_id=s.id AND q.status='approved'))");args.extend([u['id'],u['id']])
        if u['role']=='student' and browse:clauses.extend(["s.status='scheduled'",'s.service_date>=?']);args.append(service_today())
        if clauses:sql+=' WHERE '+' AND '.join(clauses)
        return all_rows(c,sql+' ORDER BY s.service_date DESC,s.departure_time DESC LIMIT 300',tuple(args))

@app.post('/api/schedules')
async def add_schedule(req:Request):
    u=actor(req,'admin');d=await req.json()
    rid=intval(d.get('route_id'),'Route',1);bid=intval(d.get('bus_id'),'Bus',1);did=intval(d.get('driver_id'),'Driver',1)
    day=validate_date(d.get('service_date'));time=validate_time(d.get('departure_time'))
    shift=d.get('shift','morning');need(shift in ('morning','evening'),'Shift must be morning or evening')
    duration=intval(d.get('duration_minutes',120),'Trip duration',10,600);test=flag(d.get('is_test',False))
    start=int(time[:2])*60+int(time[3:]);end=start+duration
    with db(True) as c:
        r=one(c,'SELECT * FROM routes WHERE id=? AND active=1',(rid,));need(r,'Route not found or inactive')
        need(one(c,'SELECT id FROM stops WHERE route_id=?',(rid,)),'Add route stops first')
        b=one(c,'SELECT * FROM buses WHERE id=? AND active=1',(bid,));need(b,'Bus not found or inactive')
        driver=one(c,"SELECT * FROM users WHERE id=? AND role='driver' AND active=1",(did,));need(driver,'Active driver not found')
        allocated=one(c,'SELECT COUNT(*) count FROM allocations WHERE route_id=? AND active=1',(rid,))['count']
        need(allocated<=b['capacity'],f'{allocated} students allocated but bus has only {b["capacity"]} seats',409)
        conflicts=all_rows(c,"SELECT * FROM schedules WHERE service_date=? AND status IN ('scheduled','active') AND (bus_id=? OR driver_id=?)",(day,bid,did))
        for other in conflicts:
            other_start=int(other['departure_time'][:2])*60+int(other['departure_time'][3:]);other_end=other_start+other['duration_minutes']
            need(end<=other_start or start>=other_end,'Bus or driver has an overlapping scheduled trip',409)
        cur=c.execute('INSERT INTO schedules(route_id,bus_id,driver_id,service_date,departure_time,duration_minutes,is_test,created_at,shift) VALUES(?,?,?,?,?,?,?,?,?)',
         (rid,bid,did,day,time,duration,test,now(),shift))
        audit(c,u['id'],'create','schedule',cur.lastrowid,{'is_test':test})
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/schedules/{id}')
async def edit_schedule(id:int,req:Request):
    u=actor(req,'admin');d=await req.json()
    status=d.get('status');need(status=='cancelled','Only cancelling is supported once a schedule is published')
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(id,));need(s,'Schedule not found',404)
        need(s['status']=='scheduled','Only scheduled trips may be cancelled',409)
        c.execute("UPDATE schedules SET status='cancelled',cancelled_at=? WHERE id=?",(now(),id))
        audit(c,u['id'],'cancel','schedule',id)
    return {'ok':True}

@app.post('/api/schedules/{id}/start')
def start_trip(id:int,req:Request):
    u=actor(req,'driver','admin')
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(id,));need(s,'Trip not found',404)
        need(u['role']=='admin' or s['driver_id']==u['id'],'Not your assigned trip',403)
        need(s['status']=='scheduled','Trip is not scheduled',409)
        need(s['service_date']==service_today() or u['role']=='admin','Drivers can only start trips for the server-local date',409)
        need(not one(c,"SELECT id FROM schedules WHERE driver_id=? AND status='active'",(s['driver_id'],)),'Driver already has an active trip',409)
        need(not one(c,"SELECT id FROM schedules WHERE bus_id=? AND status='active'",(s['bus_id'],)),'Bus already in an active trip',409)
        c.execute("UPDATE schedules SET status='active',started_at=? WHERE id=?",(now(),id))
        audit(c,u['id'],'start','schedule',id)
    return {'ok':True}

@app.post('/api/schedules/{id}/finish')
def finish_trip(id:int,req:Request):
    u=actor(req,'driver','admin')
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(id,));need(s,'Trip not found',404)
        need(u['role']=='admin' or s['driver_id']==u['id'],'Not your assigned trip',403)
        need(s['status']=='active','Trip is not active',409)
        c.execute("UPDATE schedules SET status='completed',ended_at=? WHERE id=?",(now(),id))
        audit(c,u['id'],'finish','schedule',id)
    return {'ok':True}

@app.post('/api/schedules/{id}/location')
async def location(id:int,req:Request):
    u=actor(req,'driver'); d=await req.json()
    try:
        lat=float(d['lat']);lon=float(d['lon']);accuracy=float(d['accuracy']);timestamp=float(d['timestamp'])
        speed=float(d['speed']) if d.get('speed') is not None else None
        heading=float(d['heading']) if d.get('heading') is not None else None
    except (ValueError,TypeError,KeyError,OverflowError):problem(400,'Invalid device location data')
    need(-90<=lat<=90 and -180<=lon<=180 and 0<=accuracy<=5000,'Location out of range')
    now_ms=datetime.now(timezone.utc).timestamp()*1000
    need(abs(now_ms-timestamp)<120000,'GPS location is stale or clock differs by over 2 minutes')
    measured=datetime.fromtimestamp(timestamp/1000,timezone.utc).isoformat()
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(id,))
        need(s and s['driver_id']==u['id'],'Assigned trip not found',404)
        need(s['status']=='active','Only an active trip can submit GPS data',409)
        last=one(c,'SELECT measured_at FROM locations WHERE schedule_id=? ORDER BY id DESC LIMIT 1',(id,))
        need(not last or measured>last['measured_at'],'Location must be newer than previous update',409)
        c.execute('INSERT INTO locations(schedule_id,lat,lon,accuracy,speed,heading,measured_at,received_at) VALUES(?,?,?,?,?,?,?,?)',
         (id,lat,lon,accuracy,speed,heading,measured,now()))
    completed=False
    # Auto-close only on a genuine return to campus after a measured journey.
    # A driver can still finish manually if GPS is unreliable.
    if accuracy<=75:
        with db(True) as c:
            campus=one(c,"SELECT lat,lon FROM stops WHERE route_id=? ORDER BY stop_order LIMIT 1",(s['route_id'],))
            first=one(c,"SELECT lat,lon FROM locations WHERE schedule_id=? ORDER BY id LIMIT 1",(id,))
            started=datetime.fromisoformat(s['started_at']) if s.get('started_at') else datetime.now(timezone.utc)
            seconds=(datetime.now(timezone.utc)-started).total_seconds()
            if campus and first and seconds>=360 and _metres(lat,lon,campus['lat'],campus['lon'])<=95:
                left=one(c,'''SELECT id FROM locations WHERE schedule_id=? AND
                  ((lat-?)*(lat-?)+(lon-?)*(lon-?))>? AND accuracy<=75 LIMIT 1''',
                  (id,campus['lat'],campus['lat'],campus['lon'],campus['lon'],(0.0055)**2))
                if left:
                    c.execute("UPDATE schedules SET status='completed',ended_at=? WHERE id=? AND status='active'",(now(),id))
                    completed=c.total_changes>0
                    if completed:audit(c,u['id'],'auto_return_to_campus','schedule',id)
    return {'ok':True,'measured_at':measured,'auto_completed':completed}

def _metres(a,b,c,d):
    p1,p2=math.radians(a),math.radians(c)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(math.radians(d-b)/2)**2
    return 12742000*math.asin(min(1,math.sqrt(h)))

@app.get('/api/live')
def live(req:Request):
    u=actor(req)
    with db() as c:
        ss=all_rows(c,sched_q()+" WHERE s.status='active' ORDER BY s.started_at DESC")
        result=[]
        for s in ss:
            if visible_schedule(u,s,c):
                s['last_location']=one(c,'SELECT lat,lon,accuracy,speed,heading,measured_at,received_at FROM locations WHERE schedule_id=? ORDER BY id DESC LIMIT 1',(s['id'],))
                result.append(s)
        return result

@app.get('/api/schedules/{id}/trail')
def trail(id:int,req:Request):
    u=actor(req)
    with db() as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(id,));need(s,'Trip not found',404)
        need(visible_schedule(u,s,c),'Not permitted',403)
        return all_rows(c,'SELECT lat,lon,accuracy,measured_at FROM locations WHERE schedule_id=? ORDER BY id DESC LIMIT 100',(id,))[::-1]

@app.get('/api/my/pass')
def my_pass(req:Request):
    u=actor(req,'student')
    with db() as c:
        a=one(c,'''SELECT a.*,r.code route_code,r.name route_name,st.name stop_name
          FROM allocations a JOIN routes r ON r.id=a.route_id JOIN stops st ON st.id=a.stop_id
          WHERE a.student_id=? AND a.active=1''',(u['id'],))
    if not a:return {'allocation':None}
    return {'allocation':a,'student':{'name':u['name'],'student_number':u['student_number']}}

@app.get('/api/my/pass-qr.svg')
def pass_qr(req:Request):
    u=actor(req,'student')
    with db() as c:a=one(c,'SELECT pass_code FROM allocations WHERE student_id=? AND active=1',(u['id'],))
    need(a,'No active bus pass',404)
    import qrcode, qrcode.image.svg
    img=qrcode.make('CAMPUSRIDE:'+a['pass_code'],image_factory=qrcode.image.svg.SvgPathImage,box_size=10,border=2)
    buf=io.BytesIO();img.save(buf)
    return Response(buf.getvalue(),media_type='image/svg+xml',headers={'Content-Disposition':'inline; filename=bus-pass.svg'})

@app.post('/api/schedules/{id}/board')
async def board(id:int,req:Request):
    u=actor(req,'driver','admin');d=await req.json()
    raw=str(d.get('pass_code') or '').strip().upper()
    code=raw.removeprefix('CAMPUSRIDE:')
    need(12<=len(code)<=40,'Invalid pass code')
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?'+(' FOR UPDATE' if os.getenv('DATABASE_URL') else ''),(id,));need(s,'Trip not found',404)
        need(s['status']=='active','Trip must be active',409)
        need(u['role']=='admin' or s['driver_id']==u['id'],'Not your assigned trip',403)
        a=one(c,'SELECT * FROM allocations WHERE pass_code=? AND active=1',(code,))
        rq=None
        if not a:
            # Passes are tied to an active allocation; temporary requests use the same student pass.
            problem(404,'Unknown or inactive pass')
        if a['route_id']!=s['route_id']:
            rq=one(c,"SELECT * FROM requests WHERE student_id=? AND schedule_id=? AND status='approved'",(a['student_id'],id))
            need(rq,'Student is not assigned or approved for this route',403)
        if rq:
            assigned_stop=rq['stop_id']
        else:assigned_stop=a['stop_id']
        need(not one(c,'SELECT id FROM boardings WHERE schedule_id=? AND student_id=?',(id,a['student_id'])),'Student already checked in on this trip',409)
        count=one(c,'SELECT COUNT(*) n FROM boardings WHERE schedule_id=?',(id,))['n']
        capacity=one(c,'SELECT capacity FROM buses WHERE id=?',(s['bus_id'],))['capacity']
        need(count<capacity,'Vehicle capacity reached',409)
        cur=c.execute('INSERT INTO boardings(schedule_id,allocation_id,student_id,stop_id,recorded_at,recorded_by) VALUES(?,?,?,?,?,?)',
          (id,a['id'],a['student_id'],assigned_stop,now(),u['id']))
        audit(c,u['id'],'board','boarding',cur.lastrowid,{'schedule_id':id})
        student=one(c,'SELECT name,student_number FROM users WHERE id=?',(a['student_id'],))
    return {'ok':True,'student':student,'boarding_id':cur.lastrowid}

@app.get('/api/schedules/{id}/boardings')
def boardings(id:int,req:Request):
    u=actor(req,'admin','driver')
    with db() as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?'+(' FOR UPDATE' if os.getenv('DATABASE_URL') else ''),(id,));need(s,'Trip not found',404)
        need(u['role']=='admin' or s['driver_id']==u['id'],'Not assigned',403)
        return all_rows(c,'''SELECT b.*,u.name student_name,u.student_number,st.name stop_name
           FROM boardings b JOIN users u ON u.id=b.student_id JOIN stops st ON st.id=b.stop_id WHERE b.schedule_id=? ORDER BY b.recorded_at DESC''',(id,))

@app.get('/api/incidents')
def incidents(req:Request):
    u=actor(req)
    with db() as c:
        rows=all_rows(c,'''SELECT i.*,s.route_id,s.service_date,r.code route_code,u.name reporter
        FROM incidents i JOIN schedules s ON s.id=i.schedule_id JOIN routes r ON r.id=s.route_id
        JOIN users u ON u.id=i.reported_by ORDER BY i.created_at DESC LIMIT 100''')
        return [i for i in rows if u['role']=='admin' or (u['role']=='driver' and one(c,'SELECT id FROM schedules WHERE id=? AND driver_id=?',(i['schedule_id'],u['id']))) or (u['role']=='student' and one(c,'SELECT id FROM allocations WHERE student_id=? AND route_id=? AND active=1',(u['id'],i['route_id'])))]

@app.post('/api/incidents')
async def report_incident(req:Request):
    u=actor(req,'driver','admin');d=await req.json()
    sid=intval(d.get('schedule_id'),'Trip',1);cat=d.get('category')
    need(cat in ('delay','breakdown','emergency','traffic','other'),'Invalid incident type')
    desc=textval(d.get('description'),'Description',5,500)
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(sid,));need(s,'Trip not found',404)
        need(u['role']=='admin' or s['driver_id']==u['id'],'Not your trip',403)
        need(s['status'] in ('active','scheduled'),'Cannot report against completed/cancelled trip')
        cur=c.execute('INSERT INTO incidents(schedule_id,category,description,reported_by,created_at) VALUES(?,?,?,?,?)',(sid,cat,desc,u['id'],now()))
        audit(c,u['id'],'create','incident',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/incidents/{id}')
def resolve_incident(id:int,req:Request):
    u=actor(req,'admin')
    with db(True) as c:
        i=one(c,'SELECT * FROM incidents WHERE id=?',(id,));need(i,'Incident not found',404)
        need(not i['resolved_at'],'Already resolved',409)
        c.execute('UPDATE incidents SET resolved_at=?,resolved_by=? WHERE id=?',(now(),u['id'],id))
        audit(c,u['id'],'resolve','incident',id)
    return {'ok':True}

@app.get('/api/notices')
def notices(req:Request):
    u=actor(req)
    with db() as c:
        rr=all_rows(c,'SELECT n.*,r.code route_code FROM notices n LEFT JOIN routes r ON r.id=n.route_id ORDER BY n.created_at DESC LIMIT 100')
        if u['role']=='student':
            routes={a['route_id'] for a in all_rows(c,'SELECT route_id FROM allocations WHERE active=1 AND student_id=?',(u['id'],))}
            rr=[n for n in rr if n['route_id'] is None or n['route_id'] in routes]
        return rr

@app.post('/api/notices')
async def create_notice(req:Request):
    u=actor(req,'admin');d=await req.json();title=textval(d.get('title'),'Title',3,120);body=textval(d.get('body'),'Message',3,600)
    rid=d.get('route_id') or None
    if rid:rid=intval(rid,'Route',1)
    with db(True) as c:
        if rid:need(one(c,'SELECT id FROM routes WHERE id=?',(rid,)),'Route not found',404)
        cur=c.execute('INSERT INTO notices(route_id,title,body,author_id,created_at) VALUES(?,?,?,?,?)',(rid,title,body,u['id'],now()))
        audit(c,u['id'],'create','notice',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.get('/api/requests')
def requests_list(req:Request):
    u=actor(req)
    with db() as c:
        query='''SELECT q.*,u.name student_name,u.student_number,r.code route_code,s.service_date,s.departure_time,st.name stop_name
        FROM requests q JOIN users u ON u.id=q.student_id JOIN schedules s ON s.id=q.schedule_id
        JOIN routes r ON r.id=s.route_id JOIN stops st ON st.id=q.stop_id'''
        if u['role']=='student':return all_rows(c,query+' WHERE q.student_id=? ORDER BY q.created_at DESC',(u['id'],))
        need(u['role']=='admin','Not authorized',403)
        return all_rows(c,query+' ORDER BY q.created_at DESC')

@app.post('/api/requests')
async def request_transfer(req:Request):
    u=actor(req,'student');d=await req.json();sid=intval(d.get('schedule_id'),'Trip',1)
    stop_id=intval(d.get('stop_id'),'Stop',1);reason=textval(d.get('reason'),'Reason',5,250)
    with db(True) as c:
        s=one(c,'SELECT * FROM schedules WHERE id=?',(sid,));need(s and s['status']=='scheduled','Trip unavailable',404)
        need(s['service_date']>=service_today(),'Cannot request a past trip')
        need(one(c,'SELECT * FROM stops WHERE id=? AND route_id=?',(stop_id,s['route_id'])),'Choose a stop on this route')
        need(one(c,'SELECT id FROM allocations WHERE student_id=? AND active=1',(u['id'],)),'Student must have a current transport allocation',403)
        need(not one(c,'SELECT id FROM allocations WHERE student_id=? AND route_id=? AND active=1',(u['id'],s['route_id'])),'Already allocated to this route',409)
        try:cur=c.execute('INSERT INTO requests(student_id,schedule_id,stop_id,reason,created_at) VALUES(?,?,?,?,?)',(u['id'],sid,stop_id,reason,now()))
        except sqlite3.IntegrityError:problem(409,'You already have a request for this trip')
        audit(c,u['id'],'create','request',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/requests/{id}')
async def decide_request(id:int,req:Request):
    u=actor(req,'admin');d=await req.json();status=d.get('status')
    need(status in ('approved','rejected'),'Status must be approved or rejected')
    with db(True) as c:
        q=one(c,'SELECT * FROM requests WHERE id=?',(id,));need(q,'Request not found',404)
        need(q['status']=='pending','Request already decided',409)
        s=one(c,'SELECT * FROM schedules WHERE id=?',(q['schedule_id'],))
        need(s['status']=='scheduled','Trip not open for decisions',409)
        if status=='approved':
            capacity=one(c,'SELECT capacity FROM buses WHERE id=?',(s['bus_id'],))['capacity']
            fixed=one(c,'SELECT COUNT(*) n FROM allocations WHERE route_id=? AND active=1',(s['route_id'],))['n']
            extras=one(c,"SELECT COUNT(*) n FROM requests WHERE schedule_id=? AND status='approved'",(s['id'],))['n']
            need(fixed+extras<capacity,'No spare seat capacity for this transfer',409)
        c.execute('UPDATE requests SET status=?,decided_at=?,decided_by=? WHERE id=?',(status,now(),u['id'],id))
        audit(c,u['id'],status,'request',id)
    return {'ok':True}

@app.get('/api/overview')
def overview(req:Request):
    actor(req,'admin')
    with db() as c:
        result={}
        queries={'students':"SELECT COUNT(*) n FROM users WHERE role='student' AND active=1",
         'drivers':"SELECT COUNT(*) n FROM users WHERE role='driver' AND active=1",
         'buses':"SELECT COUNT(*) n FROM buses WHERE active=1",
         'routes':"SELECT COUNT(*) n FROM routes WHERE active=1",
         'allocations':"SELECT COUNT(*) n FROM allocations WHERE active=1",
         'active_trips':"SELECT COUNT(*) n FROM schedules WHERE status='active'",
         'completed_trips':"SELECT COUNT(*) n FROM schedules WHERE status='completed' AND is_test=0",
         'boardings':"SELECT COUNT(*) n FROM boardings b JOIN schedules s ON b.schedule_id=s.id WHERE s.is_test=0",
         'open_incidents':"SELECT COUNT(*) n FROM incidents WHERE resolved_at IS NULL",
         'pending_requests':"SELECT COUNT(*) n FROM requests WHERE status='pending'"}
        for k,v in queries.items():result[k]=one(c,v)['n']
        result['recent']=all_rows(c,sched_q()+' ORDER BY s.created_at DESC LIMIT 6')
        return result

@app.get('/api/analytics')
def analytics(req:Request):
    actor(req,'admin')
    with db() as c:
        route_stats=all_rows(c,'''SELECT r.id,r.code,r.name,COUNT(DISTINCT s.id) completed_trips,
          COUNT(b.id) actual_boardings FROM routes r LEFT JOIN schedules s
          ON s.route_id=r.id AND s.status='completed' AND s.is_test=0
          LEFT JOIN boardings b ON b.schedule_id=s.id GROUP BY r.id ORDER BY r.code''')
        daily=all_rows(c,'''SELECT s.service_date,COUNT(DISTINCT s.id) trips,COUNT(b.id) passengers
           FROM schedules s LEFT JOIN boardings b ON b.schedule_id=s.id WHERE s.status='completed' AND s.is_test=0
           GROUP BY s.service_date ORDER BY s.service_date DESC LIMIT 21''')
        return {'routes':route_stats,'daily':daily[::-1], 'note':'Completed non-test trips only. Boarding data is recorded at actual check-in.'}

@app.get('/api/forecast')
def forecast(req:Request,route_id:int|None=None,target_date:str|None=None,departure_time:str|None=None):
    actor(req,'admin')
    with db() as c:
        records=all_rows(c,'''SELECT s.id,s.route_id,s.service_date,s.departure_time,COUNT(b.id) passengers
         FROM schedules s LEFT JOIN boardings b ON b.schedule_id=s.id
         WHERE s.status='completed' AND s.is_test=0
         GROUP BY s.id ORDER BY s.service_date,s.departure_time''')
    return build_forecast(records,route_id,target_date,departure_time)

@app.get('/api/audit')
def audit_records(req:Request):
    actor(req,'admin')
    with db() as c:return all_rows(c,'''SELECT a.*,u.name actor FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100''')



# V2: permanent route/bus/driver assignment, fee ledger and role-specific read models.
CAMPUS = {'name':'KLH University — Bachupally / Bowrampet campus area',
          'lat':17.54661,'lon':78.404776,'source':'Official 2025–26 AICTE disclosure photo; approximate campus area, not a bus gate',
          'official_address':'ALEAP Industrial Area, Bowrampet, Hyderabad, Telangana 500043'}

@app.get('/api/meta')
def meta(req:Request):
    actor(req)
    return {'campus':CAMPUS,'dataset':'DEMONSTRATION' if os.getenv('CAMPUSRIDE_DEMO','0')=='1' else 'REAL RECORDS',
            'disclaimer':'Demo buses, routes, users and fees are fictional. Location updates only come from device GPS.'}

@app.get('/api/fleet')
def fleet(req:Request):
    actor(req)
    with db() as c:
        return all_rows(c,'''SELECT b.*,f.route_id,f.driver_id,r.code route_code,r.name route_name,u.name driver_name,
         (SELECT COUNT(*) FROM allocations a WHERE a.route_id=f.route_id AND a.active=1) students
         FROM buses b LEFT JOIN route_fleet f ON b.id=f.bus_id LEFT JOIN routes r ON r.id=f.route_id
         LEFT JOIN users u ON u.id=f.driver_id ORDER BY b.label''')

@app.post('/api/route-fleet')
async def set_fleet(req:Request):
    u=actor(req,'admin');d=await req.json()
    rid=intval(d.get('route_id'),'Route',1);bid=intval(d.get('bus_id'),'Bus',1);did=intval(d.get('driver_id'),'Driver',1)
    with db(True) as c:
        need(one(c,'SELECT id FROM routes WHERE id=? AND active=1',(rid,)),'Active route not found')
        b=one(c,'SELECT capacity FROM buses WHERE id=? AND active=1',(bid,));need(b,'Active bus not found')
        need(one(c,"SELECT id FROM users WHERE id=? AND role='driver' AND active=1",(did,)),'Driver not found')
        total=one(c,'SELECT COUNT(*) n FROM allocations WHERE route_id=? AND active=1',(rid,))['n']
        need(total<=b['capacity'],'Bus capacity smaller than assigned student count',409)
        need(not one(c,"SELECT id FROM schedules WHERE route_id=? AND status='active'",(rid,)),'Cannot reassign route while trip active',409)
        need(not one(c,"SELECT id FROM schedules WHERE route_id=? AND status IN ('scheduled','active') AND (bus_id!=? OR driver_id!=?)",(rid,bid,did)),
            'Route has upcoming journeys assigned to a different bus/driver. Cancel/recreate the affected schedules before changing permanent allocation.',409)
        need(not one(c,'SELECT route_id FROM route_fleet WHERE bus_id=? AND route_id!=?',(bid,rid)),'Bus already belongs to another route',409)
        need(not one(c,'SELECT route_id FROM route_fleet WHERE driver_id=? AND route_id!=?',(did,rid)),'Driver already belongs to another route',409)
        c.execute('INSERT INTO route_fleet(route_id,bus_id,driver_id,assigned_at) VALUES(?,?,?,?) ON CONFLICT(route_id) DO UPDATE SET bus_id=excluded.bus_id,driver_id=excluded.driver_id,assigned_at=excluded.assigned_at',(rid,bid,did,now()))
        audit(c,u['id'],'assign','route_fleet',rid)
    return {'ok':True}

def _roster(c,route_id):
    return all_rows(c,'''SELECT u.name,u.student_number,u.email,u.phone,st.name pickup_stop,
      r.code route_code,a.id allocation_id
      FROM allocations a JOIN users u ON u.id=a.student_id JOIN stops st ON st.id=a.stop_id
      JOIN routes r ON r.id=a.route_id
      WHERE a.active=1 AND a.route_id=? ORDER BY u.student_number''',(route_id,))

@app.get('/api/admin/roster/{route_id}')
def roster(route_id:int,req:Request):
    actor(req,'admin')
    with db() as c:return _roster(c,route_id)

@app.get('/api/admin/roster.csv/{route_id}')
def roster_csv(route_id:int,req:Request):
    actor(req,'admin')
    with db() as c:
        route=one(c,'SELECT code FROM routes WHERE id=?',(route_id,));need(route,'Route not found',404)
        rows=_roster(c,route_id)
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['Student Name','Roll Number','Email','Phone','Pickup Stop','Route'])
    for r in rows:
        # Prevent spreadsheet formula injection when importing university-supplied fields.
        safe=lambda x: ("'"+str(x)) if str(x).startswith(('=','+','-','@','\t','\r')) else str(x or '')
        writer.writerow([safe(r[k]) for k in ('name','student_number','email','phone','pickup_stop','route_code')])
    return Response('﻿'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{route["code"]}_students.csv"'})

@app.get('/api/admin/today')
def today_board(req:Request,service_date:str|None=None):
    actor(req,'admin');day=validate_date(service_date or service_today())
    with db() as c:
        bb=all_rows(c,'''SELECT b.*,f.route_id,r.code route_code,r.name route_name,u.name driver_name,
          (SELECT COUNT(*) FROM allocations a WHERE a.route_id=f.route_id AND a.active=1) students
          FROM buses b LEFT JOIN route_fleet f ON f.bus_id=b.id LEFT JOIN routes r ON r.id=f.route_id
          LEFT JOIN users u ON u.id=f.driver_id ORDER BY b.label''')
        trips=all_rows(c,sched_q()+' WHERE s.service_date=? ORDER BY s.departure_time',(day,))
        for t in trips:
            t['last_location']=one(c,'SELECT lat,lon,accuracy,speed,measured_at,received_at FROM locations WHERE schedule_id=? ORDER BY id DESC LIMIT 1',(t['id'],))
        return {'date':day,'buses':bb,'trips':trips,'demo':os.getenv('CAMPUSRIDE_DEMO','0')=='1'}

@app.get('/api/my/dashboard')
def student_dashboard(req:Request):
    u=actor(req,'student');today=service_today()
    with db() as c:
        a=one(c,'''SELECT a.*,r.code route_code,r.name route_name,st.name pickup_stop,st.lat pickup_lat,st.lon pickup_lon,st.pickup_time,
         f.bus_id,f.driver_id,b.label bus_label,b.plate,b.capacity,u.name driver_name,u.phone driver_phone
         FROM allocations a JOIN routes r ON r.id=a.route_id JOIN stops st ON st.id=a.stop_id
         LEFT JOIN route_fleet f ON f.route_id=r.id LEFT JOIN buses b ON b.id=f.bus_id
         LEFT JOIN users u ON u.id=f.driver_id
         WHERE a.active=1 AND a.student_id=?''',(u['id'],))
        stops=all_rows(c,'SELECT * FROM stops WHERE route_id=? ORDER BY stop_order',(a['route_id'],)) if a else []
        trips=all_rows(c,sched_q()+" WHERE s.route_id=? AND s.service_date=? ORDER BY s.departure_time",(a['route_id'],today)) if a else []
        for t in trips:t['last_location']=one(c,'SELECT lat,lon,accuracy,measured_at,received_at FROM locations WHERE schedule_id=? ORDER BY id DESC LIMIT 1',(t['id'],))
        fees=all_rows(c,'SELECT id,term,amount_paise,paid_paise,due_date,notes,demo FROM fees WHERE student_id=? ORDER BY id DESC',(u['id'],))
        notices=all_rows(c,'SELECT title,body,created_at FROM notices WHERE route_id IS NULL OR route_id=? ORDER BY id DESC LIMIT 6',(a['route_id'],)) if a else []
    return {'allocation':a,'stops':stops,'trips':trips,'fees':fees,'notices':notices,'student':public_user(u),'today':today}

@app.get('/api/driver/dashboard')
def driver_dashboard(req:Request):
    u=actor(req,'driver');today=service_today()
    with db() as c:
        assignment=one(c,'''SELECT f.*,b.label bus_label,b.plate,b.capacity,r.code route_code,r.name route_name
          FROM route_fleet f JOIN buses b ON b.id=f.bus_id JOIN routes r ON r.id=f.route_id WHERE f.driver_id=?''',(u['id'],))
        stops=all_rows(c,'SELECT * FROM stops WHERE route_id=? ORDER BY stop_order',(assignment['route_id'],)) if assignment else []
        students=all_rows(c,'''SELECT u.name,u.student_number,st.name pickup_stop FROM allocations a JOIN users u ON u.id=a.student_id JOIN stops st ON st.id=a.stop_id WHERE a.route_id=? AND a.active=1 ORDER BY u.student_number''',(assignment['route_id'],)) if assignment else []
        trips=all_rows(c,sched_q()+' WHERE s.driver_id=? AND s.service_date=? ORDER BY s.departure_time',(u['id'],today))
        for t in trips:t['last_location']=one(c,'SELECT lat,lon,accuracy,measured_at,received_at FROM locations WHERE schedule_id=? ORDER BY id DESC LIMIT 1',(t['id'],))
    return {'assignment':assignment,'stops':stops,'students':students,'trips':trips,'today':today,'driver':public_user(u)}

@app.get('/api/fees')
def fee_records(req:Request):
    actor(req,'admin')
    with db() as c:return all_rows(c,'''SELECT f.*,u.name student_name,u.student_number
        FROM fees f JOIN users u ON u.id=f.student_id ORDER BY f.id DESC LIMIT 500''')

@app.post('/api/fees')
async def create_fee(req:Request):
    u=actor(req,'admin');d=await req.json()
    sid=intval(d.get('student_id'),'Student',1);term=textval(d.get('term'),'Term',2,60)
    amount=intval(d.get('amount_paise'),'Fee',0,100000000);paid=intval(d.get('paid_paise',0),'Paid',0,amount)
    due=d.get('due_date') or None
    if due:validate_date(due)
    with db(True) as c:
        need(one(c,"SELECT id FROM users WHERE id=? AND role='student'",(sid,)),'Student not found',404)
        try:
            cur=c.execute('INSERT INTO fees(student_id,term,amount_paise,paid_paise,due_date,notes,demo,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (sid,term,amount,paid,due,str(d.get('notes',''))[:200],flag(d.get('demo',False)),now()))
        except sqlite3.IntegrityError:problem(409,'Fee record already exists for this student and term')
        audit(c,u['id'],'create','fee',cur.lastrowid)
    return {'ok':True,'id':cur.lastrowid}

@app.patch('/api/fees/{id}')
async def update_fee(id:int,req:Request):
    u=actor(req,'admin');d=await req.json()
    with db(True) as c:
        fee=one(c,'SELECT * FROM fees WHERE id=?',(id,));need(fee,'Fee not found',404)
        paid=intval(d.get('paid_paise'),'Paid amount',0,fee['amount_paise'])
        c.execute('UPDATE fees SET paid_paise=? WHERE id=?',(paid,id));audit(c,u['id'],'record_fee','fee',id)
    return {'ok':True}

def bootstrap_admin_from_environment():
    email=os.getenv('CAMPUSRIDE_BOOTSTRAP_EMAIL','').strip().lower()
    password=os.getenv('CAMPUSRIDE_BOOTSTRAP_PASSWORD','')
    name=os.getenv('CAMPUSRIDE_BOOTSTRAP_NAME','Transport Administrator').strip()
    if not email and not password: return
    if not email or not password or len(password)<16 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise RuntimeError('Bootstrap admin requires a valid email and a password of at least 16 characters')
    with db(True) as c:
        existing=one(c,"SELECT id FROM users WHERE role='admin' LIMIT 1")
        if existing:return
        c.execute('INSERT INTO users(name,email,role,password_hash,must_change_password,created_at) VALUES(?,?,?,?,0,?)',
                  (name,email,'admin',ph.hash(password),now()))
        audit(c,None,'bootstrap_admin','user')

init_db()
bootstrap_admin_from_environment()
