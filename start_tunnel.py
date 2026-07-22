import subprocess, re, time, sys, os, signal

SSH = os.path.expandvars(r"%SystemRoot%\System32\OpenSSH\ssh.exe")
if not os.path.exists(SSH):
    SSH = "ssh"

print("Starting tunnel...", flush=True)
proc = subprocess.Popen(
    [SSH, "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
     "-o", "ExitOnForwardFailure=yes", "-R", "80:localhost:5000", "nokey@localhost.run"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

url = None
try:
    for line in proc.stdout:
        line = line.strip()
        m = re.search(r'(https://[a-zA-Z0-9]+\.lhr\.life)', line)
        if m:
            url = m.group(1)
            print(f"PUBLIC_URL:{url}", flush=True)
            break
        if "connection id" not in line and line:
            print(line, flush=True)
except KeyboardInterrupt:
    pass

if url:
    print(f"Tunnel active at {url}", flush=True)
    # Keep reading to keep alive
    try:
        for line in proc.stdout:
            pass
    except:
        pass
else:
    print("FAILED to get URL", flush=True)
    proc.terminate()
