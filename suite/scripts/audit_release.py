#!/usr/bin/env python3
"""Read-only public-image audit. Never print credential contents."""
import json,re,zipfile,hashlib
from pathlib import Path
SUITE=Path('/opt/h3-suite');issues=[]
for forbidden in ['auth.json','credentials.json','token.txt','.env','.npmrc','connection.json','login.dpapi','known-hosts.json']:
 for p in SUITE.rglob(forbidden):
  if p.is_file():issues.append({'path':str(p),'reason':'unexpected credential/config filename'})
for area in ['gateway','network','scripts','agent-templates','workflows']:
 for p in (SUITE/area).rglob('*'):
  if not p.is_file() or p.stat().st_size>2*1024**2:continue
  text=p.read_text(errors='ignore')
  if re.search(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{32,}',text):issues.append({'path':str(p),'reason':'credential pattern'})
for p in [Path('/root/.codex/auth.json'),Path('/root/.dsh'),Path('/root/.npmrc'),Path('/root/.netrc')]:
 if p.exists():issues.append({'path':str(p),'reason':'user credential directory/file exists; review before image save'})
package=Path('/工具包/H3控制端.zip')
with zipfile.ZipFile(package) as z:
 if z.testzip():issues.append({'path':str(package),'reason':'ZIP integrity failure'})
 if not {'H3Launcher.exe','H3Launcher.ico','browser-runtime/node.exe'}.issubset(z.namelist()):issues.append({'path':str(package),'reason':'launcher component missing'})
 for name in z.namelist():
  if Path(name).name in ['connection.json','login.dpapi','auth.json','token.txt']:issues.append({'path':name,'reason':'private launcher config bundled'})
manifest=json.loads((SUITE/'bootstrap/models.json').read_text())
for item in manifest['files']:
 if not re.fullmatch('[a-f0-9]{64}',item['sha256']):issues.append({'path':item['destination'],'reason':'missing model SHA256'})
print(json.dumps({'pass':not issues,'issues':issues,'model_files':len(manifest['files']),'model_bytes':sum(x['size'] for x in manifest['files']),'scope':'release program trees, generic config, launcher package; final host cleanup still required'},ensure_ascii=False,indent=2))
raise SystemExit(bool(issues))
