#!/usr/bin/env python3
"""SDP önbelleği, kovan yedeği ve keşif sözleşmeleri — root GEREKTİRMEZ.

Buradaki dört sözleşmenin ortak yanı: bozulduklarında **hata vermezler**.
Ters çevrilmiş bir SDP sarmalı BlueZ'e sessiz bir ayrıştırma hatası olarak
gider, alınmamış bir kovan yedeği ancak kovan bozulunca fark edilir, kaçış
çözülmemiş bir mount noktası yanlış diske bakar, ve düşen bir rol-LTK bölümü
zaten sessizdi.

    tests/test_servicecache.py

MAKİNEYE ÖZEL KİMLİK YOK: MAC'ler uydurma, SDP gövdeleri sentetik. Depo public.
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from btbond import bluezbond  # noqa: E402
from btbond import hivebond  # noqa: E402
from btbond import sidemount  # noqa: E402
from btbond import winbond  # noqa: E402

OK = FAIL = 0

ADAPTER = "00:11:22:33:44:55"
DEV = "AA:BB:CC:DD:EE:FF"
DEV_HEX = "aabbccddeeff"


def check(label, got, want):
    global OK, FAIL
    if got == want:
        OK += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [HATA] {label}: {got!r} != {want!r}")


def body(n):
    """`n` baytlık sentetik SDP gövdesi (256'nın üstünde de üretebilmeli)."""
    return bytes((0x40 + i) % 256 for i in range(n)).hex()


def des8(payload_hex):
    """BlueZ sarmalı: `35 LL <gövde>`."""
    raw = bytes.fromhex(payload_hex)
    return (bytes([0x35, len(raw)]) + raw).hex()


def des16(payload_hex):
    """Windows sarmalı: `36 00LL <gövde>`."""
    raw = bytes.fromhex(payload_hex)
    return (bytes([0x36]) + len(raw).to_bytes(2, "big") + raw).hex()


print("=== winbond.plain_record: Windows sarmalı -> BlueZ sarmalı ===")
# ÖLÇÜLDÜ (2026-09-05, `win11-nvme` ↔ host `cache/`): gövde iki tarafta AYNI,
# yalnız dış sarmalın uzunluk kodlaması ayrılıyor. Beş gerçek kayıtta boy farkı
# tam olarak 1 bayttı (58↔59, 61↔62, 99↔100, 77↔78).
for n in (56, 59, 97, 75):
    plain, dynamic = des8(body(n)), des16(body(n))
    check(f"{n} baytlık gövde: dynamic -> plain", winbond.plain_record(dynamic), plain)
    check(f"{n} baytlık gövde: boy farkı 1", len(dynamic) // 2 - len(plain) // 2, 1)

check("zaten BlueZ biçimindeyse olduğu gibi geçer",
      winbond.plain_record(des8(body(20))), des8(body(20)))
check("büyük harf girdi küçük harfe iner",
      winbond.plain_record(des16(body(8)).upper()), des8(body(8)))

print("\n--- gidiş-dönüş: iki dönüşüm birbirinin TERSİ ---")
for n in (1, 16, 56, 200, 255):
    plain = des8(body(n))
    check(f"{n} bayt: plain -> dynamic -> plain",
          winbond.plain_record(winbond.dynamic_record(plain)), plain)

print("\n--- çevrilemeyen şekiller YAZILMAZ (None) ---")
# Ölçülmemiş bir sarmalı BlueZ önbelleğine sokmaktansa kayıt olmaması yeğdir:
# kayıt yoksa BlueZ SDP'yi yeniden sorar, bozuk kayıt sessizce yanlış cevaplar.
check("32-bit uzunluk sarmalı (0x37)", winbond.plain_record("3700000004" + body(4)), None)
check("SDP olmayan gövde", winbond.plain_record("00" * 8), None)
check("hex değil", winbond.plain_record("zzzz"), None)
check("çok kısa", winbond.plain_record("35"), None)
check("16-bit uzunluk gövdeyi tarif etmiyor",
      winbond.plain_record("36" + "0099" + body(4)), None)
check("8-bit uzunluk gövdeyi tarif etmiyor",
      winbond.plain_record("35" + "99" + body(4)), None)
# 8-bit sarmala sığmayan gövde: 256 bayt. Uydurup kırpmak yerine düşürülür.
check("256 baytlık gövde 8-bit sarmala sığmıyor",
      winbond.plain_record(des16(body(256))), None)

print("\n=== winbond.cached_service_records: ağaçtan çıkarım ===")
BASE = r"\SYSTEM\ControlSet001\Services\BTHPORT\Parameters"
plain_a, dyn_b = des8(body(10)), des16(body(12))
tree = {
    f"{BASE}\\Devices\\{DEV_HEX}\\CachedServices": {
        "00010000": ("Binary", plain_a),
        "SonSeen": ("QWord", "123"),                 # handle değil -> atlanır
    },
    f"{BASE}\\Devices\\{DEV_HEX}\\DynamicCachedServices": {
        "00010000": ("Binary", des16(body(99))),     # Cached kazanmalı
        "00010001": ("Binary", dyn_b),
    },
    f"{BASE}\\Keys\\001122334455": {DEV_HEX: ("Binary", "aa" * 16)},
}
records = winbond.cached_service_records(tree)
check("cihaz bulundu", sorted(records), [DEV])
check("Cached kaydı BlueZ biçiminde", records[DEV]["00010000"], plain_a)
check("Dynamic kaydı çevrildi", records[DEV]["00010001"], des8(body(12)))
check("handle olmayan ad atlandı", "sonseen" in records[DEV], False)
check("iki handle", sorted(records[DEV]), ["00010000", "00010001"])

# Sözlük sırası garanti değil: öncelik SIRAYA bağlanırsa sessizce döner.
reversed_tree = dict(reversed(list(tree.items())))
check("öncelik sözlük sırasından BAĞIMSIZ",
      winbond.cached_service_records(reversed_tree)[DEV]["00010000"], plain_a)

check("Binary olmayan değer atlanır",
      winbond.cached_service_records({
          f"{BASE}\\Devices\\{DEV_HEX}\\CachedServices": {
              "00010000": ("String", plain_a)}}), {})
check("boş kova cihaz üretmez",
      winbond.cached_service_records({
          f"{BASE}\\Devices\\{DEV_HEX}\\CachedServices": {}}), {})

print("\n=== bluezbond.write_service_records ===")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cache_dir = root / ADAPTER / "cache"
    cache_dir.mkdir(parents=True)
    target = cache_dir / DEV
    target.write_text(
        "[General]\nName=Test Cihaz\n\n"
        "[ServiceRecords]\n"
        f"0x00010000={des8(body(10)).upper()}\n"
        f"0x000100ff={des8(body(4)).upper()}\n\n"
        "[Endpoints]\n01=01:00:01:7fff0835\n",
        encoding="utf-8")

    incoming = {"00010000": des8(body(10)),          # aynı -> kept
                "00010001": des8(body(12)),          # yeni  -> added
                "000100ff": des8(body(6))}           # farklı -> blocked
    stats = bluezbond.write_service_records(root, ADAPTER, DEV, incoming,
                                            force=False, log=lambda *_: None)
    check("aynı kayıt korundu", stats["kept"], 1)
    check("yeni kayıt eklendi", stats["added"], 1)
    check("farklı kayıt --force olmadan DEĞİŞMEDİ", stats["blocked"], 1)
    check("güncelleme yok", stats["updated"], 0)

    after = bluezbond._parser()
    after.read(target)
    check("başka bölümler korundu", sorted(after.sections()),
          ["Endpoints", "General", "ServiceRecords"])
    check("General korundu", after["General"]["Name"], "Test Cihaz")
    check("Endpoints korundu", after["Endpoints"]["01"], "01:00:01:7fff0835")
    check("yeni handle yazıldı", after["ServiceRecords"]["0x00010001"],
          des8(body(12)).upper())
    check("bloke edilen handle ESKİ değerde", after["ServiceRecords"]["0x000100ff"],
          des8(body(4)).upper())
    check("yedek alındı", len(list(cache_dir.glob(f"{DEV}.bak-*"))), 1)
    check("dosya izni 0600", oct(target.stat().st_mode & 0o777), "0o600")

    stats = bluezbond.write_service_records(root, ADAPTER, DEV, incoming,
                                            force=True, log=lambda *_: None)
    check("--force ile güncellendi", stats["updated"], 1)
    after = bluezbond._parser()
    after.read(target)
    check("değer gerçekten değişti", after["ServiceRecords"]["0x000100ff"],
          des8(body(6)).upper())

    # Değişecek bir şey yoksa dosyaya HİÇ dokunulmaz: gereksiz yedek üretmek
    # `cache/` dizinini şişirir ve her turda yeni bir dosya bırakır.
    before = target.stat().st_mtime_ns
    baks = len(list(cache_dir.glob(f"{DEV}.bak-*")))
    stats = bluezbond.write_service_records(root, ADAPTER, DEV, incoming,
                                            force=True, log=lambda *_: None)
    check("değişiklik yoksa yazma yok", stats["added"] + stats["updated"], 0)
    check("dosya dokunulmadı", target.stat().st_mtime_ns, before)
    check("yedek üretilmedi", len(list(cache_dir.glob(f"{DEV}.bak-*"))), baks)

    # Hiç önbellek dosyası olmayan cihaz: dosya sıfırdan kurulur.
    other = "11:22:33:44:55:66"
    stats = bluezbond.write_service_records(root, ADAPTER, other,
                                            {"00010000": des8(body(8))},
                                            log=lambda *_: None)
    check("yeni dosya kuruldu", (cache_dir / other).is_file(), True)
    check("yeni dosyada yedek yok", len(list(cache_dir.glob(f"{other}.bak-*"))), 0)
    check("okuyucu geri okuyabiliyor",
          bluezbond.service_records(root, ADAPTER, other),
          {"00010000": des8(body(8))})
    check("geçici dosya kalmadı", list(cache_dir.glob(".*tmp*")), [])

print("\n=== bluezbond.stale_role_ltk ===")
# ÖLÇÜLDÜ (2026-09-05): bu makinedeki üç bond'un HİÇBİRİNDE bu bölüm yok, yani
# üretimde tetik sessiz. Test onu yine de kilitliyor çünkü sessiz kalması
# ölçümün sonucu, kodun garantisi değil.
empty = bluezbond._parser()
empty.read_string("[General]\nName=X\n\n[LongTermKey]\nKey=00\n")
check("rol bölümü yoksa sessiz", bluezbond.stale_role_ltk(empty), [])
check("info yoksa sessiz", bluezbond.stale_role_ltk(None), [])
for name in ("PeripheralLongTermKey", "SlaveLongTermKey"):
    parser = bluezbond._parser()
    parser.read_string(f"[General]\nName=X\n\n[{name}]\nKey=00\n")
    check(f"[{name}] yakalanıyor", bluezbond.stale_role_ltk(parser), [name])

print("\n=== hivebond.backup_hive ===")
with tempfile.TemporaryDirectory() as tmp:
    hive = Path(tmp) / "SYSTEM"
    hive.write_bytes(b"regf" + b"\x00" * 64)
    backup_dir = Path(tmp) / "yedek"
    dest = hivebond.backup_hive(hive, backup_dir)
    check("yedek dosyası var", dest.is_file(), True)
    check("içerik birebir", dest.read_bytes(), hive.read_bytes())
    check("adı kovanın adıyla başlıyor", dest.name.startswith("SYSTEM-"), True)
    check("yedek izni 0600", oct(dest.stat().st_mode & 0o777), "0o600")
    check("dizin izni 0700", oct(backup_dir.stat().st_mode & 0o777), "0o700")

    # Yedek alınamıyorsa yazma HİÇ başlamamalı: yedeksiz yazmak ayrı bir karar.
    blocked = Path(tmp) / "dolu"
    blocked.write_text("bu bir dosya, dizin değil")
    try:
        hivebond.backup_hive(hive, blocked / "alt")
        check("yedek alınamazsa HiveError", "istisna yok", "HiveError")
    except hivebond.HiveError as exc:
        check("yedek alınamazsa HiveError", "yazma YAPILMADI" in str(exc), True)

print("\n=== sidemount: bağlı Windows keşfi ===")
check("sekizli kaçış çözülüyor", sidemount._unescape_mount(r"/mnt/win\040yedek"),
      "/mnt/win yedek")
check("ters bölü kaçışı", sidemount._unescape_mount(r"/mnt/a\134b"), "/mnt/a\\b")
check("kaçışsız yol aynen", sidemount._unescape_mount("/mnt/win"), "/mnt/win")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    win = root / "win"
    (win / "Windows/System32/config").mkdir(parents=True)
    (win / "Windows/System32/config/SYSTEM").write_bytes(b"regf")
    bos = root / "bos"
    bos.mkdir()
    kacisli = root / "bosluk lu"
    (kacisli / "Windows/System32/config").mkdir(parents=True)
    (kacisli / "Windows/System32/config/SYSTEM").write_bytes(b"regf")

    mounts = root / "mounts"
    mounts.write_text(
        f"/dev/sda2 {win} ntfs3 ro 0 0\n"
        f"/dev/sda3 {bos} ext4 rw 0 0\n"
        f"/dev/sdb1 {str(kacisli).replace(' ', chr(92) + '040')} ntfs3 ro 0 0\n"
        f"/dev/loop0 {win} squashfs ro 0 0\n"
        f"proc /proc proc rw 0 0\n"
        f"kirik\n", encoding="utf-8")

    found = sidemount.locate_mounted_windows(str(mounts))
    check("Windows taşıyan bölüm bulundu", str(win) in found, True)
    check("SYSTEM taşımayan bölüm elendi", str(bos) in found, False)
    check("/dev/loop elendi", found.count(str(win)), 1)
    check("/dev ile başlamayan satır elendi", "/proc" in found, False)
    check("boşluklu mount noktası çözüldü", str(kacisli) in found, True)
    check("bozuk satır çökertmiyor", len(found), 2)

check("okunamayan mounts dosyası boş liste",
      sidemount.locate_mounted_windows("/yok/boyle/bir/dosya"), [])

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
