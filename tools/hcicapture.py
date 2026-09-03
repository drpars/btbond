"""Radyo devri sırasında HCI'dan **uzak cihaz bilgisi** topla.

NİYE DEVİRDE: `Read Remote Supported Features` ve `Read Remote Version
Information` yalnız bağlantı **kurulurken** geçer. Radyo host'a döndüğünde
adaptör sıfırdan kurulur ve bütün cihazlar taze bağlanır — yakalamanın doğal
anı orasıdır. Bağlantı kurulduktan **sonra** başlatılan btmon hiçbir şey
görmez: ölçüldü (2026-09-04), log'un ilk paketi zaten `Handle 256` idi ve
`Exit Sniff Mode` geçiyordu, yani ACL ayaktaydı ve `bluetoothctl connect`
yalnız profil katmanını uyandırdı.

NİYE GEREKLİ: Windows, BR/EDR profil devnode'larını ancak cihazın neyi
desteklediğini bildiğinde kuruyor; o bilgi `Devices\\<mac>` altındaki dört
alanda (`LMPFeatures`, `ManufacturerId`, `LmpVersion`, `LmpSubversion`) ve
**hiçbir BlueZ dosyasında yok**. Ters yön onları bugün elle yazılmış
değerlerle dolduruyor — bu modül o boşluğun makineleşmiş hâli.

ÖLÇÜM DURUMU, ve ikisi ayrı:

- `LMPFeatures` **ölçüldü ve iki taraflı doğrulandı** (2026-09-04, Soundcore
  Life Q10): btmon `af fe 0d fe d8 bf 7b 87` bastı, little-endian okunuşu
  Windows'un aynı cihaz için yazdığı QWORD'e birebir eşit.
- Sürüm üçlüsünün ayrıştırıcısı **gerçek bir olay görmedi.** O olay ölçülen
  kopar-kur turunda ateşlemedi (çekirdek koşulsuz istemiyor), yani buradaki
  alan adları btmon'un biçiminden yazıldı, ölçümden değil. `summary()` bunu
  saklamaz: özellikler gelip sürüm gelmediyse **söyler**.

GİZLİLİK, ve burada ayrım ince: bu modülün **okuduğu** olaylar cihaz yeteneği
taşır, bond anahtarı değil. Ama bıraktığı **ham log** öyle değil — yakalama
bir **eşleştirmeye** denk gelirse anahtar dağıtımı HCI'dan geçer ve log'a
girer, ve dosya adı iki durumu ayırt etmez. O yüzden `*.btmon.log`
`.gitignore`da, ve yakalama penceresi devirle sınırlı tutuluyor.
"""

import os
import re
import signal
import subprocess
import time

# Olay başlığı: `> HCI Event: Read Remote Supported Features (0x0b) plen 11 …`
EVENT_RE = re.compile(r"^[<>]\s+HCI Event:\s+([^(]+?)\s+\(0x[0-9a-f]+\)")
# Girintili alan: `        Handle: 256 (BR-ACL) Address: E8:07:… (…)`
FIELD_RE = re.compile(r"^\s{4,}([A-Za-z][A-Za-z ]*?):\s*(.*)$")
ADDRESS_RE = re.compile(r"Address:\s*([0-9A-Fa-f:]{17})")
HANDLE_RE = re.compile(r"^(\d+)")
# Bayt satırı: `        af fe 0d fe d8 bf 7b 87              ......{.`
BYTES_RE = re.compile(r"^\s{4,}((?:[0-9a-f]{2} ){7}[0-9a-f]{2})\b")
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

# Windows `Devices\<mac>` alan adları ↔ burada toplanan anahtarlar.
WINDOWS_FIELDS = {
    "LMPFeatures": "lmp_features",
    "LmpVersion": "lmp_version",
    "ManufacturerId": "manufacturer",
    "LmpSubversion": "lmp_subversion",
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


def parse(text):
    """btmon metnini `{BD_ADDR: {alan: değer}}` sözlüğüne çevir.

    Handle → adres eşlemesi **log boyunca** taşınır: sürüm olayı adresi
    basmıyor olabilir (yalnız handle), o yüzden adresi basan herhangi bir
    önceki olaydan çözülür.
    """
    by_handle = {}
    out = {}
    event = None
    fields = {}
    handle = None

    def flush():
        if not event or handle is None:
            return
        address = by_handle.get(handle)
        if not address:
            return
        entry = out.setdefault(address, {})
        if FEATURES_EVENT.match(event) and "features" in fields:
            # HCI baytları LSB'den gelir; Windows QWORD'ü little-endian
            # saklıyor (ÖLÇÜLDÜ, Soundcore: birebir eşleşti).
            entry["lmp_features"] = int.from_bytes(fields["features"], "little")
        elif VERSION_EVENT.match(event):
            for key, name in (("lmp_version", "Version"),
                              ("manufacturer", "Manufacturer"),
                              ("lmp_subversion", "Subversion")):
                if name in fields:
                    entry[key] = _num(fields[name])

    for line in text.splitlines():
        header = EVENT_RE.match(line)
        if header:
            flush()
            event, fields, handle = header.group(1).strip(), {}, None
            continue
        if event is None:
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
        address = ADDRESS_RE.search(line)
        if address and handle is not None:
            by_handle[handle] = address.group(1).upper()

    flush()
    return out


class Capture:
    """btmon'u arka planda koştur; `stop()` metni verir.

    Süreç **kendi oturumunda** başlatılıp grup olarak sonlandırılıyor: bu
    depoda ölçülmüş tuzak, işi durdurmanın çocuğunu bırakabilmesi. btmon'un
    çocuğu yok ama kalıp aynı tutuluyor, ve `timeout` bir üst sınır olarak
    ayrıca sarıyor — yakalama unutulursa kendiliğinden biter.
    """

    def __init__(self, path, limit_seconds=180):
        self.path = path
        self.limit = limit_seconds
        self.proc = None
        self.handle = None

    def start(self):
        self.handle = open(self.path, "w", encoding="utf-8", errors="replace")
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
            return handle.read()


def summary(parsed):
    """İnsan için satırlar + neyin EKSİK olduğunun açıkça söylenmesi."""
    if not parsed:
        return ["HCI yakalaması boş — hiç bağlantı kurulmadı ya da btmon geç başladı "
                "(sonda: log'un başında `Connect Complete` var mı)."]
    lines = []
    for address, entry in sorted(parsed.items()):
        got = ", ".join(f"{win}={entry[key]}" for win, key in WINDOWS_FIELDS.items()
                        if key in entry)
        missing = [win for win, key in WINDOWS_FIELDS.items() if key not in entry]
        lines.append(f"  {address}  {got or '(alan yok)'}")
        if missing:
            lines.append(f"{'':<21}eksik: {', '.join(missing)}"
                         + ("  — sürüm olayı ateşlemedi (çekirdek koşulsuz istemiyor)"
                            if "LmpVersion" in missing else ""))
    return lines


def to_windows_fields(entry):
    """Toplanan kaydı Windows alan adlarına çevir (yalnız dolu olanlar)."""
    return {win: entry[key] for win, key in WINDOWS_FIELDS.items() if key in entry}
