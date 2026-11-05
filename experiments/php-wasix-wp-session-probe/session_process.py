"""Own one private session process and bounded, retained regular-file captures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'target/php-wasix-wp-session-probe/session-runs'
LINE_LIMIT=4*1024*1024
STDOUT_LIMIT=16*1024*1024
STDERR_LIMIT=65536
_OWNED_SESSIONS={}


def require(value,code):
    if not value: raise ValueError(code)


def identity(wire): return {'bytes':len(wire),'sha256':hashlib.sha256(wire).hexdigest()}
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def file_identity(path):
    hasher=hashlib.sha256();size=0
    with path.open('rb') as stream:
        while chunk:=stream.read(65536): size+=len(chunk);hasher.update(chunk)
    return {'bytes':size,'sha256':hasher.hexdigest()}


def parse(wire):
    def pairs(rows):
        result={}
        for key,value in rows:
            require(key not in result,'duplicate_session_json'); result[key]=value
        return result
    def bad(_): raise ValueError('nonfinite_session_json')
    return json.loads(wire,object_pairs_hook=pairs,parse_constant=bad)


class Session:
    def __init__(self,binary,environment,report,label):
        require(binary.resolve(strict=True)==binary and binary.is_file(),'fixed_session_binary')
        require(WORK.resolve()==WORK,'fixed_session_work'); WORK.mkdir(parents=True,exist_ok=True)
        self.directory=WORK/('session-'+uuid.uuid4().hex); self.directory.mkdir(mode=0o700)
        require(self.directory.resolve()==self.directory,'owned_session_directory')
        self.stdout=self.directory/'stdout.jsonl';self.stderr=self.directory/'stderr.bin'
        self.out=self.stdout.open('xb');self.err=self.stderr.open('xb')
        self.stdout.chmod(0o600);self.stderr.chmod(0o600)
        self.child=None;self.offset=0;self.finished=False;self.frames=0
        self.force_cleanup_required=False;self.in_request=False
        self.row={'owned_reaped':False,'forced_cleanup':False,'capture_kind':'owned_regular_files',
            'run':self.directory.relative_to(ROOT).as_posix(),'run_retained':True,
            'parent_input_closed':False,'captured_output_complete_after_reap':False,'events':[]}
        report.setdefault('session_executions',{})[label]=self.row
        try:
            self.child=subprocess.Popen([str(binary)],cwd=ROOT,env=environment,stdin=subprocess.PIPE,
                stdout=self.out,stderr=self.err,bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.row['owned_pid']=self.child.pid
            _OWNED_SESSIONS[id(self)]=self
        except BaseException:
            self.abort();raise

    def check(self):
        if self.stdout.stat().st_size>STDOUT_LIMIT or self.stderr.stat().st_size>STDERR_LIMIT:
            self.force_cleanup_required=True
            raise ValueError('session_live_capture_limit')

    def read_line(self,timeout):
        deadline=time.monotonic()+timeout
        while True:
            self.check()
            with self.stdout.open('rb') as stream:
                stream.seek(self.offset);wire=stream.readline(LINE_LIMIT+1)
            if len(wire)>LINE_LIMIT:
                self.force_cleanup_required=True;raise ValueError('session_line_limit')
            if wire.endswith(b'\n'):
                require(wire.count(b'\n')==1,'session_single_line')
                self.offset+=len(wire)
                return parse(wire),identity(wire)
            if time.monotonic()>=deadline:
                self.force_cleanup_required=True;raise ValueError('session_output_deadline_or_exit')
            require(self.child.poll() is None,'session_output_deadline_or_exit')
            time.sleep(0.01)

    def ready(self):
        value,metric=self.read_line(300)
        require(type(value) is dict and set(value)=={'schema','module_compilations_total','module_sha256','compiled_module_ready'}
            and value['schema']=='recastpress.php-wasix-wp-session-ready.v0.5'
            and type(value['module_compilations_total']) is int and value['module_compilations_total']==1
            and value['compiled_module_ready'] is True and type(value['module_sha256']) is str
            and len(value['module_sha256'])==64 and all(c in '0123456789abcdef' for c in value['module_sha256']),
            'fixed_compiled_session_ready')
        self.row['ready_line']=metric
        return value

    def send(self,value):
        wire=canonical(value)+b'\n'
        require(0<len(wire)<=24*1024 and wire.count(b'\n')==1,'bounded_session_input_line')
        require(self.child.stdin.write(wire)==len(wire),'session_input_delivery')
        self.child.stdin.flush()
        return identity(wire)

    def request(self,seq,payload):
        require(type(seq) is int and seq==self.frames+1 and 1<=seq<=3,'ordered_parent_session_request')
        self.in_request=True
        sent=self.send({'schema':'recastpress.php-wasix-wp-session-input.v0.5','seq':seq,'request':payload})
        value,metric=self.read_line(300)
        require(type(value) is dict and set(value)=={'schema','seq','request'}
            and value['schema']=='recastpress.php-wasix-wp-session-response.v0.5'
            and type(value['seq']) is int and value['seq']==seq and type(value['request']) is dict,'fixed_session_response')
        self.row['events'].append({'seq':seq,'input':sent,'acknowledgement':metric})
        self.frames+=1;self.in_request=False
        return value['request']

    def close_input(self):
        if self.child is not None and self.child.stdin is not None:
            self.child.stdin.close();self.child.stdin=None
        self.row['parent_input_closed']=True

    def finish(self,expected_exit=0,timeout=30):
        require(type(timeout) in (int,float) and 0<timeout<=30,'bounded_session_final_timeout')
        deadline=time.monotonic()+timeout
        self.close_input()
        value,metric=self.read_line(timeout)
        try:self.child.wait(timeout=max(0,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            self.force_cleanup_required=True;raise
        self.check()
        _OWNED_SESSIONS.pop(id(self),None)
        require(self.child.returncode==expected_exit,'session_expected_exit')
        self.row.update(owned_reaped=True,exit_code=self.child.returncode,final_line=metric)
        self.out.close();self.err.close()
        require(self.stdout.stat().st_size==self.offset and self.stderr.stat().st_size==0,'complete_clean_session_capture')
        self.row.update(stdout=file_identity(self.stdout),stderr=identity(b''),captured_output_complete_after_reap=True)
        self.finished=True
        return value

    def abort(self):
        self.close_input()
        if self.child is not None:
            if (self.force_cleanup_required or self.in_request) and self.child.poll() is None:
                self.row['forced_cleanup']=True;self.child.kill();self.child.wait(timeout=5)
            else:
                try:self.child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.row['forced_cleanup']=True;self.child.kill();self.child.wait(timeout=5)
            self.row.update(owned_reaped=True,exit_code=self.child.returncode)
            _OWNED_SESSIONS.pop(id(self),None)
        self.out.close();self.err.close();self.finished=True
        self.row['descendant_cleanup_verified']=False
        if self.stdout.exists() and self.stderr.exists():
            self.row.update(stdout=file_identity(self.stdout),stderr=file_identity(self.stderr))

    def __enter__(self):return self
    def __exit__(self,kind,value,trace):
        if not self.finished:self.abort()


def reap_owned_sessions():
    failed=False
    for session in list(_OWNED_SESSIONS.values()):
        try:session.abort()
        except BaseException:failed=True
    require(not failed and not _OWNED_SESSIONS,'owned_session_cleanup_unconfirmed')
