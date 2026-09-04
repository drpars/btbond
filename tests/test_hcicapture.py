#!/usr/bin/env python3
"""btmon ayrıştırıcısının sözleşmeleri — root, adaptör ve cihaz GEREKTİRMEZ.

NİYE VAR: bu modülün sürüm dalı yazıldıktan sonra **hiç koşmadı** ve o hâlde
kaldığı için iki kusur birden taşıdı; ikisi de ilk gerçek olayda çıktı
(2026-09-04). İkisi de sessizdi — istisna yok, çıkış kodu 0, yalnız boş ya da
eksik sonuç. Testin işi tam olarak bu sınıfı kilitlemek.

    tests/test_hcicapture.py

Metin gerçek btmon çıktısının biçimidir; MAC'ler UYDURMA, özellik baytları
dolgu (depo public → makineye özel kimlik yazılmaz).
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
import hcicapture  # noqa: E402

DEV_A = "AA:BB:CC:DD:EE:FF"
DEV_B = "11:22:33:44:55:66"

OK = FAIL = 0


def check(label, got, want):
    global OK, FAIL
    if got == want:
        OK += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [HATA] {label}: {got!r} != {want!r}")


# btmon'un GERÇEK biçimi: alanlar 8 boşluk girintili, sürüm ve alt sürüm
# TEK satırda, ve olay adresi BASMIYOR — yalnız handle.
VERSION_LOG = """\
> HCI Event: Read Remote Version Complete (0x0c) plen 8      #4 [hci0] 5.381820
        Status: Success (0x00)
        Handle: 2048
        LMP version: Bluetooth 5.1 (0x0a) - Subversion 531 (0x0213)
        Manufacturer: Texas Instruments Inc. (13)
"""

FEATURES_LOG = """\
> HCI Event: Read Remote Supported Features (0x0b) plen 11   #3 [hci0] 5.591509
        Status: Success (0x00)
        Handle: 256
        Features[0/0][8]:
        01 02 03 04 05 06 07 08                          ........
          3 slot packets
"""

# Adres taşıyan olay: handle→adres eşlemesi log'un İÇİNDEN de kurulabiliyor.
CONNECT_LOG = """\
> HCI Event: Connect Complete (0x03) plen 11                 #2 [hci0] 1.100000
        Status: Success (0x00)
        Handle: 256
        Address: 11:22:33:44:55:66 (Filler)
        Link type: ACL (0x01)
"""

HCITOOL_CON = """\
Connections:
\t< LE AA:BB:CC:DD:EE:FF handle 2048 state 1 lm CENTRAL AUTH ENCRYPT
\t> ACL 11:22:33:44:55:66 handle 256 state 1 lm CENTRAL AUTH ENCRYPT
"""

print("hcicapture — sürüm dalı")

# --- TOHUM: kurucu kusur. Var olan bir bağlantıya komut yollandığında
# yakalamanın içinde `Connect Complete` GEÇMEZ; eşleme boş kalır ve dolu bir
# olay sessizce ATILIR.
check("tohumsuz: adres çözülemez -> BOŞ (ve bu bir kusur, özellik değil)",
      hcicapture.parse(VERSION_LOG), {})
seeded = hcicapture.parse(VERSION_LOG, by_handle={2048: DEV_A})
check("tohumlu: kayıt açılıyor", sorted(seeded), [DEV_A])

# --- BİRLEŞİK SATIR: btmon iki alanı tek satırda basıyor ve alan adı
# `LMP version`. Ayrı `Version`/`Subversion` alanları hiçbir gerçek log'da yok.
check("lmp_version birleşik satırdan", seeded[DEV_A].get("lmp_version"), 10)
check("lmp_subversion birleşik satırdan", seeded[DEV_A].get("lmp_subversion"), 531)
check("manufacturer", seeded[DEV_A].get("manufacturer"), 13)

# --- SESSİZ YANLIŞIN KİLİDİ: `_num` SON parantezi alır, yani satırı düz
# eşlemek `lmp_version`e ALT SÜRÜMÜ yazardı. Değer eşitliği değil, KARIŞMAMASI
# sınanıyor — 10 ve 531 birbirinin yerine geçmemeli.
check("lmp_version alt sürümle KARIŞMIYOR", seeded[DEV_A]["lmp_version"] != 531, True)
check("_num son parantezi alıyor (kusurun kaynağı yerinde duruyor)",
      hcicapture._num("Bluetooth 5.1 (0x0a) - Subversion 531 (0x0213)"), 531)

# --- Windows alan adlarına çeviri: yalnız DOLU olanlar.
check("to_windows_fields üçünü de veriyor",
      hcicapture.to_windows_fields(seeded[DEV_A]),
      {"LmpVersion": 10, "LmpSubversion": 531, "ManufacturerId": 13})

print("hcicapture — özellik dalı (regresyon)")

parsed = hcicapture.parse(FEATURES_LOG, by_handle={256: DEV_B})
check("lmp_features little-endian okunuyor",
      parsed[DEV_B].get("lmp_features"), 0x0807060504030201)
check("özellik dalı Windows alanına eşleniyor",
      hcicapture.to_windows_fields(parsed[DEV_B]),
      {"LMPFeatures": 0x0807060504030201})

print("hcicapture — tohum ile log'un ilişkisi")

# Log'daki gerçek olay tohumu EZER: aynı handle yeniden atanmış olabilir.
both = hcicapture.parse(CONNECT_LOG + FEATURES_LOG, by_handle={256: DEV_A})
check("log'daki adres tohumu eziyor", sorted(both), [DEV_B])
# Tohum, log'da hiç geçmeyen handle için hâlâ geçerli.
mixed = hcicapture.parse(CONNECT_LOG + VERSION_LOG, by_handle={2048: DEV_A})
check("tohum log'un kapsamadığı handle'ı taşıyor", sorted(mixed), [DEV_A])

print("hcicapture — `hcitool con` ayrıştırması")

cons = [(int(h), a.upper(), k) for k, a, h in hcicapture.CON_RE.findall(HCITOOL_CON)]
check("iki bağlantı, handle ve tür ile",
      cons, [(2048, DEV_A, "LE"), (256, DEV_B, "ACL")])
check("`Connections:` başlığı satır sayılmıyor", len(cons), 2)

print("hcicapture — boş yakalamanın kapsamı")

# Boş sonuç "cihaz yok" diye okunmasın: `summary` sebebi SÖYLER.
lines = hcicapture.summary({})
check("boş yakalama açıklanıyor", len(lines) == 1 and "boş" in lines[0], True)
# Sürüm eksikse sebebi yazılıyor — çekirdeğin komutu hiç yollamaması.
missing = hcicapture.summary({DEV_A: {"lmp_features": 1}})
check("sürüm eksikse sebebi yazılıyor",
      any("hcitool" in line for line in missing), True)

# LE'de `LMPFeatures` eksik DEĞİL, Windows'ta zaten yok. Kapsamsız bir
# "eksik" satırı var olmayan bir kusuru rapor ederdi.
le_entry = {DEV_A: {"lmp_version": 10, "lmp_subversion": 531, "manufacturer": 13}}
check("LE'de LMPFeatures 'eksik' diye basılmıyor",
      any("ZATEN YOK" in line for line in
          hcicapture.summary(le_entry, kinds={DEV_A: "LE"})), True)
check("aynı kayıt BR/EDR'de eksik sayılıyor",
      any("Windows alanı eksik" in line for line in
          hcicapture.summary(le_entry, kinds={DEV_A: "ACL"})), True)
check("tür bilinmiyorsa eksik sayılıyor (varsayım yok)",
      any("Windows alanı eksik" in line for line in
          hcicapture.summary(le_entry)), True)

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
