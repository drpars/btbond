#!/usr/bin/env python3
"""Misafir Windows'un bond'larını host BlueZ'e replike et (Windows → Linux).

Kaynak `HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters`,
kanal `qemu-guest-agent` (→ `agentexec`). Hedef
`/var/lib/bluetooth/<adaptör>/<cihaz>/info`.

ÖLÇÜLMÜŞ DÜZEN (2026-09-03, Windows 11 misafir, bluez 5.87):

    Keys\\<adaptör-mac>
        CentralIRK        : Binary len=16      <- adaptörün kendi IRK'si
        <cihaz-mac>       : Binary len=16      <- BR/EDR link key (DEĞER)
    Keys\\<adaptör-mac>\\<cihaz-mac>            <- LE bond (ALT ANAHTAR)
        LTK, IRK          : Binary len=16
        KeyLength, EDIV, AddressType, AuthReq, CEntralIRKStatus : DWord
        ERand, Address    : QWord

Yani iki teknoloji iki ayrı biçimde duruyor: Klasik bond adaptör anahtarının
altında **bir değer**, LE bond **bir alt anahtar**. Ayrım ölçüldü, tahmin
değil — Soundcore Life Q10 (BR/EDR) ve Xbox Wireless Controller (LE) aynı
anda eşleştirilerek.

ÖLÇÜLMEMİŞ olan ve bu yüzden bayrağa bağlanan tek şey **bayt sırası**:
Windows'un `REG_BINARY` baytları BlueZ'in hex dizesiyle aynı sırada mı?
`--key-order asis` (varsayılan) ve `--key-order reverse` iki kolu verir;
doğru kol bağlantının kurulup kurulmadığından okunur.

GİZLİLİK: anahtar baytları stdout'a **basılmaz**, yalnız `info` dosyasına
0600 ile yazılır. `--dry-run` de baytları basmaz.

Kullanım:
    sudo tools/win-to-bluez.py                      # yaz (varsayılan domain)
    tools/win-to-bluez.py --dry-run                 # yalnız yapıyı göster
    sudo tools/win-to-bluez.py --key-order reverse --force
"""

import argparse
import configparser
import hashlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentexec import run_powershell  # noqa: E402

PARAMS = r"HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters"

# Değer satırları: V<TAB>anahtar-yolu<TAB>ad<TAB>tip<TAB>değer
# Binary değerler hex olarak gelir; sayısal tipler ondalık.
POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$params = 'HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters'

function Emit-Item($item) {
  $label = $item.Name
  foreach ($n in $item.GetValueNames()) {
    $v = $item.GetValue($n)
    $t = $item.GetValueKind($n)
    if ($v -is [byte[]]) {
      $hex = -join ($v | ForEach-Object { $_.ToString('x2') })
      "V`t$label`t$n`tBinary`t$hex"
    } else {
      "V`t$label`t$n`t$t`t$v"
    }
  }
}

foreach ($sub in 'Keys', 'Devices') {
  $root = Join-Path $params $sub
  if (-not (Test-Path $root)) { "MISSING`t$root"; continue }
  Emit-Item (Get-Item -LiteralPath $root)
  Get-ChildItem -LiteralPath $root -Recurse | ForEach-Object {
    Emit-Item (Get-Item -LiteralPath $_.PSPath)
  }
}
"""


def mac_from_hex12(text):
    """`c88a9a004717` → `C8:8A:9A:00:47:17`; değilse None."""
    text = text.strip()
    if len(text) != 12:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return ":".join(text[i:i + 2] for i in range(0, 12, 2)).upper()


def parse_dump(stdout):
    """Ajan çıktısını {anahtar-yolu: {değer-adı: (tip, değer)}} sözlüğüne çevir."""
    tree = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if not parts or parts[0] != "V" or len(parts) < 5:
            continue
        _, path, name, kind, value = parts[0], parts[1], parts[2], parts[3], "\t".join(parts[4:])
        tree.setdefault(path, {})[name] = (kind, value)
    return tree


def split_path(path):
    """Kayıt defteri yolunun `…\\Parameters\\<alt>` sonrası parçalarını ver."""
    marker = r"BTHPORT\Parameters" + "\\"
    idx = path.find(marker)
    if idx < 0:
        return []
    return path[idx + len(marker):].split("\\")


def collect(tree):
    """Ölçülen ağacı adaptör → bond yapısına çevir."""
    adapters = {}
    names = {}

    for path, values in tree.items():
        parts = split_path(path)
        if not parts:
            continue

        if parts[0] == "Devices" and len(parts) == 2:
            mac = mac_from_hex12(parts[1])
            kind_value = values.get("Name")
            if mac and kind_value and kind_value[0] == "Binary":
                raw = bytes.fromhex(kind_value[1])
                names[mac] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            continue

        if parts[0] != "Keys":
            continue

        # Keys\<adaptör>            → BR/EDR link key'ler + CentralIRK
        # Keys\<adaptör>\<cihaz>    → LE bond
        if len(parts) == 2:
            adapter = mac_from_hex12(parts[1])
            if not adapter:
                continue
            entry = adapters.setdefault(adapter, {"bredr": {}, "le": {}, "central_irk": None})
            for name, (kind, value) in values.items():
                if name.lower() == "centralirk":
                    entry["central_irk"] = value
                    continue
                dev = mac_from_hex12(name)
                if dev and kind == "Binary":
                    entry["bredr"][dev] = value
        elif len(parts) == 3:
            adapter = mac_from_hex12(parts[1])
            dev = mac_from_hex12(parts[2])
            if not (adapter and dev):
                continue
            entry = adapters.setdefault(adapter, {"bredr": {}, "le": {}, "central_irk": None})
            entry["le"][dev] = {name: value for name, (kind, value) in values.items()}

    return adapters, names


def key_hex(raw_hex, order):
    """REG_BINARY hex'ini BlueZ'in beklediği büyük harfli hex'e çevir."""
    data = bytes.fromhex(raw_hex)
    if order == "reverse":
        data = data[::-1]
    return data.hex().upper()


def bredr_info(name, link_key, key_type, order):
    return "\n".join([
        "[General]",
        f"Name={name}",
        "SupportedTechnologies=BR/EDR;",
        "Trusted=true",
        "Blocked=false",
        "",
        "[LinkKey]",
        f"Key={key_hex(link_key, order)}",
        f"Type={key_type}",
        "PINLength=0",
        "",
    ])


def le_info(name, bond, order, authenticated):
    addr_type = "public" if int(bond.get("AddressType", 0)) == 0 else "static"
    enc_size = int(bond.get("KeyLength", 16))
    ediv = int(bond.get("EDIV", 0))
    rand = int(bond.get("ERand", 0))

    lines = [
        "[General]",
        f"Name={name}",
        f"AddressType={addr_type}",
        "SupportedTechnologies=LE;",
        "Trusted=true",
        "Blocked=false",
        "",
    ]
    if "IRK" in bond:
        lines += ["[IdentityResolvingKey]", f"Key={key_hex(bond['IRK'], order)}", ""]
    if "LTK" in bond:
        lines += [
            "[LongTermKey]",
            f"Key={key_hex(bond['LTK'], order)}",
            f"Authenticated={authenticated}",
            f"EncSize={enc_size}",
            f"EDiv={ediv}",
            f"Rand={rand}",
            "",
        ]
    return "\n".join(lines)


def fingerprint(raw_hex):
    """Anahtarın kısa parmak izi — bayt değil, sha256'nın ilk 12 hex'i.

    Karşılaştırma için yeterli, geri döndürülemez: nota ve hata kaydına
    yapıştırılabilir.
    """
    return hashlib.sha256(bytes.fromhex(raw_hex)).hexdigest()[:12]


def compare_key(guest_hex, host_hex):
    """İki anahtarı karşılaştır; bayt sırasını da söyle."""
    if host_hex is None:
        return "host'ta yok", None
    guest = bytes.fromhex(guest_hex)
    host = bytes.fromhex(host_hex)
    if guest == host:
        return "EŞLEŞİYOR (aynı sıra)", fingerprint(guest_hex)
    if guest[::-1] == host:
        return "EŞLEŞİYOR (ters sıra)", fingerprint(guest_hex)
    return "EŞLEŞMİYOR", fingerprint(guest_hex)


def read_host_info(root, adapter, dev):
    """Host'taki `info` dosyasını oku; yoksa None."""
    path = Path(root) / adapter / dev / "info"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except FileNotFoundError:
        return None
    except PermissionError:
        sys.exit(f"{path} okunamadı — `sudo` ile çalıştırın")
    return parser


def verify(adapters, names, root, only):
    """Misafir ile host'un aynı anahtar materyalini taşıdığını doğrula."""
    problems = 0
    for adapter, entry in sorted(adapters.items()):
        print(f"adaptör {adapter}")
        for dev, link_key in sorted(entry["bredr"].items()):
            if only and dev not in only:
                continue
            info = read_host_info(root, adapter, dev)
            host_key = info["LinkKey"].get("Key") if info and info.has_section("LinkKey") else None
            verdict, fp = compare_key(link_key, host_key)
            problems += verdict != "EŞLEŞİYOR (aynı sıra)" and verdict != "EŞLEŞİYOR (ters sıra)"
            print(f"  BR/EDR {dev}  \"{names.get(dev, dev)}\"")
            print(f"    LinkKey  fp={fp}  {verdict}")

        for dev, bond in sorted(entry["le"].items()):
            if only and dev not in only:
                continue
            info = read_host_info(root, adapter, dev)
            print(f"  LE     {dev}  \"{names.get(dev, dev)}\"")
            for win_name, section in (("LTK", "LongTermKey"), ("IRK", "IdentityResolvingKey")):
                if win_name not in bond:
                    continue
                host_key = info[section].get("Key") if info and info.has_section(section) else None
                verdict, fp = compare_key(bond[win_name], host_key)
                problems += not verdict.startswith("EŞLEŞİYOR")
                print(f"    {win_name:<8} fp={fp}  {verdict}  [{section}]")
    return problems


def write_info(root, adapter, dev, content, force, dry_run):
    target_dir = Path(root) / adapter / dev
    target = target_dir / "info"

    if dry_run:
        print(f"  [dry-run] {target} ({len(content)} bayt, anahtarlar basılmadı)")
        return True

    if target.exists() and not force:
        print(f"  ATLANDI (var, --force yok): {target}")
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)
    if target.exists():
        backup = target.with_name(f"info.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        target.replace(backup)
        print(f"  yedek: {backup}")

    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    print(f"  yazıldı: {target}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="win11-nvme")
    parser.add_argument("--root", default="/var/lib/bluetooth",
                        help="BlueZ durum dizini (varsayılan /var/lib/bluetooth)")
    parser.add_argument("--key-order", choices=("asis", "reverse"), default="asis",
                        help="REG_BINARY baytlarının BlueZ'e yazılma sırası (ÖLÇÜLMEMİŞ)")
    parser.add_argument("--link-key-type", type=int, default=4,
                        help="BlueZ [LinkKey] Type (4 = unauthenticated combination, SSP)")
    parser.add_argument("--authenticated", type=int, default=0,
                        help="BlueZ [LongTermKey] Authenticated değeri")
    parser.add_argument("--only", action="append", default=[],
                        help="yalnız bu cihaz MAC'i (birden çok kez verilebilir)")
    parser.add_argument("--force", action="store_true", help="var olan info dosyasını yedekleyip değiştir")
    parser.add_argument("--dry-run", action="store_true", help="hiçbir şey yazma, yalnız yapıyı bas")
    parser.add_argument("--verify", action="store_true",
                        help="yazma; iki tarafın aynı anahtarı taşıdığını parmak iziyle doğrula")
    args = parser.parse_args()

    exitcode, stdout, stderr = run_powershell(args.domain, POWERSHELL)
    if exitcode != 0:
        sys.exit(f"misafir komutu exitcode={exitcode}\n{stderr}")

    adapters, names = collect(parse_dump(stdout))
    if not adapters:
        sys.exit("misafirde hiç bond yok (Keys altında adaptör anahtarı bulunamadı)")

    only = {m.upper() for m in args.only}

    if args.verify:
        problems = verify(adapters, names, args.root, only)
        print("\ntüm anahtarlar eşleşiyor." if not problems
              else f"\n{problems} anahtar eşleşmiyor ya da host'ta yok.")
        sys.exit(1 if problems else 0)

    written = 0

    for adapter, entry in sorted(adapters.items()):
        adapter_dir = Path(args.root) / adapter
        # `/var/lib/bluetooth` 0700 root: root değilken `is_dir()` sessizce
        # False döner, yani "yok" ile "okuyamadım" aynı görünür. Ayrılıyor.
        if adapter_dir.is_dir():
            state = "VAR"
        elif os.geteuid() != 0:
            state = "okunamadı (root değil)"
        else:
            state = "YOK"
        print(f"adaptör {adapter}  (host dizini: {state})")
        print(f"  CentralIRK: {'var' if entry['central_irk'] else 'yok'}"
              f"  |  BR/EDR bond: {len(entry['bredr'])}  |  LE bond: {len(entry['le'])}")

        if not adapter_dir.is_dir() and not args.dry_run:
            print("  ATLANDI: host'ta bu adaptörün dizini yok — başka radyo mu?")
            continue

        for dev, link_key in sorted(entry["bredr"].items()):
            if only and dev not in only:
                continue
            name = names.get(dev, dev)
            print(f"  BR/EDR {dev}  \"{name}\"")
            written += write_info(args.root, adapter, dev,
                                  bredr_info(name, link_key, args.link_key_type, args.key_order),
                                  args.force, args.dry_run)

        for dev, bond in sorted(entry["le"].items()):
            if only and dev not in only:
                continue
            name = names.get(dev, dev)
            fields = ", ".join(f"{k}={v}" for k, v in sorted(bond.items())
                               if k not in ("LTK", "IRK"))
            print(f"  LE     {dev}  \"{name}\"  [{fields}]")
            written += write_info(args.root, adapter, dev,
                                  le_info(name, bond, args.key_order, args.authenticated),
                                  args.force, args.dry_run)

    print(f"\n{written} info dosyası {'planlandı' if args.dry_run else 'yazıldı'}.")
    if not args.dry_run and written:
        print("BlueZ bunları yalnız adaptör kurulurken okur: "
              "`systemctl restart bluetooth` (radyo host'ta olmalı).")


if __name__ == "__main__":
    main()
