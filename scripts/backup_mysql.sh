#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
OUT_DIR="${1:-./data_backups}"
mkdir -p "$OUT_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
python3 -c "
import json, subprocess, sys
from pathlib import Path
cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
db = cfg.get('Database', {})
if str(db.get('engine','')).lower() not in ('mysql','mariadb'):
    print('非 MySQL，跳过'); sys.exit(0)
out = Path('$OUT_DIR') / f\"mysql_{db.get('database','db')}_${STAMP}.sql.gz\"
cmd = ['mysqldump','-h'+str(db.get('host','127.0.0.1')), '-P'+str(db.get('port',3306)),
       '-u'+str(db.get('user','')), '-p'+str(db.get('password','')),
       '--single-transaction', str(db.get('database',''))]
print('备份', out)
with open(out,'wb') as f:
    p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(['gzip'], stdin=p1.stdout, stdout=f)
    p1.stdout.close()
    if p1.wait() != 0 or p2.wait() != 0:
        sys.exit('mysqldump 失败')
print('完成')
"
