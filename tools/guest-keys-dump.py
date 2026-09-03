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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentexec  # noqa: E402
from agentexec import run_powershell  # noqa: E402

BTHPORT = r"HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys"

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


def main():
    domain = sys.argv[1] if len(sys.argv) > 1 else agentexec.DEFAULT_DOMAIN
    exitcode, stdout, stderr = run_powershell(domain, POWERSHELL)

    for label, data in (("stdout", stdout), ("stderr", stderr)):
        if data:
            print(f"--- {label} ---")
            print(data)

    # PowerShell stderr'e CLIXML ilerleme gürültüsü yazar; exitcode=0 ise o
    # gürültü hata değildir. Hükmü çıkış kodu taşır, stderr'in doluluğu değil.
    print(f"exitcode={exitcode}")


if __name__ == "__main__":
    # Ajan hatasını mesaja çeviren yer → `win-to-bluez.py`deki aynı yorum.
    try:
        main()
    except agentexec.AgentError as exc:
        sys.exit(str(exc))
