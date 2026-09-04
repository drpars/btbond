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
adapters, names, devices, _svc = winbond.collect(winbond.parse_dump(lines))
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

print("\n=== LEFlags: sabit YOK, ya korunur ya verilir ya yazılmaz ===")
# Ölçüldü (2026-09-04): Xbox 0x10030000, fare 0x000B0000 — cihaza göre
# değişiyor ve n=2'de hangi bitin neye bağlı olduğu ayrıştırılamıyor. Sabit
# yazmak ölçülmüş biçimde YANLIŞ olurdu → `winbond.LEFLAGS_NOTU`.
check("sabit kümede LEFlags YOK", "LEFlags" in winbond.LE_SERVICE_FLAGS, False)

svc = {(DEV_MAC, "00:11:22:33:44:55"): {"LEFlags": "268632064"},
       ("AA:BB:CC:DD:EE:02", "00:11:22:33:44:55"): {"LEFlags": "-1"}}
check("hedefteki değer okunuyor",
      winbond.existing_le_flags(svc, DEV_MAC, "00:11:22:33:44:55"), 0x10030000)
check("işaretli gösterim işaretsize çevriliyor",
      winbond.existing_le_flags(svc, "AA:BB:CC:DD:EE:02", "00:11:22:33:44:55"),
      0xFFFFFFFF)
check("hedefte yoksa None",
      winbond.existing_le_flags(svc, "AA:BB:CC:DD:EE:03", "00:11:22:33:44:55"), None)
check("adaptör tutmazsa None",
      winbond.existing_le_flags(svc, DEV_MAC, "FF:FF:FF:FF:FF:FF"), None)

# `collect` bu alt anahtarı eskiden SESSİZCE düşürüyordu (`len(parts) == 2`
# süzgeci), yani hedefin kendi değeri modelde hiç görünmüyordu.
svc_lines = "\n".join([
    f"V\t{BASE}\\Devices\\{DEV_HEX}\\ServicesFor{ADAPTER_HEX}\tLEFlags\tDWord\t720896",
    f"V\t{BASE}\\Devices\\{DEV_HEX}\\ServicesFor{ADAPTER_HEX}\tBasebandSupport\tDWord\t32768",
])
_a, _n, _d, svc2 = winbond.collect(winbond.parse_dump(svc_lines))
check("collect ServicesFor'u yakalıyor",
      winbond.existing_le_flags(svc2, DEV_MAC, "00:11:22:33:44:55"), 0x000B0000)

# Emitör: verilmezse alan HİÇ yazılmaz, verilirse yazılır.
def _has_leflags(ops):
    return any(op[0] == winbond.DW and op[2] == "LEFlags" for op in ops)


check("le_flags=None -> alan yazılmıyor",
      _has_leflags(winbond.device_record_ops(
          "00:11:22:33:44:55", DEV_MAC, "", True, {}, [])), False)
check("le_flags verilince yazılıyor",
      _has_leflags(winbond.device_record_ops(
          "00:11:22:33:44:55", DEV_MAC, "", True, {}, [], le_flags=0x000B0000)), True)

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

print("\n=== cross_sides: host'un hiç bond'u YOKKEN de ayrışmayı görüyor mu ===")
# Düzeltilen sessiz dal: ölçüt `per_side` uzunluğu olsaydı (host anahtarı
# doğmadığı için 2 < 3) iki misafirli gerçek bir ayrışma sessizce geçerdi —
# tam da `collect` fazının otomatik yazmasını engellemesi gereken durum.
host_yok = bondsync.cross_sides([side("A", None, {"LTK": "k1"}),
                                 side("B", None, {"LTK": "k2"})])
check("host'suz ayrışma bulundu", len(host_yok), 1)
check("gruplar yalnız misafirlerden", host_yok[0]["labels"]["LTK"]["groups"],
      {"k1": ["A"], "k2": ["B"]})

print("\n=== iki fazlı akış ===")
check("sync iki fazı SIRAYLA taşıyor", btbond_sync.PHASES["sync"],
      ["to-host", "to-guest"])
check("collect tek faz", btbond_sync.PHASES["collect"], ["to-host"])
check("distribute tek faz", btbond_sync.PHASES["distribute"], ["to-guest"])

# Ayrışan cihaz HİÇBİR fazda otomatik yazılmaz. Bütün satırlar engelliyse
# `run_phase` kapıya hiç bakmadan 0 döner (yazacak bir şey yok).
tek_satir = side("A", None, {"LTK": "k1"})
tek_satir["rows"][0]["direction"] = "to-host"
tek_satir["rows"][0]["verdict"] = bondsync.GUEST_ONLY
check("engellenen cihazla faz yazmaya kalkmıyor",
      btbond_sync.run_phase(None, tek_satir, "to-host", {DEV_MAC}), 0)

print("\n=== write_gate: 'hedef radyoyu tutuyor' artık tek cevap değil ===")
# Kapının asıl sorusu "hedef TAZE okuyabilecek mi": radyo sonradan gelir YA DA
# yığın yeniden başlar. Host için ikincisi bluetoothd stop/start; misafir için
# karşılığı ölçülmedi, o yüzden orada kaçış yok.
host_holds = {"host": True, "guest": False}
check("host tutuyor, restart yok -> DURUR",
      bondsync.write_gate(host_holds, "to-host")[0], False)
check("host tutuyor, restart var -> GEÇER",
      bondsync.write_gate(host_holds, "to-host", stack_restart=True)[0], True)
check("ret mesajı çareyi söylüyor",
      "--stop-bluetooth" in bondsync.write_gate(host_holds, "to-host")[1], True)
guest_holds = {"host": False, "guest": True}
check("misafir tutuyor, restart bayrağı misafiri GEÇİRMEZ (ölçülmedi)",
      bondsync.write_gate(guest_holds, "to-guest", stack_restart=True)[0], False)

print("\n=== kapsam: argümansız = TANIMLI HERKES, --domain daraltır ===")
# Eski davranış (tek varsayılan + "dokunulmayan N domain" uyarısı) kullanıcıyı
# her koşuda üç --domain yazmaya mahkûm ediyordu; kaldırıldı (2026-09-04).
import agentexec
_real_discover = agentexec.discover_domains
try:
    agentexec.discover_domains = lambda uri=None: ["a", "b", "c"]
    check("argümansız -> herkes", btbond_sync.resolve_domains(None)[0], ["a", "b", "c"])
    check("argümansız -> not YOK", btbond_sync.resolve_domains(None)[1], None)
    doms, note = btbond_sync.resolve_domains(["b", "b"])
    check("--domain daraltır ve tekilleştirir", doms, ["b"])
    check("daraltma notu dokunulmayanı ADLANDIRIYOR",
          note is not None and "a" in note and "c" in note, True)
    # Tek hedefli araçlar: birden çok tanımlıyken TAHMİN YOK.
    check("single_domain: verildiyse o", agentexec.single_domain("b")[0], "b")
    dom, why = agentexec.single_domain(None, "x")
    check("single_domain: 3 tanımlı -> hata, ad listesiyle",
          dom is None and "a, b, c" in why, True)
    agentexec.discover_domains = lambda uri=None: ["tek"]
    check("single_domain: tek tanımlı -> o", agentexec.single_domain(None)[0], "tek")
    agentexec.discover_domains = lambda uri=None: None
    check("keşif olanaksız -> varsayılan + not",
          (btbond_sync.resolve_domains(None)[0], btbond_sync.resolve_domains(None)[1] is not None),
          ([agentexec.DEFAULT_DOMAIN], True))
finally:
    agentexec.discover_domains = _real_discover

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
