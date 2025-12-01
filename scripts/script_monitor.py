import os, time, json, subprocess, shlex
import psycopg2

def get_db_conn():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST','127.0.0.1'),
        port=int(os.environ.get('DB_PORT','5432')),
        user=os.environ.get('DB_USER','openEuler'),
        password=os.environ.get('DB_PASSWORD','openEuler@pwd'),
        dbname=os.environ.get('DB_NAME','postgres')
    )

BASE=os.environ.get('LAB_DIR', os.path.join(os.path.dirname(__file__), '..'))
TMP=os.path.join(BASE, 'runtime')
os.makedirs(TMP, exist_ok=True)

procs={}

def fetch_pending(cur):
    cur.execute("SELECT id, script_id, cmd FROM script_commands WHERE status IS NULL OR status='pending' ORDER BY id ASC LIMIT 20")
    return cur.fetchall()

def mark_command(cur, cid, status, note=None):
    cur.execute("UPDATE script_commands SET status=%s, processed_at=NOW(), note=%s WHERE id=%s", (status, note, cid))

def get_script(cur, sid):
    cur.execute("SELECT name, lang, content FROM scripts WHERE id=%s", (sid,))
    return cur.fetchone()

def insert_log(cur, sid, status, pid=None):
    cur.execute("INSERT INTO script_exec_log(script_id,status,pid,started_at) VALUES(%s,%s,%s,NOW()) RETURNING id", (sid, status, pid))
    return cur.fetchone()[0]

def finish_log(cur, lid, status, output):
    cur.execute("UPDATE script_exec_log SET status=%s, output=%s, finished_at=NOW() WHERE id=%s", (status, output, lid))

def latest_running(cur, sid):
    cur.execute("SELECT id, pid FROM script_exec_log WHERE script_id=%s AND status='running' ORDER BY id DESC LIMIT 1", (sid,))
    return cur.fetchone()

def launch_process(lang, name, content):
    if str(lang).lower() == 'py':
        path=os.path.join(TMP, f"{name}.py")
        with open(path,'w',encoding='utf-8') as f:
            f.write(content or '')
        cmd=f"python3 {shlex.quote(path)}"
        return subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    path=os.path.join(TMP, f"{name}.c")
    with open(path,'w',encoding='utf-8') as f:
        f.write(content or '')
    binp=os.path.join(TMP, f"{name}")
    cc=subprocess.run(['gcc', path, '-o', binp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if cc.returncode != 0:
        return None, cc.stdout.decode('utf-8', errors='ignore')
    cmd=binp
    return subprocess.Popen([cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT), None

def drain_output(p):
    out=b''
    if p.stdout:
        out=p.stdout.read()
    return out.decode('utf-8', errors='ignore')

def main():
    while True:
        conn=None
        try:
            conn=get_db_conn()
            cur=conn.cursor()
            for cid, sid, cmd in fetch_pending(cur):
                mark_command(cur, cid, 'processing')
                row=get_script(cur, sid)
                if not row:
                    mark_command(cur, cid, 'failed', 'script missing')
                    continue
                name, lang, content=row
                if cmd=='run':
                    ret=launch_process(lang, name, content)
                    if isinstance(ret, tuple):
                        proc, errmsg=ret
                    else:
                        proc=ret
                        errmsg=None
                    if not proc:
                        lid=insert_log(cur, sid, 'failed', None)
                        finish_log(cur, lid, 'failed', errmsg or 'compile failed')
                        mark_command(cur, cid, 'done', 'compile failed')
                    else:
                        lid=insert_log(cur, sid, 'running', proc.pid)
                        procs[(sid,lid)]=proc
                        conn.commit()
                elif cmd=='stop':
                    lr=latest_running(cur, sid)
                    if not lr:
                        mark_command(cur, cid, 'done', 'no running')
                    else:
                        lid, pid=lr
                        try:
                            os.kill(pid, 9)
                        except Exception:
                            pass
                        finish_log(cur, lid, 'failed', 'stopped')
                        mark_command(cur, cid, 'done', 'stopped')
                conn.commit()
            keys=list(procs.keys())
            for key in keys:
                sid,lid=key
                p=procs[key]
                if p.poll() is not None:
                    out=drain_output(p)
                    cur=conn.cursor()
                    finish_log(cur, lid, 'success' if p.returncode==0 else 'failed', out)
                    conn.commit()
                    procs.pop(key, None)
        except Exception as e:
            pass
        finally:
            if conn:
                conn.close()
        time.sleep(1)

if __name__=='__main__':
    main()