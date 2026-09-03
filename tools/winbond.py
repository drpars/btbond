"""Windows tarafı: `BTHPORT\\Parameters` bond düzeni — okuma, ayrıştırma, yazma.

Bu modül **ölçülmüş kayıt defteri düzeninin tek sahibidir**. İki yön de
(`win-to-bluez.py`, `bluez-to-win.py`) düzeni buradan okur; iki kopya tutulsaydı
biri ilerler, öbürü donar ve donduğunu okuyucuya söylemezdi.

ÖLÇÜLMÜŞ DÜZEN (2026-09-03, Windows 11 misafir, iki gerçek cihaz eşleştirilerek):

    Keys\\<adaptör-mac>
        CentralIRK        : Binary len=16      <- adaptörün kendi IRK'si
        <cihaz-mac>       : Binary len=16      <- BR/EDR link key (DEĞER)
    Keys\\<adaptör-mac>\\<cihaz-mac>            <- LE bond (ALT ANAHTAR)
        LTK, IRK          : Binary len=16
        KeyLength, EDIV, AddressType, AuthReq, CEntralIRKStatus : DWord
        ERand, Address    : QWord
        CSRK, CSRKInbound : Binary len=16      <- yalnız imza anahtarı dağıtan cihazda
        OutboundSignCounter : DWord ; InboundSignCounter : QWord
    Devices\\<cihaz-mac>
        Name              : Binary             <- UTF-8, NUL ile biten cihaz adı
        LEAddressType     : DWord              <- 0 public, 1 random

Yani iki teknoloji iki ayrı biçimde duruyor: Klasik bond adaptör anahtarının
altında **bir değer**, LE bond **bir alt anahtar**. Ayrım ölçüldü, tahmin
değil — Soundcore Life Q10 (BR/EDR) ve Xbox Wireless Controller (LE) aynı
anda eşleştirilerek.

**ALAN KÜMESİ CİHAZA GÖRE DEĞİŞİYOR, ve eksiklik sessizdir** (ölçüldü
2026-09-04, ROG GLADIUS III WL eklenerek). LE **legacy** eşleştiren cihazda
`EDIV`/`ERand` sıfır değil, buna karşılık `AddressType`, `Address` ve `IRK`
**hiç yazılmıyor**; imza anahtarı dağıtan cihazda dört `CSRK*` alanı
ekleniyor. Yani bir alanın yokluğu "varsayılan değeri" demek değil — cevap
`Devices\\<mac>`te olabilir (→ `le_address_type`).

GİZLİLİK: bu modül anahtar baytını stdout'a basmaz. Karşılaştırma gereken
yerde `fingerprint()` kullanılır (sha256'nın ilk 12 hex'i).
"""

import hashlib
import uuid

PARAMS = r"HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters"
KEYS = PARAMS + r"\Keys"
DEVICES = PARAMS + r"\Devices"

# Windows'un LE bond'unda okunan ama YORUMLANMAYAN iki alan. Windows kendi
# eşleştirdiği cihazda bu değerleri yazmıştı; ters yönde yazarken aynıları
# kullanılıyor. "Doğru değer" oldukları ölçülmedi → README, "Ölçülmeyenler".
AUTHREQ_DEFAULT = 45
CENTRAL_IRK_STATUS_DEFAULT = 1

# `Devices\<mac>\ServicesFor<adaptör>` — ÖLÇÜLDÜ (2026-09-03, gerçek Windows
# eşleştirmesinin öncesi/sonrası kayıt defteri karşılaştırılarak). Yalnız
# `Keys` yazmak yetmiyor: Windows cihazı `paired` gösteriyor ve link key ile
# gerçek bir baseband bağlantısı kuruyor, ama profil devnode'ları doğmuyor —
# ses cihazı ya da HID olarak kullanılamıyor. Bu bölüm o eksiği kapatır.
#
# Değerler tek bir eşleştirmeden alındı (bir BR/EDR kulaklık + bir LE oyun
# kolu); cihazdan cihaza değişip değişmedikleri ÖLÇÜLMEDİ.
BREDR_SERVICE_FLAGS = {
    "SSP Paired": 1,
    "SSP MITM Protected": 1,
    "SSP Supported": 1,
    "AuthenticationRequirements": 5,
    "RemoteAuthenticationRequirements": 5,
    "IoCapability": 1,
    "BasebandSupport": 16384,
    "BRFlags": 0,
}

# `LEFlags` CİHAZA GÖRE DEĞİŞİYOR — ölçüldü (2026-09-04), ve dördüncü alan
# değişmiyor: Xbox kolunda `268632064` (0x10030000), ROG faresinde `720896`
# (0x000B0000); aynı iki cihazda `AuthenticationRequirementsLE`,
# `RemoteAuthenticationRequirementsLE`, `IoCapabilityLE` ve `BasebandSupport`
# birebir aynı. Yani buradaki sabit yalnız Xbox'ın değeri; nasıl türetildiği
# BİLİNMİYOR ve BlueZ'de karşılığı aranmadı. Fare henüz ters yönde
# yazılmadığı için yanlış `LEFlags`in neyi bozduğu da ölçülmedi.
# Farenin kaydında ayrıca `LEExtendedDeviceInfoFlags = 0` var (burada yok).
LE_SERVICE_FLAGS = {
    "AuthenticationRequirementsLE": 3,
    "RemoteAuthenticationRequirementsLE": 255,
    "IoCapabilityLE": 255,
    "BasebandSupport": 32768,
    "LEFlags": 268632064,
}

# Windows'un her iki teknolojide de yazdığı sabitler.
DIB_SERVICE_VERSION = 131072
LOCAL_EVALD_IO_CAP = 1            # BR/EDR
LOCAL_EVALD_IO_CAP_LE = 4

# Servis alt anahtarının `C00000000` yaprağı. `PriLangServiceName` ölçüldü:
# 256 baytın tamamı SIFIR, yani ad taşımıyor — sabit tampon.
PRI_LANG_SERVICE_NAME_LEN = 256

# `LeContainerId` — Windows'un LE cihaza verdiği container GUID'i. Kullanıcının
# 2024'te elle hazırladığı ve ÇALIŞTIĞI bilinen `.reg` dosyalarının üçünde de
# var (`LeContainerIDSource=1` ile birlikte), o yüzden yazılıyor. Windows'un
# ürettiği değerin nasıl türetildiği bilinmiyor; burada adaptör+cihaz
# adresinden **deterministik** üretiliyor, böylece aynı çift her koşuda aynı
# GUID'i alır. GEREKLİ OLUP OLMADIĞI ÖLÇÜLMEDİ — `--no-container-id` ile
# kapatılabilir.
CONTAINER_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS ns


def container_guid_hex(adapter, dev):
    """Adaptör+cihaz çiftinden deterministik container GUID'i (registry baytı).

    Windows GUID'i kayıt defterinde .NET `Guid(byte[])` düzeniyle saklıyor:
    ilk üç alan küçük-endian, son sekiz bayt olduğu gibi (ölçüldü — dökülen
    `c9cc6d0e…` baytı `0e6dccc9-471e-…` GUID'ini veriyor). `bytes_le` tam olarak
    o düzendir.
    """
    name = f"btbond/{hex12(adapter)}/{hex12(dev)}"
    return uuid.uuid5(CONTAINER_NAMESPACE, name).bytes_le.hex()

# Değer satırları: V<TAB>anahtar-yolu<TAB>ad<TAB>tip<TAB>değer
# Binary değerler hex olarak gelir; sayısal tipler ondalık.
DUMP_POWERSHELL = r"""
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

# Yazma tarafının ortak başlığı.
#
# `New-Item -Force` var olan bir kayıt defteri anahtarını YENİDEN OLUŞTURUR,
# yani değerlerini siler — `Keys\<adaptör>` altındaki `CentralIRK` böyle
# kaybolurdu. O yüzden anahtar oluşturma daima `Test-Path` ile korunuyor.
#
# DWord/QWord'e işaretsiz değer yazmak için `BitConverter` üzerinden
# dönüştürülüyor: `ERand` gerçek bir 64-bit rastgele sayı olabilir ve doğrudan
# `[Int64]` cast'i taşarak hata verir.
WRITE_PRELUDE = r"""
$ErrorActionPreference = 'Stop'

function Hex-Bytes($h) {
  $b = New-Object byte[] ($h.Length / 2)
  for ($i = 0; $i -lt $b.Length; $i++) {
    $b[$i] = [Convert]::ToByte($h.Substring($i * 2, 2), 16)
  }
  ,$b
}

function Ensure-Key($p) {
  # `New-Item`in `-LiteralPath` parametresi YOK (ölçüldü: PowerShell 5.1
  # misafirde `NamedParameterNotFound` ile düşüyor) — yalnız `-Path` var.
  # Anahtar adları hex MAC olduğu için joker karakter riski yok.
  if (-not (Test-Path -LiteralPath $p)) { New-Item -Path $p -Force | Out-Null }
}

function Set-Bin($p, $n, $hex) {
  New-ItemProperty -LiteralPath $p -Name $n -PropertyType Binary `
    -Value (Hex-Bytes $hex) -Force | Out-Null
}

function Set-Dw($p, $n, $v) {
  $raw = [BitConverter]::ToInt32([BitConverter]::GetBytes([UInt32]$v), 0)
  New-ItemProperty -LiteralPath $p -Name $n -PropertyType DWord `
    -Value $raw -Force | Out-Null
}

function Set-Qw($p, $n, $v) {
  $raw = [BitConverter]::ToInt64([BitConverter]::GetBytes([UInt64]$v), 0)
  New-ItemProperty -LiteralPath $p -Name $n -PropertyType QWord `
    -Value $raw -Force | Out-Null
}

function Set-Str($p, $n, $v) {
  New-ItemProperty -LiteralPath $p -Name $n -PropertyType String `
    -Value $v -Force | Out-Null
}

# Sıfır tamponu misafirde üretiliyor, hex olarak GÖNDERİLMİYOR: betik
# `-EncodedCommand` ile gidiyor ve Windows'un komut satırı sınırı aşılırsa
# `guest-exec` "Failed to execute helper program (Invalid argument)" ile
# düşüyor (ölçüldü 2026-09-03: beş profilli tek bir BR/EDR kaydı sınırı aştı).
function Set-Zeros($p, $n, $len) {
  New-ItemProperty -LiteralPath $p -Name $n -PropertyType Binary `
    -Value (New-Object byte[] $len) -Force | Out-Null
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


def hex12(mac):
    """`C8:8A:9A:00:47:17` → `c88a9a004717` (kayıt defteri anahtar adı biçimi)."""
    return mac.replace(":", "").lower()


def fingerprint(raw_hex):
    """Anahtarın kısa parmak izi — bayt değil, sha256'nın ilk 12 hex'i.

    Karşılaştırma için yeterli, geri döndürülemez: nota ve hata kaydına
    yapıştırılabilir.
    """
    return hashlib.sha256(bytes.fromhex(raw_hex)).hexdigest()[:12]


def key_hex(raw_hex, order):
    """Anahtar baytlarını istenen sırada büyük harfli hex'e çevir."""
    data = bytes.fromhex(raw_hex)
    if order == "reverse":
        data = data[::-1]
    return data.hex().upper()


def as_uint(value, bits):
    """Ajanın bastığı DWORD/QWORD'ü işaretsiz sayıya çevir.

    PowerShell `GetValue` bir QWORD'ü `[Int64]`, DWORD'ü `[Int32]` olarak
    döndürüyor: üst biti dolu olan gerçek bir değer **negatif** basılıyor.
    ÖLÇÜLDÜ (2026-09-04, ROG GLADIUS III WL): `ERand = -241429041862138248`,
    `InboundSignCounter = -1`. BlueZ `Rand` alanını işaretsiz okur, yani
    çevrilmeden yazılan değer o alanın anlamını değiştirir.

    Xbox kolunda iki alan da 0 olduğu için bu tuzak ilk turda görünmedi —
    LE **legacy** eşleştirme (EDIV/ERand sıfır değil) ilk kez bu cihazla
    ölçüldü.
    """
    value = int(value)
    return value + (1 << bits) if value < 0 else value


# Bir değerin anahtar materyali olup olmadığı ADA göre değil UZUNLUĞA göre
# sorulur: 16 bayt (32 hex) ve saf hex ise anahtardır. Ad bazlı eleme bir kez
# ödendi — `CSRK`/`CSRKInbound` listede olmadığı için `--dry-run` çıktısında
# baytlarıyla stdout'a düştü (2026-09-04). Sayısal alanlar bu sondaya
# takılamaz: en geniş DWORD/QWORD ondalık gösterimi 20 basamak, eşik 32.
_HEX_DIGITS = set("0123456789abcdefABCDEF")


def looks_like_key(value):
    """Değer bir anahtar materyali mi (>=32 hex basamak, saf hex)?"""
    text = str(value)
    return len(text) >= 32 and len(text) % 2 == 0 and set(text) <= _HEX_DIGITS


def redact(mapping):
    """Bir alan sözlüğünü basıma hazırla: anahtarların BAYTI BASILMAZ.

    Anahtar görünen her değer parmak iziyle değiştirilir; geri kalan olduğu
    gibi kalır. Çıktı nota ve hata kaydına yapıştırılabilir.
    """
    out = {}
    for name, value in mapping.items():
        out[name] = f"fp={fingerprint(value)}" if looks_like_key(value) else value
    return out


def le_address_type(bond, props):
    """LE cihazın adres tipini ver: `(0|1, kaynak)` — 0 public, 1 random.

    İKİ KAYNAK, ve `Keys` her cihazda taşımıyor (ölçüldü 2026-09-04):

        cihaz                    Keys\\…\\<mac>\\AddressType   Devices\\<mac>\\LEAddressType
        Xbox Wireless Controller  0                            0
        ROG GLADIUS III WL        (YOK)                        1

    Yani `bond.get("AddressType", 0)` varsayılanı faresi için sessizce
    **yanlış** cevap veriyordu: `public` yazılan bir static-random cihazı
    BlueZ bulamaz. Eksikse `Devices` kaydına düşülür.

    Adresin üst iki biti (`D4` → `11` → static random) yalnız **doğrulayıcı**
    olarak kullanılır, karar verici olarak değil: bit kuralı ancak adres zaten
    rastgeleyse anlamlıdır, public bir OUI de aynı bitleri taşıyabilir.
    """
    if "AddressType" in bond:
        return as_uint(bond["AddressType"], 32), "Keys"
    if "LEAddressType" in props:
        return as_uint(props["LEAddressType"], 32), "Devices"
    return 0, "varsayılan (iki kaynakta da yok)"


def parse_dump(stdout):
    """Ajan çıktısını {anahtar-yolu: {değer-adı: (tip, değer)}} sözlüğüne çevir."""
    tree = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if not parts or parts[0] != "V" or len(parts) < 5:
            continue
        path, name, kind = parts[1], parts[2], parts[3]
        value = "\t".join(parts[4:])
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
    """Ölçülen ağacı adaptör → bond yapısına çevir.

    Döner: ({adaptör-mac: {"bredr": {...}, "le": {...}, "central_irk": ...}},
            {cihaz-mac: ad},
            {cihaz-mac: {`Devices\\<mac>` alanları}})

    Üçüncü sözlük `Keys`te bulunmayan ama bond'un anlamını taşıyan alanlar
    için: `LEAddressType` orada duruyor ve bazı cihazlarda `Keys` karşılığı
    hiç yazılmıyor → `le_address_type`.
    """
    adapters = {}
    names = {}
    devices = {}

    for path, values in tree.items():
        parts = split_path(path)
        if not parts:
            continue

        if parts[0] == "Devices" and len(parts) == 2:
            mac = mac_from_hex12(parts[1])
            if not mac:
                continue
            devices[mac] = {name: value for name, (kind, value) in values.items()}
            kind_value = values.get("Name")
            if kind_value and kind_value[0] == "Binary":
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

    return adapters, names, devices


def name_blob(name):
    """Cihaz adını `Devices\\<mac>\\Name`'in beklediği biçime çevir.

    Ölçülen biçim: UTF-8, NUL ile biten — REG_BINARY olarak saklanıyor.
    """
    return (name.encode("utf-8") + b"\x00").hex()


def bredr_script(adapter, dev, link_key_hex):
    """Bir BR/EDR bond'unun ANAHTARINI yazan PowerShell parçası.

    Cihaz kaydı ayrı: → `device_record_script`. Yalnız bu parça yazılırsa
    Windows cihazı `paired` gösterir ve link key ile bağlanır, ama profil
    devnode'ları doğmaz (ölçüldü 2026-09-03).
    """
    a, d = hex12(adapter), hex12(dev)
    return "\n".join([
        f"Ensure-Key '{KEYS}\\{a}'",
        f"Set-Bin '{KEYS}\\{a}' '{d}' '{link_key_hex}'",
        f"'OK bredr {d}'",
    ])


def device_record_script(adapter, dev, name, is_le, attrs, services, sdp_records=None):
    """`Devices\\<mac>` + `ServicesFor<adaptör>` kaydını yazan parça.

    `attrs`: BR/EDR için {"COD": int}; LE için {"LEAppearance", "LEAddressType",
    "VID", "PID", "VIDType", "Version"} (olmayanlar atlanır).
    `services`: BR/EDR profil UUID'leri (LE'de kullanılmaz — Windows LE servis
    düğümlerini GATT keşfinden kuruyor, `ServicesFor` altında {uuid} yok).
    """
    a, d = hex12(adapter), hex12(dev)
    dev_path = f"{DEVICES}\\{d}"
    svc_path = f"{dev_path}\\ServicesFor{a}"

    lines = [
        f"Ensure-Key '{dev_path}'",
        f"Set-Dw '{dev_path}' 'DibServiceVersion' {DIB_SERVICE_VERSION}",
    ]
    if name:
        lines.append(f"Set-Bin '{dev_path}' 'Name' '{name_blob(name)}'")

    if is_le:
        if name:
            lines.append(f"Set-Bin '{dev_path}' 'LEName' '{name_blob(name)}'")
        lines.append(f"Set-Dw '{dev_path}' 'LocalEvaldIoCapLE' {LOCAL_EVALD_IO_CAP_LE}")
        if attrs.get("LeContainerId"):
            lines.append(f"Set-Bin '{dev_path}' 'LeContainerId' '{attrs['LeContainerId']}'")
            lines.append(f"Set-Dw '{dev_path}' 'LeContainerIDSource' 1")
        for field in ("LEAppearance", "LEAddressType", "VID", "PID", "VIDType", "Version",
                      "LERemoteConnParamsIntervalMin", "LERemoteConnParamsIntervalMax",
                      "LERemoteConnParamsLatency", "LERemoteConnParamsLSTO"):
            if attrs.get(field) is not None:
                lines.append(f"Set-Dw '{dev_path}' '{field}' {int(attrs[field])}")
        lines.append(f"Ensure-Key '{svc_path}'")
        for field, value in sorted(LE_SERVICE_FLAGS.items()):
            lines.append(f"Set-Dw '{svc_path}' '{field}' {value}")
        lines.append(f"Set-Qw '{svc_path}' 'LEExtendedDeviceInfoFlags' 0")
    else:
        lines.append(f"Set-Dw '{dev_path}' 'LocalEvaldIoCap' {LOCAL_EVALD_IO_CAP}")
        if attrs.get("COD") is not None:
            lines.append(f"Set-Dw '{dev_path}' 'COD' {int(attrs['COD'])}")
        lines.append(f"Ensure-Key '{svc_path}'")
        for field, value in sorted(BREDR_SERVICE_FLAGS.items()):
            lines.append(f"Set-Dw '{svc_path}' '{field}' {value}")
        lines.append(f"Set-Qw '{svc_path}' 'BRExtendedDeviceInfoFlags' 0")
        if sdp_records:
            cached = f"{dev_path}\\CachedServices"
            lines.append(f"Ensure-Key '{cached}'")
            for record_name, record_hex in sorted(sdp_records.items()):
                lines.append(f"Set-Bin '{cached}' '{record_name}' '{record_hex}'")
        for uuid in services:
            uuid_path = f"{svc_path}\\{{{uuid}}}"
            leaf = f"{uuid_path}\\C00000000"
            lines += [
                f"Ensure-Key '{uuid_path}'",
                f"Set-Dw '{uuid_path}' 'Instance' 1",
                f"Ensure-Key '{leaf}'",
                f"Set-Zeros '{leaf}' 'PriLangServiceName' {PRI_LANG_SERVICE_NAME_LEN}",
                f"Set-Str '{leaf}' 'DeviceString' ''",
                f"Set-Dw '{leaf}' 'CounterInstanceId' 0",
                f"Set-Dw '{leaf}' 'Enabled' 1",
            ]

    lines.append(f"'OK record {d}'")
    return "\n".join(lines)


def le_script(adapter, dev, bond):
    """Bir LE bond'unun ANAHTARINI yazan PowerShell parçası.

    `bond`: {"LTK": hex, "IRK": hex|None, "KeyLength": int, "EDIV": int,
             "ERand": int, "AddressType": int, "AuthReq": int,
             "CEntralIRKStatus": int}
    Cihaz kaydı ayrı → `device_record_script`.
    """
    a, d = hex12(adapter), hex12(dev)
    path = f"{KEYS}\\{a}\\{d}"
    lines = [
        f"Ensure-Key '{KEYS}\\{a}'",
        f"Ensure-Key '{path}'",
        f"Set-Bin '{path}' 'LTK' '{bond['LTK']}'",
    ]
    if bond.get("IRK"):
        lines.append(f"Set-Bin '{path}' 'IRK' '{bond['IRK']}'")
    for field in ("KeyLength", "EDIV", "AddressType", "AuthReq", "CEntralIRKStatus"):
        lines.append(f"Set-Dw '{path}' '{field}' {int(bond[field])}")
    for field in ("ERand", "Address"):
        lines.append(f"Set-Qw '{path}' '{field}' {int(bond[field])}")

    # İmza anahtarları (CSRK) — bu makinede ÖLÇÜLMEDİ: iki test cihazının
    # ikisi de dağıtmıyor. Alan adları ve tipleri kullanıcının 2024'te
    # hazırladığı, çalıştığı bilinen ROG fare `.reg` dosyasından alındı.
    # Hangi yönün hangi ada karşılık geldiği (BlueZ `[LocalSignatureKey]` →
    # `CSRK`) türetim, ölçüm değil.
    if bond.get("CSRK"):
        lines.append(f"Set-Bin '{path}' 'CSRK' '{bond['CSRK']}'")
        lines.append(f"Set-Dw '{path}' 'OutboundSignCounter' {int(bond.get('OutboundSignCounter', 0))}")
    if bond.get("CSRKInbound"):
        lines.append(f"Set-Bin '{path}' 'CSRKInbound' '{bond['CSRKInbound']}'")
        lines.append(f"Set-Qw '{path}' 'InboundSignCounter' {int(bond.get('InboundSignCounter', 0))}")

    lines.append(f"'OK le {d}'")
    return "\n".join(lines)


def remove_script(adapter, dev, is_le):
    """Bir bond'u misafirden silen PowerShell parçası (geri alma için)."""
    a, d = hex12(adapter), hex12(dev)
    lines = []
    if is_le:
        lines.append(f"Remove-Item -LiteralPath '{KEYS}\\{a}\\{d}' -Recurse -Force "
                     f"-ErrorAction SilentlyContinue")
    else:
        lines.append(f"Remove-ItemProperty -LiteralPath '{KEYS}\\{a}' -Name '{d}' "
                     f"-Force -ErrorAction SilentlyContinue")
    lines.append(f"Remove-Item -LiteralPath '{DEVICES}\\{d}' -Recurse -Force "
                 f"-ErrorAction SilentlyContinue")
    lines.append(f"'OK removed {d}'")
    return "\n".join(lines)
