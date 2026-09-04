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
    EDiv=0                              <- legacy eşleştirmede sıfır DEĞİL
    Rand=0                              <- işaretsiz 64-bit

    [LocalSignatureKey]                 <- yalnız imza anahtarı dağıtan cihaz
    [RemoteSignatureKey]
    Key=<32 hex>
    Counter=<32-bit>
    Authenticated=0

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


def bond_info(name, order, link_key=None, key_type=4, le_bond=None,
              authenticated=0, addr_type_code=None):
    """Bir cihazın `info` içeriği — ÇİFT KİPLİ cihazda İKİ anahtar bölümü birden.

    **`SupportedTechnologies`in tek sahibi burası**, ve değeri sabit değil
    ELDEKİNDEN türetiliyor: `link_key` varsa `BR/EDR;`, `le_bond` varsa `LE;`,
    ikisi varsa `BR/EDR;LE;`.

    ÖLÇÜLDÜ (2026-09-04, sentetik girdiyle, cihaz gerekmedi): aynı MAC
    Windows'ta hem `Keys\\<adaptör>` **değeri** (BR/EDR link key) hem aynı
    yolun **alt anahtarı** (LE bond) olarak durabiliyor, ve `winbond.collect`
    ikisini ayrı kovaya koyuyor. Eski yol iki kovayı ayrı ayrı yazıyordu;
    ikisi de **aynı** `<adaptör>/<cihaz>/info` dosyasına gittiği için LE
    yazımı BR/EDR'yi **siliyordu** — `[LinkKey]` `PRESERVED_SECTIONS`ta
    olmadığı için `merge_preserved` da kurtarmıyordu. Hata yok, rc=0, geriye
    yalnız `SupportedTechnologies=LE;` kalıyordu. Ters yön (`bluez-to-win.py`)
    hep doğruydu: orada iki **bağımsız `if`** var, tek dosya değil iki ayrı
    kayıt defteri yolu yazılıyor.

    `addr_type_code` çağırandan gelir (0 public / 1 random). Kaynağı `Keys`
    olmayabilir — bazı cihazda o alan hiç yazılmıyor; kararı
    `winbond.le_address_type` verir.

    KAPSAM: `BR/EDR;LE;` biçimi `technologies()`in ayrıştırıcısından
    türetildi (`split(";")`) ve bu makinedeki bond'ların tek teknolojili
    biçimiyle uyumlu; **gerçek bir çift kipli cihazda ölçülmedi** — bu
    makinede öyle bir cihaz yok.
    """
    from winbond import as_uint, key_hex

    techs = []
    if link_key is not None:
        techs.append("BR/EDR")
    if le_bond is not None:
        techs.append("LE")

    lines = ["[General]", f"Name={name}"]
    # `AddressType` YALNIZ LE'de yazılır (ölçülmüş biçim → modül başlığı).
    if le_bond is not None:
        lines.append(f"AddressType={'public' if addr_type_code == 0 else 'static'}")
    lines += [
        "SupportedTechnologies=" + "".join(f"{tech};" for tech in techs),
        "Trusted=true",
        "Blocked=false",
        "",
    ]

    if link_key is not None:
        lines += [
            "[LinkKey]",
            f"Key={key_hex(link_key, order)}",
            f"Type={key_type}",
            "PINLength=0",
            "",
        ]

    if le_bond is not None:
        if "IRK" in le_bond:
            lines += ["[IdentityResolvingKey]",
                      f"Key={key_hex(le_bond['IRK'], order)}", ""]
        if "LTK" in le_bond:
            lines += [
                "[LongTermKey]",
                f"Key={key_hex(le_bond['LTK'], order)}",
                f"Authenticated={authenticated}",
                f"EncSize={as_uint(le_bond.get('KeyLength', 16), 32)}",
                f"EDiv={as_uint(le_bond.get('EDIV', 0), 32)}",
                f"Rand={as_uint(le_bond.get('ERand', 0), 64)}",
                "",
            ]
        lines += _signature_sections(le_bond, order, authenticated)

    return "\n".join(lines)


def bredr_info(name, link_key, key_type, order):
    """Yalnız BR/EDR — `bond_info`ya ince sarmal (düzenin sahibi orası)."""
    return bond_info(name, order, link_key=link_key, key_type=key_type)


def le_info(name, bond, order, authenticated, addr_type_code):
    """Yalnız LE — `bond_info`ya ince sarmal (düzenin sahibi orası)."""
    return bond_info(name, order, le_bond=bond, authenticated=authenticated,
                     addr_type_code=addr_type_code)


# Windows ↔ BlueZ imza anahtarı eşlemesi. Yön TÜRETİM, ölçüm değil: "Inbound"
# = gelen imzalı veriyi doğrulamak için tutulan **uzak** anahtar → BlueZ'in
# `RemoteSignatureKey`'i; giden veriyi imzalayan **yerel** anahtar →
# `LocalSignatureKey`. Ters yön (`bluez-to-win.py` `le_fields`) aynı eşlemeyi
# zaten kullanıyor; iki fonksiyon birbirinin tersi olmak zorunda.
#
# Grup ve alan adları önce bluez 5.87-2 ikilisinde ayrı dize olarak
# doğrulandı (`LocalSignatureKey`, `RemoteSignatureKey`, `Counter`,
# `Authenticated`), SONRA davranış ölçüldü: yazdığımız iki bölüm bağlantıdan
# sonra BlueZ tarafından okunup geri yazıldı — yani kayıt tüketiliyor, yalnız
# duruyor değil. (`Key` ve `LongTermKey` ikilide ayrı dize olarak GÖRÜNMÜYOR
# ama gerçek `info` dosyalarında var: `strings` bir uzun dizenin sonekini
# ayrıca basmaz, yani orada yokluk kanıt değil.)
SIGNATURE_MAP = (
    ("LocalSignatureKey", "CSRK", "OutboundSignCounter"),
    ("RemoteSignatureKey", "CSRKInbound", "InboundSignCounter"),
)

# BlueZ `Counter`ı 32-bit okuyor; Windows'un "henüz imzalı veri gelmedi"
# nöbetçisi `InboundSignCounter = -1` (işaretsiz 0xFFFF…FFFF) oraya sığmıyor,
# ve sığsaydı "bundan küçük her sayacı reddet" anlamına gelirdi. Sığmayan
# değer 0'a iniyor — TÜRETİM, ölçülmedi.
_COUNTER_MAX = (1 << 32) - 1


def _signature_sections(bond, order, authenticated):
    """`CSRK`/`CSRKInbound` varsa BlueZ imza anahtarı bölümlerini üret."""
    from winbond import as_uint, key_hex

    # `Authenticated` bu iki bölümde BOOLEAN, `[LongTermKey]`de tam sayı —
    # ölçüldü (2026-09-04): yazdığımız `0` BlueZ tarafından okundu ve dosya
    # geri yazılırken `false`a normalize edildi, LTK'nınki `0` kaldı. İkisi de
    # kabul ediliyor; BlueZ'in kendi yazımı taklit ediliyor ki `--force` turu
    # gereksiz fark üretmesin.
    auth = "true" if int(authenticated) else "false"
    lines = []
    for section, key_field, counter_field in SIGNATURE_MAP:
        if key_field not in bond:
            continue
        counter = as_uint(bond.get(counter_field, 0), 64)
        lines += [
            f"[{section}]",
            f"Key={key_hex(bond[key_field], order)}",
            f"Counter={0 if counter > _COUNTER_MAX else counter}",
            f"Authenticated={auth}",
            "",
        ]
    return lines


# BlueZ'in ilk bağlantıda kendisi eklediği, bizim üretmediğimiz alanlar.
# Üzerine yazarken KORUNUR: ters yön (`bluez-to-win.py`) tam olarak bunlardan
# Windows'un `COD`, `LEAppearance`, `VID/PID/Version` ve profil UUID'lerini
# türetiyor. Korunmazsa bir `--force` turu ters yönü sessizce sakatlar
# (ölçüldü 2026-09-03: alanlar gitti, hata yok, çıkış kodu 0).
# İmza anahtarları da listede: `merge_preserved` yalnız yeni içerikte OLMAYAN
# bölümü taşır, yani misafirde `CSRK` varsa üzerine yazılır — yoksa host'un
# kendi eşleştirmesinden kalan anahtar korunur. Bayat bir imza anahtarı
# tutmak, anahtarı sessizce düşürmekten iyidir: bu depoda ödenmiş hata tam
# olarak sessiz düşürmedir.
PRESERVED_GENERAL = ("Class", "Services", "Appearance", "CablePairing", "WakeAllowed")
PRESERVED_SECTIONS = ("ConnectionParameters", "DeviceID",
                      "LocalSignatureKey", "RemoteSignatureKey")


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
