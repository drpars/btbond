"""Misafirde PowerShell koşturmak için `qemu-guest-agent` kanalı.

Kanal `virsh qemu-agent-command` → `guest-exec`. Ajan misafirde
`NT AUTHORITY\\SYSTEM` olarak koştuğu için `BTHPORT\\Parameters` misafir
KOŞARKEN okunup yazılabilir: disk rebind, BitLocker açma ve offline kovan
düzenleme gerekmez. Gerekçe → README, "Neden bu şekilde: kanal seçimi".

Bu modül anahtar materyalini yorumlamaz; yalnız komutu taşır.
"""

import base64
import json
import subprocess
import sys
import time

LIBVIRT_URI = "qemu:///system"
AGENT_TIMEOUT = 90


def agent_command(domain, payload, uri=LIBVIRT_URI):
    """Ajana bir QMP komutu gönder, `return` gövdesini döndür."""
    proc = subprocess.run(
        ["virsh", "-c", uri, "qemu-agent-command", domain, json.dumps(payload)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"virsh rc={proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["return"]


def run_powershell(domain, script, timeout=AGENT_TIMEOUT, uri=LIBVIRT_URI):
    """PowerShell'i misafirde koştur; (exitcode, stdout, stderr) döndür.

    Betik `-EncodedCommand` ile gider: UTF-16LE + base64, yani tırnak,
    ters bölü ve satır sonu kabuk ayrıştırmasına hiç uğramaz.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    pid = agent_command(domain, {
        "execute": "guest-exec",
        "arguments": {
            "path": "powershell.exe",
            "arg": ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            "capture-output": True,
        },
    }, uri)["pid"]

    for _ in range(timeout):
        status = agent_command(domain, {
            "execute": "guest-exec-status", "arguments": {"pid": pid},
        }, uri)
        if status.get("exited"):
            break
        time.sleep(1)
    else:
        sys.exit(f"ajan komutu {timeout} s'de bitmedi (pid={pid})")

    def decode(field):
        data = status.get(field)
        return base64.b64decode(data).decode("utf-8", "replace") if data else ""

    # PowerShell stderr'e CLIXML ilerleme gürültüsü yazar; hükmü çıkış kodu
    # taşır, stderr'in doluluğu değil.
    return status.get("exitcode"), decode("out-data"), decode("err-data")
