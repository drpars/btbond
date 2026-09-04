"""HCI'dan **uzak cihaz bilgisi** topla — komutu araç kendisi yollayarak.

NİYE ARAÇ YOLLUYOR: çekirdek `Read Remote Version Information`ı **hiç**
yollamıyor. Ölçüldü 2026-09-04, iki bağımsız kanaldan, bu makinenin
`bluetooth.ko`sunda (linux-zen 7.2.2): (a) opcode `0x041d` ikilide **sıfır**
kez geçiyor, kardeşleri geçerken — `0x041b` 2, `0x041c` 2, `0x0419` 5,
`0x0405` 2; (b) `read_remote_version` ailesinden **hiç sembol yok**, oysa
kardeşlerinin hepsi var (`hci_cs_read_remote_features`,
`hci_remote_features_evt`, `hci_cs_read_remote_ext_features`,
`hci_remote_ext_features_evt`) ve `read_LOCAL_version` de var. Yani olayın
ateşlememesi "bu koşullarda istemedi" değil, "bu kod yolu yok".

Kapsam: **bu modül**, bu sürüm, bu makine. Sembol yokluğu burada anlamlı,
çünkü olay işleyicileri bir işaretçi tablosundan çağrılıyor — inline edilip
kaybolamazlar; komutu YOLLAYAN taraf inline edilebilirdi, onu opcode kanalı
kapatıyor.

Çare ölçüldü ve iki olay için de çalışıyor: komut var olan bir bağlantıya
**elle** yollanınca (`hcitool cmd 0x01 0x1D|0x1B <handle>`) olay geliyor.
Yani yakalama artık devir penceresine bağlı DEĞİL — cihaz bağlıysa yeter
(ölçüldü 2026-09-04: fare + Xbox LE, Soundcore BR/EDR; üçünde de değerler
Windows kaydına birebir eşit). Devir hâlâ destekleniyor, ama zorunlu değil.

ESKİ GEREKÇE (yanlıştı, kayıt için): *"olaylar yalnız bağlantı kurulurken
geçer, o yüzden yakalama devrin içinde olmalı"*. Doğru yarısı şu — çekirdeğin
KENDİLİĞİNDEN yolladığı tek şey özellik komutu ve onu bağlantı kurulurken
yolluyor; yanlış yarısı, olayların **istenerek** de üretilebildiğini
görmemekti.

NİYE GEREKLİ: Windows, BR/EDR profil devnode'larını ancak cihazın neyi
desteklediğini bildiğinde kuruyor; o bilgi `Devices\\<mac>` altındaki dört
alanda (`LMPFeatures`, `ManufacturerId`, `LmpVersion`, `LmpSubversion`) ve
**hiçbir BlueZ dosyasında yok**. Ters yön onları bugün elle yazılmış
değerlerle dolduruyor — bu modül o boşluğun makineleşmiş hâli.

ÖLÇÜM DURUMU, ve ikisi ayrı:

- `LMPFeatures` **ölçüldü ve iki taraflı doğrulandı** (2026-09-04, Soundcore
  Life Q10): btmon `af fe 0d fe d8 bf 7b 87` bastı, little-endian okunuşu
  Windows'un aynı cihaz için yazdığı QWORD'e birebir eşit.
- Sürüm üçlüsü de **ölçüldü ve iki taraflı doğrulandı** (2026-09-04, üç cihaz,
  iki taşıyıcı). Host'un btmon'dan okuduğu değerler `win11-nvme` kovanındaki
  `Devices\\<mac>` kayıtlarına birebir eşit: fare `0x0a`/531/13 ↔ 10/531/13,
  Xbox `0x08`/4868/70 ↔ 8/4868/70, Soundcore `0x08`/12850/148 ↔ 8/12850/148.
  Ayrıştırıcı ilk gerçek olayda **iki yerden kırıldı** ve ikisi de düzeltildi
  (aşağıda `LMP_VERSION_RE` ve `parse(by_handle=…)`); ölü kodun bedeli buydu.

- LE cihazlarda da üç sürüm alanı Windows'ta **var** (yalnız `LMPFeatures`
  yok). Yani ihtiyaç BR/EDR'ye özel değil; bunların LE profil kurulumunda
  GEREKLİ olup olmadığı ayrıca **ölçülmedi**.

GİZLİLİK — **ham log her koşuda anahtar taşır**, ve bu ölçüldü (2026-09-04),
tahmin değil. Modülün *okuduğu* olaylar cihaz yeteneğidir, ama btmon MGMT
kanalını da basar ve bluetoothd adaptör açılışında bond'ları çekirdeğe
yüklerken `Load Link Keys` / `Load Long Term Keys` / `Load Identity Resolving
Keys` komutları `Key[16]:` altında **düz baytları** gösterir. Yakalama tam da
adaptörün kurulduğu ana denk geldiği için istisna değil **kural** bu.

Sonuç üç önlem: log **0600** açılır, ayrıştırmadan sonra **silinir**
(`keep_log=True` denmedikçe), ve `*.btmon.log` `.gitignore`dadır.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

# Hangi alanın BR/EDR'ye özel olduğu kayıt defteri DÜZENİNİN olgusu, o yüzden
# sahibi `winbond`; burada yalnız cümle kurmak için okunuyor. `winbond` bu
# modülü import etmiyor, yani döngü yok (ölçüldü: yalnız hashlib + uuid).
from . import winbond

# Olay başlığı: `> HCI Event: Read Remote Supported Features (0x0b) plen 11 …`
# AD DEĞİL KOD eşleştiriliyor, ve sebebi ölçüldü (2026-09-04): btmon satırı
# sabit genişlikte basıyor ve sondaki `#<n> [hciN] <süre>` uzayınca **olay
# adını kırpıyor** — aynı olay bir koşuda `Read Remote Supported Features`,
# başka bir koşuda `Read Remote Supported Featu..` olarak geliyor. Ada dayanan
# eşleme o yüzden koşuya göre sessizce ıskalıyordu: alan toplanmıyor, hata yok,
# rapor "eksik" diyor. Parantez içindeki kod kırpılmaz.
EVENT_RE = re.compile(r"^[<>]\s+HCI Event:\s+([^(]*?)\s*\((0x[0-9a-f]+)\)")
# Olay kodları — btmon'un adları değil, spesifikasyonun numaraları.
FEATURES_CODE = "0x0b"        # Read Remote Supported Features Complete
VERSION_CODE = "0x0c"         # Read Remote Version Information Complete
EXT_FEATURES_CODE = "0x23"    # Read Remote Extended Features Complete
LE_META_CODE = "0x3e"         # LE Meta Event (gerçek ad bir SONRAKİ satırda)
# Girintili alan: `        Handle: 256 (BR-ACL) Address: E8:07:… (…)`
FIELD_RE = re.compile(r"^\s{4,}([A-Za-z][A-Za-z ]*?):\s*(.*)$")
ADDRESS_RE = re.compile(r"Address:\s*([0-9A-Fa-f:]{17})")
HANDLE_RE = re.compile(r"^(\d+)")
# Bayt satırı: `        af fe 0d fe d8 bf 7b 87              ......{.`
BYTES_RE = re.compile(r"^\s{4,}((?:[0-9a-f]{2} ){7}[0-9a-f]{2})\b")
# LE olayları `LE Meta Event (0x3e)` başlığı altında gelir ve GERÇEK ad bir
# SONRAKİ satırdadır, 6 boşluk girintiyle: `      LE Read Remote Used Features
# (0x04)`. Alan satırları 8 boşlukta. Ölçüldü (2026-09-04) — bu yapı
# tanınmadığı için LE bağlantıları yakalamada görünmüyordu.
LE_META_RE = re.compile(r"^\s{4,8}(LE [A-Za-z0-9 #-]+?)\s+\(0x[0-9a-f]+\)\s*$")
LE_FEATURES_EVENT = re.compile(r"^LE Read Remote Used Features")
# LE bağlantı olayında adres alanının adı `Address` değil `Peer address`.
PEER_ADDRESS_RE = re.compile(r"[Pp]eer address:\s*([0-9A-Fa-f:]{17})")
# Blok sonlandırıcı: sütun 0'dan başlayan her satır önceki HCI olayını
# **bitirir**. Olmazsa araya giren MGMT satırları önceki olayın alanları gibi
# okunur ve `LE Address:` taşıdıkları için hayalet kayıt üretirler — ölçüldü
# (2026-09-04): gerçek bir yakalamada adaptörün kendi adresi dahil dört adres
# çıktı, dördü de alansız.
BLOCK_END_RE = re.compile(r"^\S")
# `        Features[0/0][8]:` — bu satır `FIELD_RE`ye TAKILMAZ (adında köşeli
# parantez ve rakam var), o yüzden ayrı işaretçi. Bir kez yanlış yazıldı ve
# sessizce boş sonuç verdi: adres çözülüyordu, özellikler hiç toplanmıyordu.
FEATURES_MARK_RE = re.compile(r"^\s{4,}Features\[")

# Olay adı TAM eşleşmeyle aranmıyor, **aile öneki** ile — çünkü btmon'un aynı
# olay için birden çok adı var ve hangisinin başlığa düştüğü ölçülmedi.
# İkilide duran adlar (`strings -a /usr/bin/btmon`): "Read Remote Supported
# Features" + "… Complete"; "Read Remote Version Information",
# "… Information Complete", "Read Remote Version Complete". Gerçek log'da
# özellik olayı `Complete`SİZ basıldı (ölçüldü), sürüm olayı hiç görülmedi.
# Önek eşleşmesi üçünü de kapsar, yani doğru adı bilmek gerekmiyor.
FEATURES_EVENT = re.compile(r"^Read Remote Supported Features")
VERSION_EVENT = re.compile(r"^Read Remote Version")
# `Read Remote Extended Features` AYRI bir olay ve SAYFALI. Ölçüldü 2026-09-04
# (Soundcore, BR/EDR): sayfa 0 `af fe 0d fe d8 bf 7b 87` — page-0 maskesinin
# aynısı, yani `LMPFeatures`; sayfa 1 `07 00 …` — SSP/LE HOST destekleri, ve
# little-endian okunuşu (7) Windows'un aynı cihaz için yazdığı
# `HostSupportedFeaturesMap`e **birebir eşit**; sayfa 2 tamamen sıfır.
EXT_FEATURES_EVENT = re.compile(r"^Read Remote Extended Features")
# Sürüm olayında btmon İKİ alanı TEK satırda basıyor, ve alan adı `LMP version`:
#     LMP version: Bluetooth 5.1 (0x0a) - Subversion 531 (0x0213)
# Ölçüldü 2026-09-04, üç cihaz / iki taşıyıcı; biçim üçünde de aynı. Eski kod
# ayrı `Version`/`Subversion` alanları arıyordu, yani olay ateşlese bile yalnız
# `Manufacturer` toplanırdı — ve `_num` SON parantezi aldığı için satırı düz
# eşlemek `lmp_version`e ALT SÜRÜMÜ (531) yazardı, yani sessizce yanlış.
LMP_VERSION_RE = re.compile(
    r"\((0x[0-9a-f]+)\)\s*-\s*Subversion\s+\d+\s+\((0x[0-9a-f]+)\)")
# `hcitool con` satırı: `\t< LE D4:4C:… handle 2048 state 1 lm CENTRAL AUTH …`
CON_RE = re.compile(r"^\s*[<>]\s+(\S+)\s+([0-9A-Fa-f:]{17})\s+handle\s+(\d+)", re.M)

# Windows `Devices\<mac>` alan adları ↔ burada toplanan anahtarlar.
WINDOWS_FIELDS = {
    "LMPFeatures": "lmp_features",
    "LmpVersion": "lmp_version",
    "ManufacturerId": "manufacturer",
    "LmpSubversion": "lmp_subversion",
    # BEŞİNCİ ALAN (2026-09-04): çalışan bir kurulumla diff alınınca çıktı —
    # `win11-nvme`de var, aracın yazdığı kayıtta yoktu. Kaynağı ölçüldü:
    # `Read Remote Extended Features` **sayfa 1**. BR/EDR'ye özel, `LMPFeatures`
    # gibi (LE cihazların Windows kaydında bu alan da YOK).
    "HostSupportedFeaturesMap": "host_features",
}


def _num(text):
    """`Bluetooth 5.0 (0x09)` → 9; `Intel Corp. (2)` → 2; `0x1012` → 4114.

    Parantezli son grup varsa o alınır (btmon adı önce, sayıyı sonra yazıyor);
    yoksa dizenin kendisi. Taban `0x` önekinden anlaşılır.
    """
    match = re.findall(r"\(([^)]*)\)", text)
    raw = (match[-1] if match else text).strip()
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
    except ValueError:
        return None


def parse(text, by_handle=None):
    """btmon metnini `{BD_ADDR: {alan: değer}}` sözlüğüne çevir.

    Handle → adres eşlemesi **log boyunca** taşınır: sürüm olayı adresi
    basmıyor olabilir (yalnız handle), o yüzden adresi basan herhangi bir
    önceki olaydan çözülür.

    `by_handle` TOHUMU zorunlu oldu, ve sebebi ölçüldü (2026-09-04): var olan
    bir bağlantıya komut yollandığında yakalamanın içinde `Connect Complete`
    **geçmez**, yani eşleme boş kalır ve dolu bir sürüm olayı sessizce
    ATILIR — `parse` boş sözlük döndürür, hata yok, çıkış kodu 0. Tohum
    `connections()`tan gelir. Yakalama devrin içindeyse bağlantılar zaten
    yakalamada doğduğu için tohum gereksizdir; ikisi birlikte de zararsız,
    log'daki gerçek olay tohumu EZER (aynı handle yeniden atanmış olabilir).
    """
    by_handle = dict(by_handle or {})
    out = {}
    event = None
    code = None
    fields = {}
    handle = None

    def flush():
        if not code or handle is None:
            return
        address = by_handle.get(handle)
        if not address:
            return
        # Veri yoksa kayıt AÇILMAZ: adres taşıyan her olay (bağlantı, kopma…)
        # boş bir satır üretirdi ve rapor "cihaz bulundu, alan yok" diye
        # okunurdu — oysa o cihaz hiç sorgulanmamış olabilir.
        found = {}
        if code == FEATURES_CODE and "features" in fields:
            # HCI baytları LSB'den gelir; Windows QWORD'ü little-endian
            # saklıyor (ÖLÇÜLDÜ, Soundcore: birebir eşleşti).
            found["lmp_features"] = int.from_bytes(fields["features"], "little")
        elif code == EXT_FEATURES_CODE and "features" in fields:
            # SAYFA NUMARASI hangi maske olduğunu söyler (`Page: 1/2`). Sayfa
            # numarasını okumadan bu olayı `lmp_features`a yazmak, host
            # desteklerini cihaz özellikleri sanmak olurdu.
            page = (fields.get("Page") or "").split("/")[0].strip()
            value = int.from_bytes(fields["features"], "little")
            if page == "0":
                found["lmp_features"] = value      # sayfa 0 == LMPFeatures
            elif page == "1":
                found["host_features"] = value
            # Sayfa 2+ toplanmıyor: bu cihazda tamamen sıfırdı ve Windows
            # karşılığı **aranmadı** — uydurmaktansa boş bırakılıyor.
        elif (code == LE_META_CODE and LE_FEATURES_EVENT.match(event or "")
                and "features" in fields):
            # LE özellik maskesi AYRI anahtarda tutuluyor ve Windows alanlarına
            # EŞLENMİYOR: farenin `Devices\<mac>` kaydında `LMPFeatures` alanı
            # hiç YOK (ölçüldü), yani onu oraya yazmak uydurma olurdu. Kayda
            # geçiyor çünkü bedava ve LE tarafını anlamaya yarar.
            found["le_features"] = int.from_bytes(fields["features"], "little")
        elif code == VERSION_CODE:
            for key, name in (("lmp_version", "Version"),
                              ("manufacturer", "Manufacturer"),
                              ("lmp_subversion", "Subversion")):
                if name in fields:
                    found[key] = _num(fields[name])
            # ÖLÇÜLEN biçim bu (→ `LMP_VERSION_RE`); yukarıdaki ayrı alanlar
            # hiçbir gerçek log'da görülmedi ve yalnız yedek olarak duruyor,
            # o yüzden ölçülen okuma onları EZER.
            combined = LMP_VERSION_RE.search(fields.get("LMP version", ""))
            if combined:
                found["lmp_version"] = _num(combined.group(1))
                found["lmp_subversion"] = _num(combined.group(2))
        if found:
            out.setdefault(address, {}).update(found)

    for line in text.splitlines():
        header = EVENT_RE.match(line)
        if header:
            flush()
            event, code = header.group(1).strip(), header.group(2).lower()
            fields, handle = {}, None
            continue
        # `LE Meta Event` altındaki gerçek ad bir SONRAKİ satırda; kod (0x3e)
        # her LE olayı için aynı olduğundan orada ad okumak zorunlu.
        if code == LE_META_CODE:
            sub = LE_META_RE.match(line)
            if sub:
                event = sub.group(1).strip()
                continue
        if BLOCK_END_RE.match(line):
            flush()
            event, code, fields, handle = None, None, {}, None
            continue
        if code is None:
            continue

        if FEATURES_MARK_RE.match(line):
            fields["__features_next"] = True
            continue
        byte_line = BYTES_RE.match(line)
        if byte_line and fields.pop("__features_next", False):
            fields["features"] = bytes.fromhex(byte_line.group(1).replace(" ", ""))
            continue

        field = FIELD_RE.match(line)
        if not field:
            continue
        name, value = field.group(1).strip(), field.group(2).strip()
        fields[name] = value
        if name == "Handle":
            digits = HANDLE_RE.match(value)
            handle = int(digits.group(1)) if digits else None
        address = ADDRESS_RE.search(line) or PEER_ADDRESS_RE.search(line)
        if address and handle is not None:
            by_handle[handle] = address.group(1).upper()

    flush()
    return out


REMOTE_INFO_NAME = "remote-info.json"


def state_path(name=REMOTE_INFO_NAME):
    """Çağıran kullanıcının durum dizini — `sudo` altında root'un evi DEĞİL.

    Yakalama root koşuyor; `~` genişletmesi `/root`a gider ve dosya
    kullanıcının göremeyeceği bir yere düşerdi.
    """
    user = os.environ.get("SUDO_USER")
    home = Path(f"~{user}").expanduser() if user else Path.home()
    base = os.environ.get("XDG_STATE_HOME") or (home / ".local/state")
    return Path(base) / "btbond" / name


def give_back(path):
    """`sudo` altında oluşturulan yolu çağıran kullanıcıya devret.

    ÖLÇÜLDÜ (2026-09-04): yol kullanıcının evine çözülüyor ama `mkdir`/`open`
    root koştuğu için dizin de dosya da **root'un** oluyordu
    (`drwxr-xr-x root root`) — kullanıcı kendi durum dizinini yönetemiyor,
    silemiyor. Yol doğru, sahiplik yanlıştı.
    """
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid is None or gid is None:
        return
    try:
        os.chown(path, int(uid), int(gid))
    except OSError:
        pass


def load_remote_info(path=None):
    """Biriktirilmiş uzak cihaz bilgisini oku: `{BD_ADDR: {Windows alanı: …}}`.

    Dosya YOKSA boş sözlük döner ve bu bir hata değil — alanlar o zaman
    yazılmaz (→ `winbond.REMOTE_NOTU`). Adresler büyük harfe normalize
    ediliyor: `bluezbond` MAC'leri büyük harfle veriyor, dosya elle
    düzenlenmiş olabilir.
    """
    path = Path(path) if path else state_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {address.upper(): fields for address, fields in data.items()}


def connections():
    """Canlı ACL/LE bağlantıları: `[(handle, BD_ADDR, tür)]`.

    İki işi var, ve ikincisi olmadan birincisi işe yaramaz: komutun
    yollanacağı handle'ları vermek, ve `parse`ın handle→adres eşlemesini
    TOHUMLAMAK (→ `parse` docstring'i).

    `hcitool` yoksa boş liste döner — çağıran bunu sessiz geçmesin diye
    `request_remote_info` ayrıca söylüyor.
    """
    tool = shutil.which("hcitool")
    if not tool:
        return []
    proc = subprocess.run([tool, "con"], capture_output=True, text=True,
                          timeout=30)
    return [(int(handle), address.upper(), kind)
            for kind, address, handle in CON_RE.findall(proc.stdout)]


def request_remote_info(cons, log=print):
    """Her bağlantı için özellik + sürüm komutunu **elle** yolla.

    Çekirdek sürüm komutunu hiç yollamıyor (→ modül başlığı), özellik
    komutunu ise yalnız bağlantı kurulurken yolluyor. İkisi de var olan bir
    bağlantıda istenince ateşliyor — ölçüldü 2026-09-04, Soundcore BR/EDR'de
    `0x1B` `af fe 0d fe d8 bf 7b 87` verdi (Windows QWORD'üne birebir eşit)
    ve `0x1D` `0x08`/12850/148 verdi.

    KAPSAM: `0x041b` (`Read Remote Supported Features`) **BR/EDR'ye özel**;
    LE karşılığı ayrı bir komut (`LE Read Remote Features`, 0x2016) ve
    zaten hiçbir Windows alanına eşlenmiyor (fare kaydında `LMPFeatures`
    YOK — ölçüldü), o yüzden LE bağlantılarında istenmiyor. LE'de yalnız
    sürüm sorulur, ve o üçlü LE'de de Windows'ta duruyor.

    Döner: yollanan komut sayısı.
    """
    tool = shutil.which("hcitool")
    if not tool:
        log("  [hci] `hcitool` YOK — sürüm üçlüsü toplanamaz. Bu çekirdek "
            "komutu kendiliğinden yollamıyor, yani alan sessizce eksik kalır. "
            "Paket: bluez-deprecated-tools (bluez ile aynı sürüm).")
        return 0
    sent = 0
    for handle, address, kind in cons:
        lo, hi = handle & 0xFF, (handle >> 8) & 0xFF
        # LE'de özellik komutları atlanıyor (yukarıdaki KAPSAM notu); BR/EDR'de
        # `0x1C` SAYFA 1 ile isteniyor — `HostSupportedFeaturesMap`in kaynağı o.
        if kind.upper() == "LE":
            requests = [("0x1D", [])]
        else:
            requests = [("0x1B", []), ("0x1C", ["0x01"]), ("0x1D", [])]
        for ocf, extra in requests:
            proc = subprocess.run([tool, "cmd", "0x01", ocf,
                                   f"0x{lo:02x}", f"0x{hi:02x}"] + extra,
                                  capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                sent += 1
            else:
                log(f"  [hci] {address} ({kind}) {ocf}: komut düştü "
                    f"(rc={proc.returncode})")
            # Olay ~1 ms sonra geliyor (ölçüldü), ama btmon'un satırı dosyaya
            # düşmesi bekleniyor: yakalama HEMEN durdurulursa son olay kaybolur.
            time.sleep(0.3)
    return sent


class Capture:
    """btmon'u arka planda koştur; `stop()` metni verir.

    Süreç **kendi oturumunda** başlatılıp grup olarak sonlandırılıyor: bu
    depoda ölçülmüş tuzak, işi durdurmanın çocuğunu bırakabilmesi. btmon'un
    çocuğu yok ama kalıp aynı tutuluyor, ve `timeout` bir üst sınır olarak
    ayrıca sarıyor — yakalama unutulursa kendiliğinden biter.
    """

    def __init__(self, path, limit_seconds=180, keep_log=False):
        self.path = path
        self.limit = limit_seconds
        self.keep_log = keep_log
        self.proc = None
        self.handle = None

    def start(self):
        # 0600: log bond anahtarlarını içeriyor (yukarıda ölçüldü). Varsayılan
        # umask altında 0644 açılırdı.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self.handle = os.fdopen(fd, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            ["timeout", str(self.limit), "btmon"],
            stdout=self.handle, stderr=subprocess.STDOUT, start_new_session=True)
        # btmon'un monitör soketini açması ve ilk satırı basması zaman alır;
        # devir ondan önce başlarsa olaylar kaçar — kaçtığında hata yok,
        # yalnız boş sonuç olur, o yüzden bekleme burada.
        deadline = time.time() + 5
        while time.time() < deadline and os.path.getsize(self.path) == 0:
            time.sleep(0.2)
        return self

    def stop(self, settle_seconds=0):
        """Cihazların bağlanmasına süre tanı, sonra durdur ve metni ver."""
        if settle_seconds:
            time.sleep(settle_seconds)
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        if self.handle:
            self.handle.close()
        with open(self.path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        if not self.keep_log:
            os.unlink(self.path)
        return text


def summary(parsed, kinds=None):
    """İnsan için satırlar + neyin EKSİK olduğunun açıkça söylenmesi.

    `kinds` (`{BD_ADDR: "LE"|"ACL"}`) verilirse eksiklik cümlesi kapsamını
    taşır: LE cihazlarda `LMPFeatures` Windows'ta **zaten yok** (ölçüldü —
    fare ve Xbox kayıtlarında o alan hiç geçmiyor), yani onu "eksik" diye
    basmak var olmayan bir kusuru rapor eder.
    """
    if not parsed:
        return ["HCI yakalaması boş — hiç bağlantı kurulmadı ya da btmon geç başladı "
                "(sonda: log'un başında `Connect Complete` var mı)."]
    lines = []
    for address, entry in sorted(parsed.items()):
        got = [f"{win}={entry[key]}" for win, key in WINDOWS_FIELDS.items()
               if key in entry]
        # Windows'a EŞLENMEYEN ama toplanan alanlar da yazılır, yoksa dolu bir
        # yakalama "(alan yok)" diye görünür ve boş bir turdan ayırt edilemez.
        extra = [f"{key}={value}" for key, value in sorted(entry.items())
                 if key not in WINDOWS_FIELDS.values()]
        lines.append(f"  {address}  {', '.join(got + extra) or '(alan yok)'}")
        missing = [win for win, key in WINDOWS_FIELDS.items() if key not in entry]
        if missing:
            note = ""
            kind = (kinds or {}).get(address, "").upper()
            if kind == "LE" and set(missing) <= set(winbond.BREDR_ONLY_QWORDS):
                lines.append(f"{'':<21}{', '.join(missing)} yok — LE bağlantı, ve "
                             f"bu alanlar Windows'ta LE cihazlar için ZATEN YOK "
                             f"(ölçüldü); eksiklik değil.")
                continue
            if "LmpVersion" in missing:
                note = ("  — sürüm olayı gelmedi; bu çekirdek komutu "
                        "KENDİLİĞİNDEN hiç yollamıyor (ölçüldü: ikilide "
                        "`0x041d` sıfır kez), yani onu aracın istemesi "
                        "gerekiyor → `hcitool` kurulu mu?")
            lines.append(f"{'':<21}Windows alanı eksik: {', '.join(missing)}{note}")
    return lines


def to_windows_fields(entry):
    """Toplanan kaydı Windows alan adlarına çevir (yalnız dolu olanlar)."""
    return {win: entry[key] for win, key in WINDOWS_FIELDS.items() if key in entry}
