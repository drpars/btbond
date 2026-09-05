"""Windows tarafı: `BTHPORT\\Parameters` bond düzeni — okuma, ayrıştırma, yazma.

Bu modül **ölçülmüş kayıt defteri düzeninin tek sahibidir**. İki yön de
(`btbond to-host`, `btbond to-guest`) düzeni buradan okur; iki kopya tutulsaydı
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

# `LEFlags` BU SÖZLÜKTE DEĞİL, ve bu bilinçli bir eksiklik → `LEFLAGS_NOTU`.
# Buradakiler iki LE cihazında **birebir aynı** ölçüldü (2026-09-04), yani
# sabit olmaları gözleme dayanıyor.
LE_SERVICE_FLAGS = {
    "AuthenticationRequirementsLE": 3,
    "RemoteAuthenticationRequirementsLE": 255,
    "IoCapabilityLE": 255,
    "BasebandSupport": 32768,
}

# `LEFlags` CİHAZA GÖRE DEĞİŞİYOR, ve TÜRETİLEMEDİ — ölçüldü (2026-09-04,
# canlı misafirde `Devices\<mac>\ServicesFor<adaptör>` altında):
#
#     Xbox Wireless Controller : 268632064 = 0x10030000  bitler {16,17,28}
#     ROG GLADIUS III WL       :    720896 = 0x000B0000  bitler {16,17,19}
#
# Ortak {16,17}; ayrışan yalnız Xbox'ta 28, yalnız farede 19. Aynı alt
# anahtardaki diğer beş alan (`AuthenticationRequirementsLE`,
# `RemoteAuthenticationRequirementsLE`, `IoCapabilityLE`, `BasebandSupport`,
# `LEExtendedDeviceInfoFlags`) iki cihazda birebir aynı.
#
# NEDEN TÜRETİLEMİYOR — iki cihazın bütün LE özellikleri **birlikte** farklı,
# yani n=2'de hiçbir bit tek bir özelliğe bağlanamıyor: Xbox LE Secure
# Connections (EDIV/ERand=0), IRK dağıtıyor, public adres, CSRK yok,
# `CEntralIRKStatus=1`; fare LE **legacy** (EDIV≠0), IRK **yok**, random
# adres, CSRK **var**, `CEntralIRKStatus=0`. Bit 28 için "LESC" / "IRK var" /
# "public adres" adaylarının üçü de aynı veriyi açıklıyor; bit 19 için
# "legacy" / "CSRK var" / "random adres" de öyle. Ayırmak üçüncü bir LE
# cihaz ister ve elde yok.
#
# BU YÜZDEN SABİT YAZILMIYOR. Xbox'ın değerini fareye yazmak ÖLÇÜLMÜŞ biçimde
# yanlış olurdu. Sıra: (1) hedefte varsa **korunur**, (2) kullanıcı
# `--le-flags` ile verirse yazılır, (3) ikisi de yoksa **hiç yazılmaz** ve
# bu raporlanır. Yokluğun neyi bozduğu da ölçülmedi — o yüzden sessiz
# kalmıyor, söyleniyor.
LEFLAGS_NOTU = ("LEFlags cihaza göre değişiyor ve n=2'de türetilemedi; "
                "sabit yazmak yerine korunur/verilir/atlanır")
LEFLAGS_FIELD = "LEFlags"

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
    """`aabbccddeeff` → `AA:BB:CC:DD:EE:FF`; değilse None."""
    text = text.strip()
    if len(text) != 12:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return ":".join(text[i:i + 2] for i in range(0, 12, 2)).upper()


def hex12(mac):
    """`AA:BB:CC:DD:EE:FF` → `aabbccddeeff` (kayıt defteri anahtar adı biçimi)."""
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
            {cihaz-mac: {`Devices\\<mac>` alanları}},
            {(cihaz-mac, adaptör-mac): {`ServicesFor<adaptör>` alanları}})

    Üçüncü sözlük `Keys`te bulunmayan ama bond'un anlamını taşıyan alanlar
    için: `LEAddressType` orada duruyor ve bazı cihazlarda `Keys` karşılığı
    hiç yazılmıyor → `le_address_type`.

    DÖRDÜNCÜSÜ 2026-09-04'te eklendi ve tek bir şey için var: `LEFlags` cihaza
    göre değişiyor ve türetilemiyor, yani hedefte bir değer varsa
    **korunabilmesi** gerekiyor → `LEFLAGS_NOTU`. Eskiden bu alt anahtar
    `collect`te sessizce düşüyordu (`len(parts) == 2` süzgeci), yani hedefin
    kendi değeri modelde hiç görünmüyordu.
    """
    adapters = {}
    names = {}
    devices = {}
    service_flags = {}

    for path, values in tree.items():
        parts = split_path(path)
        if not parts:
            continue

        # `Devices\<mac>\ServicesFor<adaptör>` — yaprak alt anahtarları
        # (`{uuid}`, `C00000000`) BU DALDA DEĞİL: onlar dört parça ve üstü.
        if (parts[0] == "Devices" and len(parts) == 3
                and parts[2].lower().startswith("servicesfor")):
            dev = mac_from_hex12(parts[1])
            adapter = mac_from_hex12(parts[2][len("ServicesFor"):])
            if dev and adapter:
                service_flags[(dev, adapter)] = {
                    name: value for name, (kind, value) in values.items()}
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

    return adapters, names, devices, service_flags


def existing_le_flags(service_flags, dev, adapter):
    """Hedefte `LEFlags` varsa işaretsiz tamsayı olarak ver, yoksa `None`.

    Değer `Set-Dw`in beklediği biçimde döner (işaretsiz): ajanın bastığı
    gösterim işaretli olabilir → `as_uint`.
    """
    raw = service_flags.get((dev, adapter), {}).get(LEFLAGS_FIELD)
    return None if raw is None else as_uint(raw, 32)


def name_blob(name):
    """Cihaz adını `Devices\\<mac>\\Name`'in beklediği biçime çevir.

    Ölçülen biçim: UTF-8, NUL ile biten — REG_BINARY olarak saklanıyor.
    """
    return (name.encode("utf-8") + b"\x00").hex()


# --- Ara temsil (IR) ve renderer'lar --------------------------------------
#
# NEDEN: emitörler eskiden doğrudan PowerShell **metni** üretiyordu, yani
# düzen (hangi anahtar, hangi değer, hangi tip) ile taşıyıcı (PowerShell'in
# sözdizimi) aynı f-string'de duruyordu. Offline kovan yazma yolu eklenince
# aynı düzen hivex çağrıları olarak **ikinci kez** yazılacaktı ve bu deponun
# kuralına göre biri ilerler, öbürü donardı.
#
# Bu yüzden emitörler artık `*_ops` — yazma **işlemleri** listesi üretiyorlar
# —, ve taşıyıcılar aptal renderer'lar. Düzenin tek sahibi hâlâ bu modül;
# `hivebond` okuma tarafında zaten aynı ayrımı kullanıyor.
#
# İşlem biçimi: `(tip, *argümanlar)`. Değer hex dizesi olarak taşınıyor
# (BlueZ'in `info` dosyası da hex veriyor); hivex renderer'ı `bytes.fromhex`
# ile çevirir.
KEY = "key"                # (KEY, yol)                  -> anahtarı var et
BIN = "bin"                # (BIN, yol, ad, hex)         -> REG_BINARY
DW = "dw"                  # (DW, yol, ad, int)          -> REG_DWORD (işaretsiz)
QW = "qw"                  # (QW, yol, ad, int)          -> REG_QWORD (işaretsiz)
STR = "str"                # (STR, yol, ad, dize)        -> REG_SZ
ZEROS = "zeros"            # (ZEROS, yol, ad, uzunluk)   -> N baytlık sıfır
ECHO = "echo"              # (ECHO, metin)               -> ilerleme işareti
DEL_KEY = "del_key"        # (DEL_KEY, yol)              -> anahtar ağacını sil
DEL_VALUE = "del_value"    # (DEL_VALUE, yol, ad)        -> tek değeri sil

# Sıfır tamponunun uzunluk olarak taşınması bilinçli: hex olarak gönderilse
# `-EncodedCommand` Windows'un komut satırı sınırını aşıyor (→ WRITE_PRELUDE).
_POWERSHELL = {
    KEY: lambda p: f"Ensure-Key '{p}'",
    BIN: lambda p, n, v: f"Set-Bin '{p}' '{n}' '{v}'",
    DW: lambda p, n, v: f"Set-Dw '{p}' '{n}' {v}",
    QW: lambda p, n, v: f"Set-Qw '{p}' '{n}' {v}",
    STR: lambda p, n, v: f"Set-Str '{p}' '{n}' '{v}'",
    ZEROS: lambda p, n, v: f"Set-Zeros '{p}' '{n}' {v}",
    ECHO: lambda t: f"'{t}'",
    DEL_KEY: lambda p: (f"Remove-Item -LiteralPath '{p}' -Recurse -Force "
                        f"-ErrorAction SilentlyContinue"),
    DEL_VALUE: lambda p, n: (f"Remove-ItemProperty -LiteralPath '{p}' -Name '{n}' "
                             f"-Force -ErrorAction SilentlyContinue"),
}


def render_powershell(ops):
    """İşlem listesini `WRITE_PRELUDE`in fonksiyonlarına çeviren renderer.

    Tanınmayan işlem **gürültüyle** düşer: sessizce atlanan bir işlem, yazımı
    eksik ama çıkış kodu 0 olan bir koşu üretirdi — bu deponun en pahalı
    hata sınıfı.
    """
    lines = []
    for op in ops:
        try:
            render = _POWERSHELL[op[0]]
        except KeyError:
            raise ValueError(f"PowerShell renderer'ında tanınmayan işlem: {op[0]!r}")
        lines.append(render(*op[1:]))
    return "\n".join(lines)


def bredr_ops(adapter, dev, link_key_hex):
    """Bir BR/EDR bond'unun ANAHTARINI yazan işlemler.

    Cihaz kaydı ayrı: → `device_record_ops`. Yalnız bu parça yazılırsa
    Windows cihazı `paired` gösterir ve link key ile bağlanır, ama profil
    devnode'ları doğmaz (ölçüldü 2026-09-03).
    """
    a, d = hex12(adapter), hex12(dev)
    return [
        (KEY, f"{KEYS}\\{a}"),
        (BIN, f"{KEYS}\\{a}", d, link_key_hex),
        (ECHO, f"OK bredr {d}"),
    ]


def bredr_script(adapter, dev, link_key_hex):
    """`bredr_ops`un PowerShell hâli — çağıranların yüzeyi değişmedi."""
    return render_powershell(bredr_ops(adapter, dev, link_key_hex))


REMOTE_NOTU = """Cihazdan HCI ile ÖĞRENİLEN alanlar — hiçbir BlueZ dosyasında yok.

Windows BR/EDR profil devnode'larını ancak bunları bildiğinde kuruyor; eskiden
elle yazılıyorlardı. Kaynakları `hcicapture` topluyor ve
`$XDG_STATE_HOME/btbond/remote-info.json`a biriktiriyor.

TİPLER ÖLÇÜLDÜ (2026-09-04, `win11-nvme` kovanı, üç cihaz): `LMPFeatures` ve
`HostSupportedFeaturesMap` **QWord**, `LmpVersion`/`LmpSubversion`/
`ManufacturerId` **DWord**.

`HostSupportedFeaturesMap` BEŞİNCİ alan ve sonradan bulundu (2026-09-04):
araç dördünü yazdı, devnode doğdu, ama A2DP sürücüsü bağlanmadı; çalışan bir
kurulumla diff alınınca eksik olan buydu. Kaynağı ölçüldü — `Read Remote
Extended Features` **sayfa 1** (`07 00 …` → 7, çalışan kurulumun değeriyle
birebir).

KAPSAM, ve ikisi ayrı: üç sürüm alanı LE cihazlarda da Windows'ta VAR, ama
`LMPFeatures` ve `HostSupportedFeaturesMap` LE'de YOK (fare ve Xbox
kayıtlarında o alanlar hiç geçmiyor) — o yüzden LE'de yazılmıyorlar. Sürüm
üçlüsünün LE profil kurulumunda GEREKLİ olup olmadığı ÖLÇÜLMEDİ; yazılıyor
çünkü Windows'un kendi yazdığı biçim bu.

Bilinmeyen alan YAZILMAZ: uydurulmuş bir sürüm numarası, eksik alandan kötü —
Windows onu cihazın gerçek yeteneği sanır."""

# BR/EDR'ye özel QWORD alanlar: Windows bunları LE cihaz kayıtlarında
# tutmuyor (ölçüldü), o yüzden LE'de yazılmazlar.
BREDR_ONLY_QWORDS = ("LMPFeatures", "HostSupportedFeaturesMap")


def remote_ops(dev_path, is_le, remote):
    """`remote` sözlüğündeki BİLİNEN alanları yazan işlemler → `REMOTE_NOTU`."""
    remote = remote or {}
    ops = []
    for field in ("LmpVersion", "LmpSubversion", "ManufacturerId"):
        if remote.get(field) is not None:
            ops.append((DW, dev_path, field, int(remote[field])))
    if not is_le:
        for field in BREDR_ONLY_QWORDS:
            if remote.get(field) is not None:
                ops.append((QW, dev_path, field, int(remote[field])))
    return ops


DYNAMIC_NOTU = """`DynamicCachedServices` — Windows'un servis DÜĞÜMLERİNİ açtığı liste.

ÖLÇÜLDÜ (2026-09-04, `win11-nvme`, Windows'un KENDİ eşleştirmesiyle yazılmış
altın kayıt ↔ aracın kaydı diff'i, 46 değer aynı): araç `CachedServices`i
BlueZ'in kayıtlarıyla dolduruyordu ve o altında da birebir aynı — ama Windows
servis düğümlerini ondan DEĞİL `DynamicCachedServices`ten açıyor. O yokken
Windows altı servisten yalnız sürücüsüz ikisi için düğüm açtı, A2DP sürücüsü
hiç bağlanmadı, link ~20 sn'de düştü; `EirData`, `FriendlyName`,
`HostSupportedFeaturesMap` ve enumerasyon dürtmesi tek tek elendi.

İÇERİK AYNI VERİ: BlueZ'in `cache/<mac>` `[ServiceRecords]`ı = Windows'un
SDP sorgusu; tek fark dış sarmalın uzunluk kodlaması —
    BlueZ    35 LL …     (DES, 8-bit uzunluk)
    Windows  36 00LL …   (DES, 16-bit uzunluk, big-endian)
Gövde ve iç sarmallar (`35 03 …`) DEĞİŞMİYOR. Beş gerçek kayıtta dönüşüm
altınla **bayt bayt** aynı çıktı (58→59, 61→62, 99→100, 77→78 bayt).

KAPSAM: yalnız 0x35 ile başlayan kayıt çevrilir; 0x36 ile başlayan zaten
Windows biçimidir ve olduğu gibi geçer; başka bir şey (0x37 = 32-bit uzunluk,
ya da SDP olmayan gövde) ÇEVRİLMEZ ve Dynamic'e YAZILMAZ — ölçülmemiş bir
sarmal uydurmaktansa düğüm açılmaması yeğdir. Bu alan `CachedServices`in
YERİNE değil YANINA yazılır: altın kayıtta ikisi de var."""


LE_SERVIS_NOTU = """LE'nin servis önbelleği REPLİKE EDİLMİYOR — ve sebebi bir eksik değil.

BR/EDR'de karşılık bir DEĞERDİ (`CachedServices`/`DynamicCachedServices`), o
yüzden yazılabiliyordu. LE'de Windows'un karşılığı bir değer değil **PnP düğüm
ağacı**: keşfedilen her birincil servis için üç ayrı yerde kayıt açılıyor —

    Enum\\BTHLEDevice\\{<servis-uuid>}_Dev_VID&…_PID&…_REV&…_<mac>
                      \\<ParentIdPrefix>&<başlangıç-handle>
    Control\\Class\\{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}\\<NNNN>   (sürücü örneği)
    Control\\DeviceClasses\\{<servis-uuid>}\\##?#BTHLEDevice#…        (arayüz)

artı düğüm başına DEVPKEY `Properties` alt ağacı. Yani `→ misafir` yönü bir
değer yazmak değil **devnode uydurmak** olurdu; bu dosyanın kendi ölçütü onu
zaten reddediyor (→ `DYNAMIC_NOTU`: "ölçülmemiş bir sarmal uydurmaktansa düğüm
açılmaması yeğdir").

ÖLÇÜLDÜ (2026-09-05, `win11-nvme` kovanının TAMAMI: 37.448 düğüm / 87.558
değer; Windows 11 10.0.26100.8972, `bthleenum.inf`; iki LE cihaz):

  - `AttributeCache` (sürücü örneği altında) VAR ama BOŞ — 2 bayt, tek adsız
    `String`, alt düğüm yok. Cihaz düğümüyle AYNI saniyede doğmuş ve bir daha
    yazılmamış. Yani bu yapı GATT tablosunu SAKLAMIYOR.
  - Düğüm kümesi ile BlueZ `cache/<mac>` `[Attributes]`ın `2800` (birincil
    servis) satırları **13/13** örtüşüyor, hem handle hem UUID: Xbox 6/6
    (0001/1800, 0008/1801, 0009/180a, 0012/180f, 0016/1812, 0024/vendor),
    fare 7/7. Instance ID'nin son dört hex hanesi servisin BAŞLANGIÇ
    handle'ıdır.
  - KATMAN ASİMETRİK, ve `→ host` yönünü bu kapatıyor: BlueZ servis +
    karakteristik + betimleyici tutuyor (Xbox 26 satır = 6+16+4, fare 55 =
    7+31+17), Windows yalnız servis katmanını (6 ve 7 düğüm). Yani Windows'ta
    BlueZ'in ihtiyacı olan satırların %23'ü ve %13'ü var — kalanı orada HİÇ
    yok, türetilemez de.
  - Ağacı Windows CANLI KEŞİFLE kuruyor: cihaz düğümü + altı servis düğümü
    Xbox'ta 20:27:03–20:27:07, farede 20:47:15–20:47:18 (3–4 sn'lik pencere),
    ve ikisi de aracın o misafire bond yazmasından (09-04 00:20:48) ÖNCE.

ÖLÇÜLMEDİ — ve tek açık kalan soru bu: Windows'un HİÇ görmediği bir LE cihazın
bond'u replike edildiğinde, Windows bağlanıp ağacı kendisi kurar mı? Kurarsa
replike edilecek bir şey yok; kurmazsa çare yine bir değer değil devnode
sentezidir. `win11` bu kolu cevaplamıyor: oraya yalnız BR/EDR kulaklık bond'u
yazılmış, LE bond'u hiç yok."""


def plain_record(record_hex):
    """`dynamic_record`ün TERSİ: Windows kaydını BlueZ biçimine çevir.

    BlueZ `cache/<mac>` `[ServiceRecords]` 8-bit uzunluklu dış sarmalı
    (`35 LL …`) kullanıyor; Windows `DynamicCachedServices` 16-bit'i
    (`36 00LL …`). Gövde aynı → `DYNAMIC_NOTU`.

    ÖLÇÜLDÜ (2026-09-05, misafirin kovanı ↔ host `cache/<mac>`, BR/EDR kulaklık):
    aynı cihazın beş kaydı iki tarafta da duruyor ve `CachedServices` baytları
    BlueZ'inkiyle **birebir aynı**; `DynamicCachedServices` yalnız sarmalda
    ayrılıyor (58↔59, 61↔62, 99↔100, 77↔78 bayt).

    KAPSAM ileri yönün aynası: yalnız `36 00LL` çevrilir, `35` olduğu gibi
    geçer, 16-bit uzunluk baytı gerçekten gövdeyi tarif etmiyorsa (ya da başka
    bir sarmal — `37`, SDP olmayan gövde) `None` döner ve kayıt YAZILMAZ.
    Ölçülmemiş bir sarmalı BlueZ'in önbelleğine sokmaktansa kayıt olmaması
    yeğdir: BlueZ o zaman SDP'yi yeniden sorar.

    Döner: hex dize (küçük harf), ya da çevrilemeyen şekilde `None`.
    """
    try:
        raw = bytes.fromhex(record_hex)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    if raw[0] == 0x35:
        return record_hex.lower() if raw[1] == len(raw) - 2 else None
    if raw[0] != 0x36 or len(raw) < 3:
        return None
    if int.from_bytes(raw[1:3], "big") != len(raw) - 3:
        return None
    body = raw[3:]
    if len(body) > 0xFF:
        # 8-bit sarmala sığmıyor: BlueZ biçimine çevrilemez.
        return None
    return (bytes([0x35, len(body)]) + body).hex()


def _is_handle(name):
    """`CachedServices` değer adı 8 haneli hex bir SDP handle'ıdır (`00010000`).

    ÖLÇÜLDÜ (2026-09-05): beş kaydın beşi `00010000`…`00010004`. Süzgeç var
    çünkü aynı düğüme başka bir ad eklenirse SDP gövdesi sanılmasın.
    """
    if len(name) != 8:
        return False
    try:
        int(name, 16)
    except ValueError:
        return False
    return True


def cached_service_records(tree):
    """Ölçülen ağaçtan cihaz başına SDP kayıtlarını çıkar.

    `collect` bunları düşürüyordu — ileri yönde gerek yoktu, çünkü kayıtların
    kaynağı host'un kendi `cache/`i. Ters yön (misafirde eşleştirilmiş bir
    cihazı host'a getirmek) onları host'ta HİÇ bulamıyor: `to-guest` o durumda
    *"BlueZ cache'inde SDP kaydı yok"* uyarısını basıyordu, yani boşluk zaten
    ölçülüydü.

    `CachedServices` önceliklidir çünkü BlueZ biçiminde (`35 LL`) duruyor;
    yoksa `DynamicCachedServices` `plain_record` ile çevrilir.

    Döner: {cihaz-mac: {"00010000": "<hex>", …}} — BlueZ'in beklediği gövde.
    """
    # İki kova ayrı toplanır: `tree` bir sözlük ve hangi düğümün önce
    # görüleceği garanti değil — önceliği sıraya bağlamak sessizce dönerdi.
    cached, dynamic = {}, {}
    for path, values in tree.items():
        parts = split_path(path)
        if len(parts) != 3 or parts[0] != "Devices":
            continue
        node = parts[2].lower()
        if node == "cachedservices":
            bucket = cached
        elif node == "dynamiccachedservices":
            bucket = dynamic
        else:
            continue
        mac = mac_from_hex12(parts[1])
        if not mac:
            continue
        for name, (kind, value) in values.items():
            if kind != "Binary" or not _is_handle(name):
                continue
            converted = plain_record(value)
            if converted:
                bucket.setdefault(mac, {})[name.lower()] = converted

    records = {}
    for mac in set(cached) | set(dynamic):
        merged = dict(dynamic.get(mac, {}))
        merged.update(cached.get(mac, {}))       # `CachedServices` önceliklidir
        if merged:
            records[mac] = merged
    return records


def dynamic_record(record_hex):
    """BlueZ SDP kaydını `DynamicCachedServices` biçimine çevir → `DYNAMIC_NOTU`.

    Döner: hex dize, ya da çevrilemeyen şekilde `None`.
    """
    try:
        raw = bytes.fromhex(record_hex)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    if raw[0] == 0x36:
        return record_hex.lower()
    if raw[0] != 0x35 or raw[1] != len(raw) - 2:
        return None
    return (bytes([0x36]) + raw[1].to_bytes(2, "big") + raw[2:]).hex()


def device_record_ops(adapter, dev, name, is_le, attrs, services, sdp_records=None,
                      le_flags=None, remote=None):
    """`Devices\\<mac>` + `ServicesFor<adaptör>` kaydını yazan işlemler.

    `attrs`: BR/EDR için {"COD": int}; LE için {"LEAppearance", "LEAddressType",
    "VID", "PID", "VIDType", "Version"} (olmayanlar atlanır).
    `services`: BR/EDR profil UUID'leri (LE'de kullanılmaz — Windows LE servis
    düğümlerini GATT keşfinden kuruyor, `ServicesFor` altında {uuid} yok).
    `remote`: cihazdan HCI ile öğrenilen alanlar → `REMOTE_NOTU`.
    """
    a, d = hex12(adapter), hex12(dev)
    dev_path = f"{DEVICES}\\{d}"
    svc_path = f"{dev_path}\\ServicesFor{a}"

    ops = [
        (KEY, dev_path),
        (DW, dev_path, "DibServiceVersion", DIB_SERVICE_VERSION),
    ]
    if name:
        ops.append((BIN, dev_path, "Name", name_blob(name)))
    ops += remote_ops(dev_path, is_le, remote)

    if is_le:
        if name:
            ops.append((BIN, dev_path, "LEName", name_blob(name)))
        ops.append((DW, dev_path, "LocalEvaldIoCapLE", LOCAL_EVALD_IO_CAP_LE))
        if attrs.get("LeContainerId"):
            ops.append((BIN, dev_path, "LeContainerId", attrs["LeContainerId"]))
            ops.append((DW, dev_path, "LeContainerIDSource", 1))
        for field in ("LEAppearance", "LEAddressType", "VID", "PID", "VIDType", "Version",
                      "LERemoteConnParamsIntervalMin", "LERemoteConnParamsIntervalMax",
                      "LERemoteConnParamsLatency", "LERemoteConnParamsLSTO"):
            if attrs.get(field) is not None:
                ops.append((DW, dev_path, field, int(attrs[field])))
        ops.append((KEY, svc_path))
        for field, value in sorted(LE_SERVICE_FLAGS.items()):
            ops.append((DW, svc_path, field, value))
        # `LEFlags` yalnız BİLİNİYORSA yazılıyor: sabit yazmak ölçülmüş
        # biçimde yanlış olurdu → `LEFLAGS_NOTU`.
        if le_flags is not None:
            ops.append((DW, svc_path, LEFLAGS_FIELD, int(le_flags)))
        ops.append((QW, svc_path, "LEExtendedDeviceInfoFlags", 0))
    else:
        ops.append((DW, dev_path, "LocalEvaldIoCap", LOCAL_EVALD_IO_CAP))
        if attrs.get("COD") is not None:
            ops.append((DW, dev_path, "COD", int(attrs["COD"])))
        ops.append((KEY, svc_path))
        for field, value in sorted(BREDR_SERVICE_FLAGS.items()):
            ops.append((DW, svc_path, field, value))
        ops.append((QW, svc_path, "BRExtendedDeviceInfoFlags", 0))
        if sdp_records:
            cached = f"{dev_path}\\CachedServices"
            ops.append((KEY, cached))
            for record_name, record_hex in sorted(sdp_records.items()):
                ops.append((BIN, cached, record_name, record_hex))
            # `DynamicCachedServices` — Windows'un servis DÜĞÜMLERİNİ açtığı
            # liste (→ `DYNAMIC_NOTU`). Aynı kayıtlar, yalnız dış sarmal farklı.
            dynamic = [(name, dynamic_record(record_hex))
                       for name, record_hex in sorted(sdp_records.items())]
            dynamic = [(name, rec) for name, rec in dynamic if rec]
            if dynamic:
                dyn_path = f"{dev_path}\\DynamicCachedServices"
                ops.append((KEY, dyn_path))
                for record_name, record_hex in dynamic:
                    ops.append((BIN, dyn_path, record_name, record_hex))
        for uuid in services:
            uuid_path = f"{svc_path}\\{{{uuid}}}"
            leaf = f"{uuid_path}\\C00000000"
            ops += [
                (KEY, uuid_path),
                (DW, uuid_path, "Instance", 1),
                (KEY, leaf),
                (ZEROS, leaf, "PriLangServiceName", PRI_LANG_SERVICE_NAME_LEN),
                (STR, leaf, "DeviceString", ""),
                (DW, leaf, "CounterInstanceId", 0),
                (DW, leaf, "Enabled", 1),
            ]

    ops.append((ECHO, f"OK record {d}"))
    return ops


def device_record_script(adapter, dev, name, is_le, attrs, services, sdp_records=None,
                         le_flags=None, remote=None):
    """`device_record_ops`un PowerShell hâli."""
    return render_powershell(device_record_ops(
        adapter, dev, name, is_le, attrs, services, sdp_records, le_flags, remote))


def le_ops(adapter, dev, bond):
    """Bir LE bond'unun ANAHTARINI yazan işlemler.

    `bond`: {"LTK": hex, "IRK": hex|None, "KeyLength": int, "EDIV": int,
             "ERand": int, "AddressType": int, "AuthReq": int,
             "CEntralIRKStatus": int}
    Cihaz kaydı ayrı → `device_record_ops`.
    """
    a, d = hex12(adapter), hex12(dev)
    path = f"{KEYS}\\{a}\\{d}"
    ops = [
        (KEY, f"{KEYS}\\{a}"),
        (KEY, path),
        (BIN, path, "LTK", bond["LTK"]),
    ]
    if bond.get("IRK"):
        ops.append((BIN, path, "IRK", bond["IRK"]))
    for field in ("KeyLength", "EDIV", "AddressType", "AuthReq", "CEntralIRKStatus"):
        ops.append((DW, path, field, int(bond[field])))
    for field in ("ERand", "Address"):
        ops.append((QW, path, field, int(bond[field])))

    # İmza anahtarları (CSRK) — bu makinede ÖLÇÜLMEDİ: iki test cihazının
    # ikisi de dağıtmıyor. Alan adları ve tipleri kullanıcının 2024'te
    # hazırladığı, çalıştığı bilinen ROG fare `.reg` dosyasından alındı.
    # Hangi yönün hangi ada karşılık geldiği (BlueZ `[LocalSignatureKey]` →
    # `CSRK`) türetim, ölçüm değil.
    if bond.get("CSRK"):
        ops.append((BIN, path, "CSRK", bond["CSRK"]))
        ops.append((DW, path, "OutboundSignCounter",
                    int(bond.get("OutboundSignCounter", 0))))
    if bond.get("CSRKInbound"):
        ops.append((BIN, path, "CSRKInbound", bond["CSRKInbound"]))
        ops.append((QW, path, "InboundSignCounter",
                    int(bond.get("InboundSignCounter", 0))))

    ops.append((ECHO, f"OK le {d}"))
    return ops


def le_script(adapter, dev, bond):
    """`le_ops`un PowerShell hâli."""
    return render_powershell(le_ops(adapter, dev, bond))


def remove_ops(adapter, dev, is_le):
    """Bir bond'u misafirden silen işlemler (geri alma için)."""
    a, d = hex12(adapter), hex12(dev)
    ops = []
    if is_le:
        ops.append((DEL_KEY, f"{KEYS}\\{a}\\{d}"))
    else:
        ops.append((DEL_VALUE, f"{KEYS}\\{a}", d))
    ops.append((DEL_KEY, f"{DEVICES}\\{d}"))
    ops.append((ECHO, f"OK removed {d}"))
    return ops


def remove_script(adapter, dev, is_le):
    """`remove_ops`un PowerShell hâli."""
    return render_powershell(remove_ops(adapter, dev, is_le))
