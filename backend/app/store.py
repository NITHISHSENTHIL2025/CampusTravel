import os, sqlite3, threading, re
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv('CAMPUSRIDE_DB', str(ROOT/'data'/'campusride.sqlite3'))).expanduser().resolve()
_LOCAL = threading.local()

DDL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 role TEXT NOT NULL CHECK(role IN ('admin','driver','student')), password_hash TEXT NOT NULL,
 student_number TEXT UNIQUE, phone TEXT, active INTEGER NOT NULL DEFAULT 1,
 must_change_password INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS buses (id INTEGER PRIMARY KEY, plate TEXT NOT NULL UNIQUE COLLATE NOCASE,
 label TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 120), active INTEGER NOT NULL DEFAULT 1,
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routes (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stops (id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
 name TEXT NOT NULL, stop_order INTEGER NOT NULL CHECK(stop_order>0),
 lat REAL NOT NULL CHECK(lat BETWEEN -90 AND 90), lon REAL NOT NULL CHECK(lon BETWEEN -180 AND 180),
 pickup_time TEXT, UNIQUE(route_id,stop_order), UNIQUE(route_id,name));
CREATE TABLE IF NOT EXISTS allocations (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id),
 route_id INTEGER NOT NULL REFERENCES routes(id), stop_id INTEGER NOT NULL REFERENCES stops(id),
 pass_code TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, ended_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_allocation ON allocations(student_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL REFERENCES routes(id),
 bus_id INTEGER NOT NULL REFERENCES buses(id), driver_id INTEGER NOT NULL REFERENCES users(id),
 service_date TEXT NOT NULL, departure_time TEXT NOT NULL, duration_minutes INTEGER NOT NULL DEFAULT 120 CHECK(duration_minutes BETWEEN 10 AND 600),
 status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','active','completed','cancelled')),
 is_test INTEGER NOT NULL DEFAULT 0 CHECK(is_test IN (0,1)), created_at TEXT NOT NULL,
 started_at TEXT, ended_at TEXT, cancelled_at TEXT);
CREATE INDEX IF NOT EXISTS idx_schedules_date ON schedules(service_date,status);
CREATE TABLE IF NOT EXISTS locations (id INTEGER PRIMARY KEY, schedule_id INTEGER NOT NULL REFERENCES schedules(id),
 lat REAL NOT NULL, lon REAL NOT NULL, accuracy REAL, speed REAL, heading REAL,
 measured_at TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_locations_trip ON locations(schedule_id,id DESC);
CREATE TABLE IF NOT EXISTS boardings (id INTEGER PRIMARY KEY, schedule_id INTEGER NOT NULL REFERENCES schedules(id),
 allocation_id INTEGER NOT NULL REFERENCES allocations(id), student_id INTEGER NOT NULL REFERENCES users(id),
 stop_id INTEGER NOT NULL REFERENCES stops(id), recorded_at TEXT NOT NULL, recorded_by INTEGER NOT NULL REFERENCES users(id),
 UNIQUE(schedule_id, student_id));
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY, schedule_id INTEGER NOT NULL REFERENCES schedules(id),
 category TEXT NOT NULL CHECK(category IN ('delay','breakdown','emergency','traffic','other')),
 description TEXT NOT NULL, reported_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 resolved_at TEXT, resolved_by INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS notices (id INTEGER PRIMARY KEY, route_id INTEGER REFERENCES routes(id),
 title TEXT NOT NULL, body TEXT NOT NULL, author_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id),
 schedule_id INTEGER NOT NULL REFERENCES schedules(id), stop_id INTEGER NOT NULL REFERENCES stops(id),
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 created_at TEXT NOT NULL, decided_at TEXT, decided_by INTEGER REFERENCES users(id),
 UNIQUE(student_id,schedule_id));
CREATE TABLE IF NOT EXISTS route_fleet (
 route_id INTEGER PRIMARY KEY REFERENCES routes(id),
 bus_id INTEGER NOT NULL UNIQUE REFERENCES buses(id),
 driver_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
 assigned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fees (
 id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id),
 term TEXT NOT NULL, amount_paise INTEGER NOT NULL CHECK(amount_paise>=0),
 paid_paise INTEGER NOT NULL DEFAULT 0 CHECK(paid_paise>=0),
 due_date TEXT, notes TEXT NOT NULL DEFAULT '', demo INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, UNIQUE(student_id,term), CHECK(paid_paise<=amount_paise)
);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
 entity TEXT NOT NULL, entity_id INTEGER, created_at TEXT NOT NULL, metadata TEXT);
'''

# Cloud backend uses Neon PostgreSQL when DATABASE_URL is provided. SQLite remains
# available only for disposable local tests. No production deployment uses SQLite.
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
PG_TABLES_WITH_ID = frozenset({
    'users','buses','routes','stops','allocations','schedules','locations',
    'boardings','incidents','notices','requests','fees','audit'
})

def postgres_ddl():
    """Convert the portable base schema to native PostgreSQL DDL."""
    ddl = DDL.replace('PRAGMA foreign_keys=ON;', '')
    ddl = ddl.replace('COLLATE NOCASE', '')
    ddl = ddl.replace('id INTEGER PRIMARY KEY AUTOINCREMENT',
                      'id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY')
    ddl = re.sub(r'\bid INTEGER PRIMARY KEY\b',
                 'id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY', ddl)
    ddl = re.sub(r'\b([a-z_]+_id) INTEGER\b', r'\1 BIGINT', ddl)
    ddl = re.sub(r'\b([a-z_]+) INTEGER(?=(?: NOT NULL)? REFERENCES\b)', r'\1 BIGINT', ddl)
    ddl += """
    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS shift TEXT NOT NULL DEFAULT 'morning';
    CREATE UNIQUE INDEX IF NOT EXISTS users_email_casefold_uq ON users(lower(email));
    CREATE UNIQUE INDEX IF NOT EXISTS buses_plate_casefold_uq ON buses(lower(plate));
    CREATE UNIQUE INDEX IF NOT EXISTS routes_code_casefold_uq ON routes(lower(code));
    """
    return ddl

class PgCursor:
    """Minimal SQLite-compatible cursor for the existing audited service logic."""
    def __init__(self, cursor, lastrowid=None):
        self.cursor=cursor
        self.lastrowid=lastrowid
        self.rowcount=cursor.rowcount
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()
    def __iter__(self): return iter(self.cursor)

class PgConnection:
    def __init__(self, raw): self.raw=raw
    def execute(self, sql, params=()):
        import psycopg
        query=sql.replace('?', '%s')
        returning=False
        match=re.match(r'\s*INSERT\s+INTO\s+([a-z_][a-z_0-9]*)\b', query, re.I)
        if match and match.group(1).lower() in PG_TABLES_WITH_ID and not re.search(r'\bRETURNING\b', query, re.I):
            query=query.rstrip().rstrip(';')+' RETURNING id'
            returning=True
        try:
            cursor=self.raw.execute(query, params)
        except psycopg.IntegrityError as e:
            # Existing API converts this common error to a clean HTTP 409.
            raise sqlite3.IntegrityError(str(e)) from e
        inserted_id=cursor.fetchone()['id'] if returning else None
        return PgCursor(cursor,inserted_id)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()

def connect():
    url=os.getenv('DATABASE_URL', '').strip()
    if url:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('PostgreSQL driver missing: pip install "psycopg[binary]"') from exc
        return PgConnection(psycopg.connect(url, row_factory=dict_row,
                     connect_timeout=15, prepare_threshold=None))
    if os.getenv('CAMPUSRIDE_REQUIRE_POSTGRES') == '1':
        raise RuntimeError('DATABASE_URL must be configured; refusing ephemeral SQLite in cloud deployment.')
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=15000')
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init_db():
    c=connect()
    if isinstance(c,PgConnection):
        try:
            for statement in postgres_ddl().split(';'):
                if statement.strip(): c.execute(statement)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    else:
        try:
            c.executescript(DDL)
            cols=[x[1] for x in c.execute('PRAGMA table_info(schedules)').fetchall()]
            if 'shift' not in cols:
                c.execute("ALTER TABLE schedules ADD COLUMN shift TEXT NOT NULL DEFAULT 'morning'")
        finally:
            c.close()

@contextmanager
def db(write=False):
    c = connect()
    try:
        if not isinstance(c,PgConnection):
            c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def one(c, sql, args=()):
    r=c.execute(sql,args).fetchone()
    return dict(r) if r else None

def all_rows(c, sql, args=()):
    return [dict(x) for x in c.execute(sql,args).fetchall()]

def audit(c, actor, action, entity, entity_id=None, meta=None):
    from datetime import datetime, timezone
    import json
    c.execute('INSERT INTO audit(actor_id,action,entity,entity_id,created_at,metadata) VALUES(?,?,?,?,?,?)',
       (actor,action,entity,entity_id,datetime.now(timezone.utc).isoformat(),json.dumps(meta) if meta is not None else None))
