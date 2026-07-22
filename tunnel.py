"""
持久化 SSH 隧道 - 将本地 Flask 服务暴露到公网
使用 localhost.run 免费服务
"""
import subprocess
import re
import time
import sys
import signal
import os

SSH_EXE = os.path.expandvars(r"%SystemRoot%\System32\OpenSSH\ssh.exe")
if not os.path.exists(SSH_EXE):
    SSH_EXE = "ssh"

def start_tunnel():
    """启动持久 SSH 反向隧道"""
    cmd = [
        SSH_EXE,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-R", "80:localhost:5000",
        "nokey@localhost.run"
    ]
    
    print("正在建立公网隧道...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    url = None
    start_time = time.time()
    
    try:
        for line in proc.stdout:
            line = line.strip()
            # 提取 URL
            match = re.search(r'(https://[a-zA-Z0-9]+\.lhr\.life)', line)
            if match:
                url = match.group(1)
                print(f"\n{'='*56}")
                print(f"  公网访问地址: {url}")
                print(f"  手机扫码/浏览器输入此地址即可访问")
                print(f"  Ctrl+C 停止服务")
                print(f"{'='*56}\n")
                sys.stdout.flush()
                break
            
            # 输出其他信息
            if line and "authenticated" not in line and "follow" not in line:
                if "connection id" in line:
                    # skip the connection id line
                    pass
    except KeyboardInterrupt:
        pass
    
    return proc, url


if __name__ == "__main__":
    proc, url = start_tunnel()
    if proc:
        try:
            # 持续读取输出，保持连接存活
            for line in proc.stdout:
                pass
        except KeyboardInterrupt:
            print("\n正在关闭隧道...")
        finally:
            proc.terminate()
            proc.wait()
