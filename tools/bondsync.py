"""İki tarafın bond durumunu tek modelde topla — **yönsüz**.

Bu modül replikasyon YAPMAZ: iki tarafı okur, cihaz cihaz karşılaştırır ve her
satırın hükmünü verir. Yürütme `btbond-sync.py`nin işi.

TASARIM KARARI — **yön satırın özelliğidir, oturumun değil.** "Nereden nereye"
diye baştan sorulan bir kip, cevabı ölçülebilen bir soruyu kullanıcıya
sorar ve tek gerçek belirsizlikte (aynı cihaz, iki tarafta, FARKLI anahtar)
yeniyi eskiyle sessizce ezer. Burada o satır `key-mismatch` diye işaretlenir
ve **hiçbir zaman kendiliğinden çözülmez**; kalan satırların yönü zaten
verinin kendisinden çıkar.

ÇIKTI JSON'A ÇEVRİLEBİLİR ve bilerek öyle: arayüz katmanı bu modeli ya import
eder (Python) ya da `--json` çıktısını ayrıştırır (başka dil). Arayüzün hangi
dille yazılacağı kararı böylece açık kalıyor.

GİZLİLİK: anahtar baytı hiçbir yere konmaz — karşılaştırma da rapor da
`winbond.fingerprint` (sha256'nın ilk 12 hex'i) üzerinden yürür. Model
JSON'a basıldığında da güvenlidir.
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bluezbond  # noqa: E402
import winbond  # noqa: E402
import agentexec  # noqa: E402
from agentexec import run_powershell  # noqa: E402

# Bu makinenin radyosu; başka makinede değişir, o yüzden CLI'dan geçilebiliyor.
DEFAULT_USB_ID = "8087:0032"
DEFAULT_DOMAIN = agentexec.DEFAULT_DOMAIN

# `/sys/class/bluetooth` yalnız adaptörü değil BAĞLANTI düğümlerini de
# listeliyor (ölçüldü: `hci0 hci0:256 hci0:2048`), yani "dizin boş mu"
# sorusu yanlış soru — adaptör deseni aranır.
HCI_RE = re.compile(r"^hci\d+$")

# Karşılaştırılan LE anahtarları: (etiket, BlueZ bölümü, Windows alan adı).
# İmza anahtarları `bluezbond.SIGNATURE_MAP`ten TÜRETİLİYOR ki eşleme tek
# yerde kalsın; elle tutulan liste sessizce karşılaştırılmayan bir anahtar
# bırakır — aynı hata `--verify`da bir kez yazıldı ve orada da türetime
# çevrildi.
LE_KEYS = (
    ("LTK", "LongTermKey", "LTK"),
    ("IRK", "IdentityResolvingKey", "IRK"),
) + tuple((key_field, section, key_field)
          for section, key_field, _ in bluezbond.SIGNATURE_MAP)

MATCH = "match"
HOST_ONLY = "host-only"
GUEST_ONLY = "guest-only"
KEY_MISMATCH = "key-mismatch"

# Hangi hüküm hangi yönü ima eder. `match` yapılacak iş olmadığı için,
# `key-mismatch` ise **belirsiz** olduğu için yönsüz.
VERDICT_DIRECTION = {
    HOST_ONLY: "to-guest",
    GUEST_ONLY: "to-host",
    MATCH: None,
    KEY_MISMATCH: None,
}


def other_domains(domain, uri="qemu:///system"):
    """`domain` dışında TANIMLI domain adları; ölçülemezse `None`.

    Yalnız kapsam cümlesi için: bu adların Windows olup olmadığı, hatta
    koşup koşmadığı burada sorulmuyor — sorulan şey "kaç taraf daha var".
    """
    try:
        proc = subprocess.run(["virsh", "-c", uri, "list", "--all", "--name"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return sorted(n for n in proc.stdout.split() if n and n != domain)


def radio_where(domain=DEFAULT_DOMAIN, usb_id=DEFAULT_USB_ID):
    """Radyo nerede? **İki bağımsız kanal**, ikisi de ayrı ayrı raporlanır.

    - host: `/sys/class/bluetooth` altında bir `hciN` düğümü var mı.
    - misafir: domain'in **canlı** XML'inde o USB kimliği hostdev olarak
      geçiyor mu (`vfioctl … usb --detach` sonrası XML'den düşüyor).

    İki kanal çelişirse bu **bildirilir**, biri seçilmez: "host'ta yok"
    tek başına "misafirde" demek değil — radyo hiçbir tarafta bağlı
    olmayabilir.

    KAPSAM (2026-09-04): misafir kanalı **yalnız adı verilen domain'i** okur,
    ve bu makinede üç Windows domain'i tanımlı. O yüzden olumsuz cevap
    kapsamını taşımak zorunda: eskiden "hiçbir tarafta bağlı değil" deniyordu
    ve bu, radyo BAŞKA bir domain'de iken de basılan bir cümleydi. Tek domain
    varken doğruydu — yanlışa dönmesi için ikinci bir domain'in radyoyu
    almasını beklemek yetiyordu. Kapsam kayıtla değil **ölçülerek** yazılıyor:
    sorulmayan domain sayısı sayılabiliyor, ve sıfırsa olumsuz gerçekten tam.

    Döner: `{"host": bool, "guest": bool|None, "others": list|None,
    "where": str}` — `guest` yalnız libvirt okunamadığında `None`, `others`
    domain listesi ölçülemediğinde `None`.
    """
    host = any(HCI_RE.match(p.name) for p in Path("/sys/class/bluetooth").glob("*")) \
        if Path("/sys/class/bluetooth").is_dir() else False

    guest = None
    vendor = usb_id.split(":")[0]
    try:
        xml = subprocess.run(
            ["virsh", "-c", "qemu:///system", "dumpxml", domain],
            capture_output=True, text=True, timeout=30)
        if xml.returncode == 0:
            guest = f"0x{vendor}" in xml.stdout.lower() or vendor in xml.stdout
    except (OSError, subprocess.SubprocessError):
        guest = None

    # Kapsam yalnız OLUMSUZ dalda ölçülüyor: radyonun yeri bulunduysa
    # sorulmayan tarafların sayısı hükmü değiştirmiyor, ve `virsh list`
    # bedava değil.
    others = None
    if not host and guest is False:
        others = other_domains(domain)

    if host and guest:
        where = "ÇELİŞKİ (iki kanal da 'burada' diyor)"
    elif host:
        where = "host"
    elif guest:
        where = f"misafir ({domain})"
    elif guest is None:
        where = f"host'ta değil; misafir ({domain}) kanalı OKUNAMADI"
    elif others is None:
        where = (f"host'ta değil, {domain}'de değil — başka domain tanımlı mı "
                 f"ÖLÇÜLEMEDİ")
    elif not others:
        where = (f"hiçbir tarafta bağlı değil (host + {domain} soruldu; "
                 f"başka domain tanımlı değil)")
    else:
        where = (f"host'ta değil, {domain}'de değil — SORULMAYAN "
                 f"{len(others)} domain daha tanımlı: {', '.join(others)}")
    return {"host": host, "guest": guest, "others": others, "where": where}


def _host_state(root, adapter):
    """Host'un bond'larını `{dev: {"name":…, "tech":…, "fp": {etiket: fp}}}` ver.

    Dosyalar radyo misafirdeyken de diskte durur, yani bu okuma adaptörün
    host'ta olmasını GEREKTİRMEZ (ölçüldü: bond'lar radyo misafirdeyken
    yazıldı ve okundu).
    """
    out = {}
    for dev, info in bluezbond.list_bonds(root, adapter).items():
        techs = bluezbond.technologies(info)
        prints = {}
        link = bluezbond.section_key(info, "LinkKey")
        if link:
            prints["LinkKey"] = winbond.fingerprint(link)
        for label, section, _ in LE_KEYS:
            key = bluezbond.section_key(info, section)
            if key:
                prints[label] = winbond.fingerprint(key)
        out[dev] = {
            "name": bluezbond.device_name(info, dev),
            "tech": "BR/EDR" if "BR/EDR" in techs else ("LE" if "LE" in techs else "?"),
            "fp": prints,
        }
    return out


def guest_state(entry, names):
    """Misafirin bond'larını host'la aynı biçime çevir.

    **Açık (2026-09-04):** taşıyıcıdan bağımsız — girdisi `winbond.collect`
    çıktısı, yani anahtarların ajandan mı offline kovandan mı geldiği burada
    sorulmuyor. `hivebond` bunu çağırıyor; ikinci bir kopya yazılsaydı
    biri ilerler, öbürü donardı.
    """
    out = {}
    for dev, link_key in entry["bredr"].items():
        out[dev] = {"name": names.get(dev, dev), "tech": "BR/EDR",
                    "fp": {"LinkKey": winbond.fingerprint(link_key)}}
    for dev, bond in entry["le"].items():
        prints = {label: winbond.fingerprint(bond[field])
                  for label, _, field in LE_KEYS if field in bond}
        # Aynı cihaz iki teknolojiyi birden taşıyabilir; LE kaydı BR/EDR'yi
        # ezmesin diye parmak izleri birleştiriliyor.
        row = out.setdefault(dev, {"name": names.get(dev, dev), "tech": "LE", "fp": {}})
        row["fp"].update(prints)
        if row["tech"] == "BR/EDR" and prints:
            row["tech"] = "BR/EDR+LE"
    return out


def _verdict(host_row, guest_row):
    """İki taraflı satırın hükmünü ve farkını ver.

    ÖLÇÜT: yalnız **iki tarafta da bulunan** anahtar etiketleri karşılaştırılır.
    Bir tarafta olmayan etiket eksiklik değil olabilir — Windows bazı cihazda
    `IRK` yazmıyor, BlueZ imza bölümünü yalnız cihaz dağıtırsa taşıyor
    (ikisi de ölçüldü). Kesişim boşsa hüküm verilemez ve öyle söylenir.
    """
    if host_row and not guest_row:
        return HOST_ONLY, []
    if guest_row and not host_row:
        return GUEST_ONLY, []
    shared = sorted(set(host_row["fp"]) & set(guest_row["fp"]))
    if not shared:
        return KEY_MISMATCH, ["karşılaştırılabilir ortak anahtar yok"]
    differing = [label for label in shared
                 if host_row["fp"][label] != guest_row["fp"][label]]
    return (KEY_MISMATCH, differing) if differing else (MATCH, [])


def survey(domain=DEFAULT_DOMAIN, root=bluezbond.ROOT, usb_id=DEFAULT_USB_ID):
    """İki tarafı oku, satır satır karşılaştır. Yön SEÇMEZ, hüküm verir."""
    exitcode, stdout, stderr = run_powershell(domain, winbond.DUMP_POWERSHELL)
    if exitcode != 0:
        raise RuntimeError(f"misafir okuma komutu exitcode={exitcode}\n{stderr}")
    adapters, names, devices = winbond.collect(winbond.parse_dump(stdout))

    host_adapters = bluezbond.list_adapters(root)
    # Hangi adaptör? Misafir ve host aynı radyoyu paylaştığı için normalde tek
    # ve aynı. Kesişim boşsa bu bir bulgudur, sessizce boş liste değil.
    common = [a for a in host_adapters if a in adapters]
    result = {
        "domain": domain,
        "radio": radio_where(domain, usb_id),
        "host_adapters": host_adapters,
        "guest_adapters": sorted(adapters),
        "adapter": common[0] if common else None,
        "rows": [],
        "warnings": [],
    }
    if not common:
        result["warnings"].append(
            f"host ({host_adapters or 'yok'}) ile misafirin ({sorted(adapters) or 'yok'}) "
            "adaptörü kesişmiyor — aynı radyo değil ya da bir taraf hiç okunmadı")
        return result

    adapter = common[0]
    host = _host_state(root, adapter)
    guest = guest_state(adapters[adapter], names)

    for dev in sorted(set(host) | set(guest)):
        host_row, guest_row = host.get(dev), guest.get(dev)
        verdict, differing = _verdict(host_row, guest_row)
        row = {
            "dev": dev,
            "name": (host_row or guest_row)["name"],
            "tech": (host_row or guest_row)["tech"],
            "host": host_row["fp"] if host_row else None,
            "guest": guest_row["fp"] if guest_row else None,
            "verdict": verdict,
            "differing": differing,
            "direction": VERDICT_DIRECTION[verdict],
            "address_type": devices.get(dev, {}).get("LEAddressType"),
        }
        result["rows"].append(row)
    return result


def actionable(rows, direction=None):
    """Yönü belli olan satırları ver; `direction` verilirse o yöne süz."""
    picked = [r for r in rows if r["direction"]]
    if direction:
        picked = [r for r in picked if r["direction"] == direction]
    return picked
