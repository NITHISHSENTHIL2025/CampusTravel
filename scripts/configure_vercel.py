"""Configure the Vercel same-origin API reverse proxy with your actual Render URL.
Usage: python scripts/configure_vercel.py https://your-api.onrender.com
Run before pushing the frontend to Vercel. No secret is written to the frontend.
"""
import json,sys
from pathlib import Path
from urllib.parse import urlsplit
if len(sys.argv)!=2: raise SystemExit('Usage: python scripts/configure_vercel.py https://YOUR_RENDER_HOST.onrender.com')
url=sys.argv[1].rstrip('/')
u=urlsplit(url)
if u.scheme!='https' or not u.netloc or u.path or u.query or u.username or u.password:
    raise SystemExit('Supply the HTTPS Render origin only, without path, credentials or query string.')
p=Path(__file__).resolve().parent.parent/'frontend'/'vercel.json'
data=json.loads(p.read_text(encoding='utf-8'))
data['rewrites'][0]['destination']=url+'/api/:path*'
p.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
print('Configured Vercel /api proxy to',url)
print('Next: push the repository, set Vercel Root Directory to frontend, Framework = Other.')
