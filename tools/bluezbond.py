"""BlueZ tarafı: `/var/lib/bluetooth/<adaptör>/<cihaz>/info` — okuma ve yazma.

Bu modül **ölçülmüş BlueZ bond biçiminin tek sahibidir**; iki yön de düzeni
buradan okur.

ÖLÇÜLMÜŞ BİÇİM (2026-09-03, bluez 5.87):

    [General]
    Name=<cihaz adı>
    AddressType=public|static           <- yalnız LE
    SupportedTechnologies=BR/EDR;|LE;
    Trusted=true
    Blocked=false

    [LinkKey]                           <- BR/EDR
    Key=<32 hex>
    Type=4
    PINLength=0

    [IdentityResolvingKey]              <- LE
    Key=<32 hex>

    [LongTermKey]                       <- LE
    Key=<32 hex>
    Authenticated=0
    EncSize=16
    EDiv=0
    Rand=0

`Class`, `Services`, `Appearance`, `ConnectionParameters`, `DeviceID`
YAZILMAZ — BlueZ ilk bağlantıda kendisi ekliyor ve bizim yazdığımız anahtar
bölümlerine dokunmuyor (ölçüldü).

Dizin 0700, dosya 0600 — `/var/lib/bluetooth`'un kendi düzeniyle aynı.
"""

import configparser
import os
import re
import sys
import time
from pathlib import Path

ROOT = "/var/lib/bluetooth"

MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def _parser():
    parser = configparser.ConfigParser()
    parser.optionxform = str          # BlueZ alan adları büyük/küçük duyarlı
    return parser


def read_info(root, adapter, dev):
    """Host'taki `info` dosyasını oku; yoksa None."""
    path = Path(root) / adapter / dev / "info"
    parser = _parser()
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except FileNotFoundError:
        return None
    except PermissionError:
        sys.exit(f"{path} okunamadı — `sudo` ile çalıştırın")
    return parser


def list_adapters(root):
    """Host'taki adaptör MAC'lerini ver.

    `/var/lib/bluetooth` 0700 root: root değilken `iterdir()` PermissionError
    atar — "yok" ile "okuyamadım" ayrılıyor, çünkü ikisi sessizce aynı boş
    listeye inerdi.
    """
    base = Path(root)
    if not base.is_dir():
        if os.geteuid() != 0:
            sys.exit(f"{root} okunamadı — `sudo` ile çalıştırın")
        return []
    try:
        entries = sorted(p.name for p in base.iterdir() if p.is_dir())
    except PermissionError:
        sys.exit(f"{root} okunamadı — `sudo` ile çalıştırın")
    return [name for name in entries if MAC_RE.match(name)]


def list_bonds(root, adapter):
    """Bir adaptörün bond'larını ver: {cihaz-mac: ConfigParser}.

    Bond'un taşıyıcısı `<MAC>/info` dosyasıdır; `cache/` girdisi bond DEĞİL,
    yalnız görülmüş cihazın adıdır ve buraya girmez.
    """
    base = Path(root) / adapter
    bonds = {}
    try:
        entries = sorted(p.name for p in base.iterdir() if p.is_dir())
    except (PermissionError, FileNotFoundError) as exc:
        if isinstance(exc, PermissionError):
            sys.exit(f"{base} okunamadı — `sudo` ile çalıştırın")
        return bonds
    for name in entries:
        if not MAC_RE.match(name):
            continue
        info = read_info(root, adapter, name)
        if info is not None:
            bonds[name] = info
    return bonds


def technologies(info):
    """`SupportedTechnologies` alanını kümeye çevir (`{"BR/EDR"}`, `{"LE"}`, …)."""
    raw = info["General"].get("SupportedTechnologies", "") if info.has_section("General") else ""
    return {part for part in raw.split(";") if part}


def device_name(info, fallback):
    if info.has_section("General"):
        return info["General"].get("Name", fallback)
    return fallback


def section_key(info, section):
    """Bir bölümün `Key` alanını ver; bölüm yoksa None."""
    if not info.has_section(section):
        return None
    return info[section].get("Key")


def service_records(root, adapter, dev):
    """Cihazın ham SDP kayıtlarını `cache/<mac>` dosyasından ver.

    ÖLÇÜLDÜ (2026-09-03): BlueZ bu kayıtları `[ServiceRecords]` altında
    `0x00010000=3538…` biçiminde saklıyor ve baytlar Windows'un
    `Devices\\<mac>\\CachedServices` değerleriyle **birebir aynı** — aynı SDP
    veri elemanı dizisi, aynı 1 baytlık uzunluk başlığı. Yani Windows tarafı
    bu bloktan doğrudan yazılabiliyor, yeniden üretmek gerekmiyor.

    Döner: {"00010000": "<hex>", …} (Windows'un değer adı biçiminde).
    """
    path = Path(root) / adapter / "cache" / dev
    parser = _parser()
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except FileNotFoundError:
        return {}
    except PermissionError:
        sys.exit(f"{path} okunamadı — `sudo` ile çalıştırın")
    if not parser.has_section("ServiceRecords"):
        return {}
    records = {}
    for name, value in parser["ServiceRecords"].items():
        key = name[2:] if name.lower().startswith("0x") else name
        records[key] = value.strip().lower()
    return records


def services(info):
    """`[General] Services` listesini UUID dizisine çevir (sıra korunur)."""
    if not info.has_section("General"):
        return []
    raw = info["General"].get("Services", "")
    return [part for part in raw.split(";") if part]


def general_int(info, field):
    """`[General]` altındaki sayısal bir alanı int'e çevir (`0x…` de olur)."""
    if not info.has_section("General"):
        return None
    raw = info["General"].get(field)
    if raw is None:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def device_id(info):
    """`[DeviceID]` bölümünü Windows'un VID/PID/VIDType/Version alanlarına çevir.

    BlueZ `Source` ile Windows `VIDType` aynı sayıyı taşıyor (ölçüldü: ikisi de
    2 = USB Implementers Forum). Bölüm yoksa boş sözlük döner.
    """
    if not info.has_section("DeviceID"):
        return {}
    section = info["DeviceID"]

    def num(name):
        raw = section.get(name)
        return None if raw is None else int(raw, 0)

    return {
        "VIDType": num("Source"),
        "VID": num("Vendor"),
        "PID": num("Product"),
        "Version": num("Version"),
    }


def bredr_info(name, link_key, key_type, order):
    """BR/EDR bond'u için `info` içeriği üret."""
    from winbond import key_hex
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
    """LE bond'u için `info` içeriği üret (`bond` = Windows LE alan sözlüğü)."""
    from winbond import key_hex

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


# BlueZ'in ilk bağlantıda kendisi eklediği, bizim üretmediğimiz alanlar.
# Üzerine yazarken KORUNUR: ters yön (`bluez-to-win.py`) tam olarak bunlardan
# Windows'un `COD`, `LEAppearance`, `VID/PID/Version` ve profil UUID'lerini
# türetiyor. Korunmazsa bir `--force` turu ters yönü sessizce sakatlar
# (ölçüldü 2026-09-03: alanlar gitti, hata yok, çıkış kodu 0).
PRESERVED_GENERAL = ("Class", "Services", "Appearance", "CablePairing", "WakeAllowed")
PRESERVED_SECTIONS = ("ConnectionParameters", "DeviceID")


def merge_preserved(existing, content):
    """Var olan `info`daki öğrenilmiş alanları yeni içeriğe taşı.

    Anahtar bölümlerine dokunmaz — onlar tanımı gereği yenileniyor.
    """
    if existing is None:
        return content

    new = _parser()
    new.read_string(content)

    if existing.has_section("General"):
        if not new.has_section("General"):
            new.add_section("General")
        for field in PRESERVED_GENERAL:
            if existing["General"].get(field) and not new["General"].get(field):
                new["General"][field] = existing["General"][field]

    for section in PRESERVED_SECTIONS:
        if existing.has_section(section) and not new.has_section(section):
            new.add_section(section)
            for field, value in existing[section].items():
                new[section][field] = value

    out = []
    for section in new.sections():
        out.append(f"[{section}]")
        for field, value in new[section].items():
            out.append(f"{field}={value}")
        out.append("")
    return "\n".join(out)


def write_info(root, adapter, dev, content, force, dry_run):
    """`info` dosyasını yaz; var olanı `--force` olmadan değiştirme."""
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
