#!/usr/bin/env python3
"""Misafir Windows'un Bluetooth bond'larının YAPISINI oku.

Kanal: qemu-guest-agent. Ajan misafirde `NT AUTHORITY\\SYSTEM` olarak koşar ve
`BTHPORT\\Parameters\\Keys` tam olarak SYSTEM'e açık bir anahtardır — yani
misafir KOŞARKEN okunur; disk rebind, BitLocker açma ve offline kovan
düzenleme gerekmez. Bu, aracın temel tasarım kararıdır: yaygın "dual boot"
reçeteleri diski host'tan mount edip `chntpw` kullanır, ve o yol devredilmiş
(vfio-pci'ye bağlı) bir diskte tanımı gereği çalışmaz.

GİZLİLİK: REG_BINARY değerlerin BAYTLARI BASILMAZ, yalnız uzunluğu yazılır.
Çıktı bir nota ya da hata kaydına güvenle yapıştırılabilir. Değer ADLARI cihaz
BD_ADDR'i olduğu için görünür; onlar anahtar materyali değildir.

Bu bir ÖLÇÜM aracıdır, senkron aracı değil: hiçbir şey yazmaz.

Kullanım:  ./guest-keys-dump.py [domain]        (varsayılan: win11-nvme)
"""

import base64
import json
import subprocess
import sys
import time

BTHPORT = r"HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys"
LIBVIRT_URI = "qemu:///system"
AGENT_TIMEOUT = 90

POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$root = 'HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys'

function Dump-Key($path, $label) {
  $k = Get-Item -LiteralPath $path
  'KEY ' + $label
  foreach ($n in $k.GetValueNames()) {
    $v = $k.GetValue($n)
    $t = $k.GetValueKind($n)
    if ($v -is [byte[]]) { '    {0} : {1} len={2}' -f $n, $t, $v.Length }
    else                 { '    {0} : {1} = {2}' -f $n, $t, $v }
  }
}

if (-not (Test-Path $root)) { 'YOK: ' + $root; exit 0 }

Dump-Key $root '<kok> Parameters\Keys'
Get-ChildItem -LiteralPath $root -Recurse | ForEach-Object {
  Dump-Key $_.PSPath $_.Name
}

'--- radyo ve yigin ---'
Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |
  Select-Object -First 20 Status, FriendlyName, InstanceId |
  Format-Table -AutoSize | Out-String -Width 200

'--- eslesmis cihazlar (PnP goruntusu) ---'
Get-PnpDevice -ErrorAction SilentlyContinue |
  Where-Object { $_.InstanceId -like 'BTHENUM*' -or $_.InstanceId -like 'BTHLE*' } |
  Select-Object Status, Class, FriendlyName |
  Format-Table -AutoSize | Out-String -Width 200
"""


def agent_command(domain, payload):
    """Ajana bir QMP komutu gönder, `return` gövdesini döndür."""
    proc = subprocess.run(
        ["virsh", "-c", LIBVIRT_URI, "qemu-agent-command", domain,
         json.dumps(payload)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"virsh rc={proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["return"]


def run_in_guest(domain, script):
    """PowerShell'i misafirde koştur, bitmesini bekle, durumunu döndür."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    pid = agent_command(domain, {
        "execute": "guest-exec",
        "arguments": {
            "path": "powershell.exe",
            "arg": ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            "capture-output": True,
        },
    })["pid"]

    for _ in range(AGENT_TIMEOUT):
        status = agent_command(domain, {
            "execute": "guest-exec-status", "arguments": {"pid": pid},
        })
        if status.get("exited"):
            return status
        time.sleep(1)
    sys.exit(f"ajan komutu {AGENT_TIMEOUT} s'de bitmedi (pid={pid})")


def main():
    domain = sys.argv[1] if len(sys.argv) > 1 else "win11-nvme"
    status = run_in_guest(domain, POWERSHELL)

    for stream, label in (("out-data", "stdout"), ("err-data", "stderr")):
        data = status.get(stream)
        if data:
            print(f"--- {label} ---")
            print(base64.b64decode(data).decode("utf-8", "replace"))

    # PowerShell stderr'e CLIXML ilerleme gürültüsü yazar; exitcode=0 ise o
    # gürültü hata değildir. Hükmü çıkış kodu taşır, stderr'in doluluğu değil.
    print(f"exitcode={status.get('exitcode')}")


if __name__ == "__main__":
    main()
