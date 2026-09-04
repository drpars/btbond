#!/usr/bin/env python3
"""Taşıyıcı ve model sözleşmeleri — misafir, root ve kovan GEREKTİRMEZ.

Buradaki iddiaların hepsi bir kez elle ölçüldü; test onları **kilitliyor**,
çünkü hepsi sessizce bozulabilen sınıfta: yanlış sayı gösterimi, yol biçiminin
kayması ya da bir olumsuzun kapsamsız basılması hata vermez, çıkış kodunu
değiştirmez ve okuyan turu ikna eder.

    tests/test_transport.py

MAKİNEYE ÖZEL KİMLİK YOK: MAC'ler uydurma, anahtarlar dolgu. Depo public.
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
import bondsync  # noqa: E402
import hivebond  # noqa: E402
import winbond  # noqa: E402

# `btbond-sync.py` tire içerdiği için normal import edilemiyor.
_spec = importlib.util.spec_from_file_location("btbond_sync", TOOLS / "btbond-sync.py")
btbond_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(btbond_sync)

ADAPTER_HEX = "001122334455"
DEV_HEX = "aabbccddeeff"
DEV_MAC = "AA:BB:CC:DD:EE:FF"
BASE = r"\SYSTEM\ControlSet001\Services\BTHPORT\Parameters"

OK = FAIL = 0


def check(label, got, want):
    global OK, FAIL
    if got == want:
        OK += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [HATA] {label}: {got!r} != {want!r}")


def side(domain, host_fp, guest_fp, dev=DEV_MAC, name="Test Cihaz"):
    """Tek satırlı sentetik bir `survey` durumu."""
    return {"domain": domain, "adapter": "00:11:22:33:44:55", "warnings": [],
            "radio": {"host": True, "guest": False, "others": None, "where": "host"},
            "rows": [{"dev": dev, "name": name, "tech": "LE",
                      "host": host_fp, "guest": guest_fp,
                      "verdict": (bondsync.MATCH if host_fp == guest_fp
                                  else bondsync.KEY_MISMATCH),
                      "differing": [], "direction": None, "address_type": None}]}


print("=== hivebond._render: PowerShell gösterimine uyuyor mu ===")
# Bu eşleme taşıyıcı olgusu: `winbond.parse_dump` ajanın bastığı dizeleri
# bekliyor, offline taşıyıcı da aynı dizeleri üretmek zorunda.
check("Binary -> hex", hivebond._render(3, bytes.fromhex("aabbcc")),
      ("Binary", "aabbcc"))
check("DWord işaretli (-1)", hivebond._render(4, (0xFFFFFFFF).to_bytes(4, "little")),
      ("DWord", "-1"))
check("DWord düz", hivebond._render(4, (45).to_bytes(4, "little")), ("DWord", "45"))
check("String UTF-16LE, NUL kırpılıyor",
      hivebond._render(1, "Test\x00".encode("utf-16-le")), ("String", "Test"))

# ÜST BİTİ DOLU QWORD: PowerShell `[Int64]` döndürdüğü için negatif basıyor,
# ve `winbond.as_uint` tam o gösterimi geri çeviriyor. İşaretsiz basmak
# ayrıştırıcıyı sessizce ikiye bölerdi.
kind, text = hivebond._render(11, (0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"))
check("QWord işaretli basılıyor", (kind, text), ("QWord", "-1"))
check("as_uint geri çeviriyor", winbond.as_uint(text, 64), 0xFFFFFFFFFFFFFFFF)

print("\n=== yol biçimi: winbond.split_path onu tanıyor mu ===")
# Uymazsa `collect` BOŞ döner, yani hata temiz bir "bond yok" gibi görünür.
check("Keys/<adaptör>", winbond.split_path(rf"{BASE}\Keys\{ADAPTER_HEX}"),
      ["Keys", ADAPTER_HEX])
check("Keys/<adaptör>/<cihaz>",
      winbond.split_path(rf"{BASE}\Keys\{ADAPTER_HEX}\{DEV_HEX}"),
      ["Keys", ADAPTER_HEX, DEV_HEX])
check("Devices/<cihaz>", winbond.split_path(rf"{BASE}\Devices\{DEV_HEX}"),
      ["Devices", DEV_HEX])

print("\n=== uçtan uca: V satırları -> collect -> guest_state ===")
lines = "\n".join([
    f"V\t{BASE}\\Keys\\{ADAPTER_HEX}\tCentralIRK\tBinary\t{'11' * 16}",
    f"V\t{BASE}\\Keys\\{ADAPTER_HEX}\t{DEV_HEX}\tBinary\t{'22' * 16}",
    f"V\t{BASE}\\Keys\\{ADAPTER_HEX}\\{DEV_HEX}\tLTK\tBinary\t{'33' * 16}",
    f"V\t{BASE}\\Devices\\{DEV_HEX}\tName\tBinary\t" + b"Test\x00".hex(),
    f"V\t{BASE}\\Devices\\{DEV_HEX}\tLEAddressType\tDWord\t1",
])
adapters, names, devices = winbond.collect(winbond.parse_dump(lines))
check("adaptör bulundu", list(adapters), ["00:11:22:33:44:55"])
entry = adapters["00:11:22:33:44:55"]
check("CentralIRK bond sayılmıyor", list(entry["bredr"]), [DEV_MAC])
check("LE bond alt anahtarda", list(entry["le"]), [DEV_MAC])
check("ad Name blob'undan", names.get(DEV_MAC), "Test")
rows = bondsync.guest_state(entry, names)
check("guest_state iki tekniği birleştiriyor", rows[DEV_MAC]["tech"], "BR/EDR+LE")
check("LTK parmak izi", rows[DEV_MAC]["fp"]["LTK"], winbond.fingerprint("33" * 16))
check("LEAddressType Devices'ten çözülüyor",
      winbond.le_address_type({}, devices[DEV_MAC]), (1, "Devices"))

print("\n=== hivebond.find_system_hive: ölçüt NTFS değil, DOSYA ===")
for label, target in (("Windows olmayan dizin", "/tmp"),
                      ("var olmayan yol", "/tmp/__btbond_yok__")):
    try:
        hivebond.find_system_hive(target)
        check(label, "istisna atmadı", "HiveError")
    except hivebond.HiveError:
        check(label, "HiveError", "HiveError")

print("\n=== bondsync.cross_sides: eşleştirmeli tablonun göremediği şey ===")
check("tek misafir -> taraflar arası yok",
      bondsync.cross_sides([side("A", {"LTK": "k1"}, {"LTK": "k1"})]), [])
check("hepsi aynı -> ayrışma yok",
      bondsync.cross_sides([side("A", {"LTK": "k1"}, {"LTK": "k1"}),
                            side("B", {"LTK": "k1"}, {"LTK": "k1"})]), [])
check("hatalı taraf sayılmıyor",
      bondsync.cross_sides([side("A", {"LTK": "k1"}, {"LTK": "k1"}),
                            {"domain": "B", "error": "kapalı"}]), [])

cross = bondsync.cross_sides([side("A", {"LTK": "k1"}, {"LTK": "k1"}),
                              side("B", {"LTK": "k1"}, {"LTK": "k2"})])
check("ayrışma bulundu", len(cross), 1)
info = cross[0]["labels"]["LTK"]
check("gruplar taraf adlarıyla", info["groups"], {"k1": ["A", "host"], "k2": ["B"]})
# Cihaz TEK anahtar tutar, yani tek başına duran taraf çalışan tek taraf
# olabilir — azınlık işareti bunu görünür kılmak için var.
check("azınlık = tek başına duran", info["minority"], ["B"])

print("\n=== çıktı azınlığı HÜKÜM olarak sunmuyor ===")
rendered = btbond_sync.render_cross(cross)
check("AZINLIK işareti basılıyor", "<- AZINLIK" in rendered, True)
check("'OY DEĞİL' uyarısı metnin içinde", "OY DEĞİL" in rendered, True)

print("\n=== resolve_domains: kapsam ===")
check("açık liste tekilleşiyor, sıra korunuyor",
      btbond_sync.resolve_domains(["x", "y", "x"])[0], ["x", "y"])
check("açık listede uyarı yok", btbond_sync.resolve_domains(["x"])[1], None)

_real = bondsync.other_domains
try:
    bondsync.other_domains = lambda *a, **k: []
    check("başka domain yok -> uyarı yok", btbond_sync.resolve_domains(None)[1], None)
    bondsync.other_domains = lambda *a, **k: ["baska1", "baska2"]
    warning = btbond_sync.resolve_domains(None)[1]
    check("uyarı dokunulmayanları ADLANDIRIYOR",
          warning is not None and "baska1" in warning and "baska2" in warning, True)
finally:
    bondsync.other_domains = _real

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
