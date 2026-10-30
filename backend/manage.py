"""Local administrator onboarding and GPS retention maintenance."""
import sys, getpass, re
from datetime import datetime,timezone,timedelta
from app.store import init_db,db,one,DB_PATH
from app.main import ph,now

def init_admin():
    init_db()
    with db() as c:
        if one(c,"SELECT id FROM users WHERE role='admin' LIMIT 1"):
            print('An administrator already exists. Create more admins from the dashboard.'); return
    name=input('Administrator full name: ').strip()
    email=input('Administrator email: ').strip().lower()
    if len(name)<2 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise SystemExit('Invalid name or email')
    pw=getpass.getpass('Password (at least 12 characters): ')
    confirm=getpass.getpass('Confirm password: ')
    if len(pw)<12 or pw!=confirm:raise SystemExit('Passwords differ or are too short')
    with db(True) as c:
        c.execute("INSERT INTO users(name,email,role,password_hash,must_change_password,created_at) VALUES(?,?,?,?,0,?)",(name,email,'admin',ph.hash(pw),now()))
    print('Administrator created in configured database. Never expose your secrets.')

def cleanup():
    days=30
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    with db(True) as c:
        cur=c.execute('DELETE FROM locations WHERE received_at<?',(cutoff,))
        c.execute('DELETE FROM sessions WHERE expires_at<?',(now(),))
    print(f'Deleted {cur.rowcount} GPS records older than {days} days.')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'help'
    if cmd=='init-admin':init_admin()
    elif cmd=='cleanup':cleanup()
    elif cmd=='seed-demo':
        from seed_demo import create
        create()
    else:print('Usage: python manage.py init-admin | seed-demo | cleanup')
