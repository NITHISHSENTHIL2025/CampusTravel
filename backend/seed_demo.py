"""Opt-in, explicitly fictional dataset. Run only after admin onboarding.
Generates unique temporary credentials locally; never impersonates KLH users.
"""
from pathlib import Path
import secrets,datetime
from zoneinfo import ZoneInfo
from app.store import db,one,all_rows,ROOT
from app.main import ph,now

CAMPUS=(17.54661,78.404776)
# Nearby points are for demonstration of sequence only, not verified bus pickup points or roads.
ROUTES=[
 ('DEMO-R01','Northern practice circuit',[(17.5530,78.4040,'Sample Pickup A'),(17.5594,78.3959,'Sample Pickup B'),(17.5654,78.4030,'Sample Pickup C')]),
 ('DEMO-R02','Southern practice circuit',[(17.5403,78.3975,'Sample Pickup D'),(17.5329,78.3912,'Sample Pickup E'),(17.5283,78.4033,'Sample Pickup F')])]

def create():
    with db() as c:
        if one(c,'SELECT id FROM buses LIMIT 1') or one(c,'SELECT id FROM routes LIMIT 1') or one(c,"SELECT id FROM users WHERE role!='admin' LIMIT 1"):
            raise SystemExit('Demo seed refused: existing operational data found. Use a new empty database; no records were overwritten.')
        if not one(c,"SELECT id FROM users WHERE role='admin' LIMIT 1"):
            raise SystemExit('Create your real administrator account with RUN_WINDOWS.bat first.')
    credentials=[]
    current_date=datetime.datetime.now(ZoneInfo('Asia/Kolkata')).date()
    demo_days=[(current_date+datetime.timedelta(days=offset)).isoformat() for offset in (0,1)]
    with db(True) as c:
        for i,(code,rname,points) in enumerate(ROUTES,1):
            route=c.execute('INSERT INTO routes(code,name,description,created_at) VALUES(?,?,?,?)',(code,rname,'SYNTHETIC TEST ROUTE; no university transport route is implied.',now())).lastrowid
            ordered=[('KLH campus AREA · illustrative start',*CAMPUS)]+[(n,lat,lon) for lat,lon,n in points]+[('KLH campus AREA · illustrative return',*CAMPUS)]
            ids=[]
            for n,(name,lat,lon) in enumerate(ordered,1):
                ids.append(c.execute('INSERT INTO stops(route_id,name,stop_order,lat,lon,pickup_time) VALUES(?,?,?,?,?,?)',(route,name,n,lat,lon,None)).lastrowid)
            bus=c.execute('INSERT INTO buses(plate,label,capacity,notes,created_at) VALUES(?,?,?,?,?)',
               (f'DEMO-VEH-{i:02}',f'DEMO BUS {i:02}',36,'Fictional bus; NOT an actual KLH vehicle registration',now())).lastrowid
            dp=secrets.token_urlsafe(16)+'!A9'
            email=f'driver{i:02}@demo.campusride.test'
            driver=c.execute('INSERT INTO users(name,email,role,password_hash,phone,created_at,must_change_password) VALUES(?,?,?,?,?,?,1)',
             (f'Demo Driver {i:02}',email,'driver',ph.hash(dp),None,now())).lastrowid
            credentials.append((f'DRIVER {i}',email,dp))
            c.execute('INSERT INTO route_fleet(route_id,bus_id,driver_id,assigned_at) VALUES(?,?,?,?)',(route,bus,driver,now()))
            for s in range(1,13):
                seq=(i-1)*12+s
                num=f'DEMO26{seq:04d}'
                email=f'{num.lower()}@demo.campusride.test'
                pw=secrets.token_urlsafe(16)+'!A9'
                student=c.execute('INSERT INTO users(name,email,role,password_hash,student_number,created_at,must_change_password) VALUES(?,?,?,?,?,?,1)',
                 (f'Demo Student {seq:02}',email,'student',ph.hash(pw),num,now())).lastrowid
                credentials.append((f'STUDENT {seq:02}',email,pw))
                c.execute('INSERT INTO allocations(student_id,route_id,stop_id,pass_code,created_at) VALUES(?,?,?,?,?)',
                  (student,route,ids[1+(s-1)%len(points)],secrets.token_urlsafe(22).replace('-','').replace('_','')[:25].upper(),now()))
                c.execute('INSERT INTO fees(student_id,term,amount_paise,paid_paise,due_date,notes,demo,created_at) VALUES(?,?,?,?,?,?,1,?)',
                 (student,'DEMO transport term',450000,0,None,'Illustrative amount only, not a university fee or payable invoice',now()))
            for day in demo_days:
                for shift,time in [('morning',f'07:{10+i*5:02}'),('evening',f'16:{10+i*5:02}')]:
                    c.execute('INSERT INTO schedules(route_id,bus_id,driver_id,service_date,departure_time,duration_minutes,is_test,created_at,shift) VALUES(?,?,?,?,?,?,1,?,?)',
                     (route,bus,driver,day,time,145,now(),shift))
    f=ROOT/'DEMO_CREDENTIALS_PRIVATE.txt'
    with f.open('w',encoding='utf-8') as file:
        file.write('CAMPUSRIDE FICTIONAL DEMO ACCOUNTS — TEST ENVIRONMENT ONLY\nNever deploy these accounts in a genuine university database.\nAdmin: the account YOU created during setup.\n\n')
        for name,email,pw in credentials:file.write(f'{name}\t{email}\t{pw}\n')
    try:f.chmod(0o600)
    except OSError:pass
    print('\nDemo loaded: 2 fictitious buses, 2 sample routes, 2 test drivers, 24 fictional students, 8 TEST trips (today and tomorrow) and sample fee records.')
    print(f'Driver/student credentials (private file): {f}')
    print('The campus location is a reference; all remaining stop coordinates are illustrative.')

if __name__=='__main__':create()
