"""Pure, testable output contracts. No bpy dependency."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
import unicodedata

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {'CON','PRN','AUX','NUL'} | {'COM%d'%i for i in range(1,10)} | {'LPT%d'%i for i in range(1,10)}


def safe_component(value, fallback='Unnamed'):
    text = _INVALID.sub('_',unicodedata.normalize('NFC',str(value))).strip().rstrip('. ')
    if not text or text in {'.','..'}:
        text = fallback
    if text.split('.')[0].upper() in _RESERVED:
        text = '_' + text
    if len(text)>100 or len(text.encode('utf-8'))>180:
        prefix=text[:80].encode('utf-8')[:140].decode('utf-8',errors='ignore')
        text = prefix + '__' + hashlib.sha256(text.encode()).hexdigest()[:12]
    return text


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,
                          separators=(',',':'),allow_nan=False).encode()).hexdigest()


DEFAULT_FILENAME = '{icon} (Main {main} - Accent {accent})'
LEGACY_FILENAME = '{icon}__{main}__{accent}__{resolution}__{index}'


def readable_path(icon, main, accent, resolution, filename, extension, multiple_sizes):
    path=Path(safe_component(icon))/safe_component('Main ('+main+')')/safe_component('Accent ('+accent+')')
    if multiple_sizes:path=path/safe_component(resolution)
    return path/(safe_component(filename)+extension)


def file_digest(path):
    h = hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path,payload):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temp,'x',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():
            temp.unlink()


def append_record(path,payload):
    line=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n'
    path=Path(path)
    # Separate a prior truncated tail instead of concatenating the next valid record into it.
    prefix=''
    if path.exists() and path.stat().st_size:
        with open(path,'rb') as previous:
            previous.seek(-1,os.SEEK_END)
            if previous.read(1)!=b'\n': prefix='\n'
    with open(path,'a',encoding='utf-8') as stream:
        stream.write(prefix+line); stream.flush(); os.fsync(stream.fileno())


def read_manifest(path):
    rows=[]
    try:
        with open(path,'rb') as stream:
            for line in stream:
                try:
                    row=json.loads(line)
                    if isinstance(row,dict):
                        rows.append(row)
                except (ValueError,TypeError,UnicodeDecodeError):
                    # A process may have died halfway through the final line.
                    continue
    except FileNotFoundError:
        pass
    return rows


def reusable(row,signature,target):
    if not signature or row.get('signature')!=signature or row.get('status') not in {'DONE','VERIFIED'}:
        return False
    target=Path(target)
    try:
        if not target.is_file() or target.stat().st_size!=row.get('bytes'):
            return False
        return bool(row.get('sha256')) and file_digest(target)==row['sha256']
    except OSError:
        return False


def assert_unique_paths(paths):
    # Casefold is deliberately stricter than Linux, so the same job works on Windows/macOS.
    seen={}
    for idx,path in enumerate(paths):
        key=unicodedata.normalize('NFC',os.path.abspath(os.path.normpath(str(path)))).casefold()
        if key in seen:
            raise ValueError('Output collision between tasks {} and {}: {}. Add {{index}} to the filename.'.format(seen[key],idx,path))
        seen[key]=idx


class OutputLock:
    def __init__(self,folder):
        self.folder=Path(folder); self.path=self.folder/'.color_prime.lock'; self.token=uuid.uuid4().hex; self.owned=False

    def acquire(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(str(self.path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:
            raise RuntimeError('Output folder is locked by another/interrupted Color Prime job. Inspect .color_prime.lock before removing it.')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump({'token':self.token,'pid':os.getpid(),'created_unix':time.time()},stream)
                stream.flush(); os.fsync(stream.fileno())
            self.owned=True
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise
        return self

    def release(self):
        if not self.owned:
            return
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('token')==self.token:
                self.path.unlink()
        except (FileNotFoundError,ValueError,OSError):
            pass
        self.owned=False

    def __enter__(self):
        return self.acquire()

    def __exit__(self,*exc):
        self.release()


def effective_resolution(base_width,base_height,base_percent,mode,width,height,scale_percent):
    if mode=='ABSOLUTE':
        result=(int(width),int(height))
    else:
        factor=float(base_percent)/100. * float(scale_percent)/100.
        result=(max(1,round(base_width*factor)),max(1,round(base_height*factor)))
    if any(x<1 or x>65536 for x in result):
        raise ValueError('Resolution outside safe limits')
    return result


def paired_indices(main,accent,matrix=False):
    if not main or not accent:
        return []
    if matrix:
        return [(m,a) for m in main for a in accent]
    if len(main)==1:
        return [(main[0],a) for a in accent]
    if len(accent)==1:
        return [(m,accent[0]) for m in main]
    if len(main)!=len(accent):
        raise ValueError('Paired palettes need equal enabled lengths (or one color for broadcasting). No colors were silently dropped.')
    return list(zip(main,accent))
