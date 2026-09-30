#!/usr/bin/env python3
"""list_c_files.py <root> [--max 2000] [--maxdirs 6000] [--depth 9] — recursive .c enumerator via the
LISTDIR control verb (native PBGetCatInfo, no ToolServer). Safe: one dir per round-trip. Prints TSV path\tsize."""
import socket, sys, time
def listdir(path, timeout=15):
    s=socket.create_connection(("127.0.0.1",9001),timeout=timeout)
    s.sendall(("LISTDIR:"+path+"\n\n").encode("mac_roman","replace")); b=b""
    while True:
        try: c=s.recv(65536)
        except socket.timeout: break
        if not c: break
        b+=c
        if b.endswith(b"\r\r") or b.endswith(b"\r"): break
    s.close(); t=b.decode("mac_roman","replace")
    i=t.find("STDOUT:")
    if i<0: return []
    j=t.find("\r",i) if "\r" in t[i:] else t.find("\n",i)
    body=t[t.find("\n",i)+1:] if "\n" in t[i:i+20] else t[t.index(str(),i):]
    # robust: split after the STDOUT:<n>\n header
    import re
    m=re.search(r"STDOUT:(\d+)\r?\n?",t)
    if not m: return []
    rows=t[m.end():].split("\n")
    out=[]
    for r in rows:
        r=r.rstrip("\r")
        if not r or r.startswith("STDERR"): continue
        p=r.split("\t")
        if len(p)>=4: out.append((p[0],p[1],p[3]))
    return out
root=sys.argv[1]
MAX=int(sys.argv[sys.argv.index("--max")+1]) if "--max" in sys.argv else 2000
MAXDIRS=int(sys.argv[sys.argv.index("--maxdirs")+1]) if "--maxdirs" in sys.argv else 6000
DEPTH=int(sys.argv[sys.argv.index("--depth")+1]) if "--depth" in sys.argv else 9
t0=time.time(); stack=[(root if root.endswith(":") else root+":",0)]; nd=0; nc=0
while stack and nc<MAX and nd<MAXDIRS and time.time()-t0<1200:
    path,d=stack.pop(); nd+=1
    try: entries=listdir(path)
    except Exception: continue
    for name,typ,size in entries:
        if name in (".",".."): continue
        full=path+name
        isfile = typ not in ("fldr","") or "." in name and typ not in ("fldr",)
        if typ=="fldr" or (typ=="" and not name.lower().endswith((".c",".h",".cp",".cpp"))):
            if d<DEPTH: stack.append((full+":",d+1))
        elif name.lower().endswith(".c"):
            try: sz=int(size)
            except: sz=0
            print(f"{full}\t{sz}",flush=True); nc+=1
    if nd%50==0: print(f"# dirs={nd} c={nc} {time.time()-t0:.0f}s stack={len(stack)}",file=sys.stderr,flush=True)
print(f"# DONE dirs={nd} c_files={nc} {time.time()-t0:.0f}s",file=sys.stderr,flush=True)
