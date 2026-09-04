#!/usr/bin/env python3
"""TUI sözleşmeleri — terminal, root, misafir ve kovan GEREKTİRMEZ.

Textual'ın başsız `run_test()` sürücüsüyle koşuyor; `survey_all` sentetikle
değiştiriliyor, yani ölçülen şey yalnız arayüzün **kararları**:

  - satırlar ve hükümler modelden geliyor mu,
  - **KAPI** yıkıcı yolu kesiyor mu (ve TUI'nin kendi kopyası olmadığı için
    `bondsync.write_gate`i mi çağırıyor),
  - `ANAHTAR FARKLI` **hiçbir zaman** kendiliğinden koşmuyor mu,
  - yazımdan sonra tablo **bayat** işaretleniyor mu.

Sonuncusu görünüşte kozmetik değil: tablo yazımdan ÖNCEKİ ölçümü gösteriyor
ve taraf başına ~1 sn'lik bir tur olduğu için kendiliğinden tazelenmiyor.
Bayat işareti olmasa ekranda duran veri canlı sanılırdı.

MAKİNEYE ÖZEL KİMLİK YOK: MAC'ler uydurma, parmak izleri dolgu.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
import bondsync  # noqa: E402

_spec = importlib.util.spec_from_file_location("btbond_tui", TOOLS / "btbond-tui.py")
tui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tui)

ADAPTER = "00:11:22:33:44:55"
OK = FAIL = 0


def check(label, got, want):
    global OK, FAIL
    if got == want:
        OK += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [HATA] {label}: {got!r} != {want!r}")


def row(dev, verdict, direction, host=None, guest=None, differing=()):
    return {"dev": dev, "name": "Test Cihaz", "tech": "LE",
            "host": host, "guest": guest, "verdict": verdict,
            "differing": list(differing), "direction": direction,
            "address_type": None}


def side(domain, rows, radio_host=True, radio_guest=False, channel="ajan"):
    return {"domain": domain, "adapter": ADAPTER, "warnings": [],
            "channel": channel,
            "radio": {"host": radio_host, "guest": radio_guest,
                      "others": None, "where": "sentetik"},
            "rows": rows}


def survey(sides, cross=()):
    return {"sides": list(sides), "cross": list(cross)}


async def boot(app):
    """Uygulamayı aç ve ilk ölçümün bitmesini bekle (iş parçacığında koşuyor)."""
    pilot = app.run_test()
    driver = await pilot.__aenter__()
    for _ in range(80):
        if app.survey is not None:
            break
        await driver.pause(0.05)
    return pilot, driver


def run(coro):
    return asyncio.run(coro)


# --- 1) satırlar ve hükümler modelden geliyor -----------------------------
async def t_rows():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"}),
        row("AA:BB:CC:DD:EE:02", bondsync.MATCH, None,
            host={"LTK": "fp2"}, guest={"LTK": "fp2"}),
    ])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        check("iki satır çizildi", table.row_count, 2)
        check("hüküm etiketi modelden", table.get_row_at(0)[3], "yalnız host'ta")
        check("yön satırın özelliği", table.get_row_at(0)[4], "→ misafir")
        check("eşleşen satırın yönü boş", table.get_row_at(1)[4], "")
        check("ölçüm saati konuldu", app.measured_at is not None, True)
        check("başta bayat değil", app.stale, False)
    finally:
        await pilot.__aexit__(None, None, None)


# --- 2) KAPI yıkıcı yolu kesiyor -----------------------------------------
async def t_gate():
    # Hedef `to-guest`, ve radyo MİSAFİRDE: bu sırada yazmak hata vermez,
    # sessizce etkisiz kalır. Kapı durdurmalı ve onay ekranı AÇILMAMALI.
    bondsync.survey_all = lambda *a, **k: survey([side(
        "d1", [row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
                   host={"LTK": "fp1"})],
        radio_host=False, radio_guest=True)])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        before = len(app.screen_stack)
        app.action_replicate()
        await driver.pause()
        check("kapı onay ekranını açmadı", len(app.screen_stack), before)

        # Aynı kapı ÖLÇÜLEMEDİĞİNDE de durur (guest=None).
        allowed, _ = bondsync.write_gate(
            {"host": False, "guest": None}, "to-guest")
        check("ölçülemeyen kapı da durduruyor", allowed, False)
        allowed, _ = bondsync.write_gate(
            {"host": False, "guest": False}, "to-guest")
        check("hedef radyoyu tutmuyorsa geçiyor", allowed, True)
    finally:
        await pilot.__aexit__(None, None, None)


# --- 3) ANAHTAR FARKLI kendiliğinden koşmuyor ----------------------------
async def t_mismatch():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.KEY_MISMATCH, None,
            host={"LTK": "fpH"}, guest={"LTK": "fpG"}, differing=["LTK"]),
    ])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        check("ayrışan satırın yönü YOK", table.get_row_at(0)[4], "")
        ran = []
        app._run = lambda cmd: ran.append(cmd)      # yazımı yakala
        app.action_replicate()
        await driver.pause()
        check("çözüm ekranı açıldı", isinstance(app.screen, tui.Resolve), True)
        check("hiçbir şey KOŞMADI", ran, [])
        # Vazgeçmek de yazmaz.
        app.screen.dismiss(None)
        await driver.pause()
        check("vazgeçince de koşmadı", ran, [])
    finally:
        await pilot.__aexit__(None, None, None)


# --- 4) yazımdan sonra tablo BAYAT ---------------------------------------
async def t_stale():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"})])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        check("yazımdan önce taze", app.stale, False)
        app._run_done(0, "OK record aabbccddee01")
        await driver.pause()
        check("yazımdan sonra BAYAT", app.stale, True)
    finally:
        await pilot.__aexit__(None, None, None)


# --- 5) ulaşılamayan taraf satır olarak duruyor ---------------------------
async def t_error_side():
    # ÜÇ DURUM: okunan taraf / diski bulunan (ölçülmedi) / hiç ulaşılamayan.
    # "Bond yok" ile "ölçmedim" aynı satır olmamalı.
    bondsync.survey_all = lambda *a, **k: survey(
        [side("d1", [row("AA:BB:CC:DD:EE:01", bondsync.MATCH, None,
                         host={"LTK": "f"}, guest={"LTK": "f"})]),
         {"domain": "d2", "error": "d2: domain is not running",
          "disk": {"kind": "image", "path": "/imaj/d2.qcow2", "how": "domain XML"}},
         {"domain": "d3", "error": "d3: domain is not running", "disk": None}])
    app = tui.BtbondTui(["d1", "d2", "d3"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        check("üç taraf üç satır", table.row_count, 3)
        check("diski bulunan taraf ÖLÇÜLMEDİ", table.get_row_at(1)[3],
              "ÖLÇÜLMEDİ (kapalı)")
        check("disk yolu satırda", table.get_row_at(1)[1], "/imaj/d2.qcow2")
        check("diski bulunamayan ULAŞILAMADI", table.get_row_at(2)[3],
              "ULAŞILAMADI")
        # Bu satırlarda `Enter` hiçbir şey yapmamalı (row None).
        table.move_cursor(row=1)
        app.action_replicate()
        await driver.pause()
        check("ölçülmemiş satırda Enter sessiz", len(app.screen_stack), 1)
        # `d` ise diski ve nasıl okunacağını söylemeli.
        app.action_detail()
        await driver.pause()
        check("ayrıntı çökmüyor", app.is_running, True)
    finally:
        await pilot.__aexit__(None, None, None)


print("=== TUI: satırlar modelden ===")
run(t_rows())
print("\n=== TUI: KAPI (bondsync.write_gate, tek sahip) ===")
run(t_gate())
print("\n=== TUI: ANAHTAR FARKLI otomatik çözülmüyor ===")
run(t_mismatch())
print("\n=== TUI: yazımdan sonra tablo bayat ===")
run(t_stale())
print("\n=== TUI: üç durumlu taraf satırı ===")
run(t_error_side())


async def t_offline_channel():
    """Offline okunan taraf modelde `channel` taşıyor ve satırları normal."""
    bondsync.survey_all = lambda *a, **k: survey([side(
        "d1", [row("AA:BB:CC:DD:EE:01", bondsync.GUEST_ONLY, "to-host",
                   guest={"LTK": "fp"})],
        channel="offline: /mnt/win/Windows/System32/config/SYSTEM")])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000", {"d1": "/mnt/win"})
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        check("offline taraf normal satır veriyor", table.row_count, 1)
        check("hüküm ve yön yerinde", (table.get_row_at(0)[3],
                                       table.get_row_at(0)[4]),
              ("yalnız misafirde", "→ host"))
        check("offline eşlemesi uygulamada", app.offline, {"d1": "/mnt/win"})
    finally:
        await pilot.__aexit__(None, None, None)


print("\n=== TUI: offline kanaldan okunan taraf ===")
run(t_offline_channel())


async def t_parity():
    """YETENEK EŞİTLİĞİ: iki fazlı akış ve devir TUI'de de var, ve ikisi de
    CLI'ı çağırıyor — faz sırası/devir mantığının ikinci kopyası YOK."""
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"})])])
    app = tui.BtbondTui(["d1", "d2"], "/tmp", "8087:0032", {"d2": "/mnt/x"})
    pilot, driver = await boot(app)
    try:
        ran = []
        app._run = lambda cmd: ran.append(cmd)

        # `s` iki fazlı akışı ONAY EKRANINA koyar, hemen koşturmaz.
        app.action_sync_all()
        await driver.pause()
        check("sync onay ekranı açıldı", isinstance(app.screen, tui.Confirm), True)
        check("onaysız koşmadı", ran, [])
        cmd = app.screen._command
        check("CLI'ı çağırıyor (ikinci kopya yok)",
              Path(cmd[0]).name, "btbond-sync.py")
        check("faz komutu sync", cmd[1], "sync")
        check("kapsam geçiyor", [c for c in cmd if c in ("d1", "d2")], ["d1", "d2"])
        check("offline eşlemesi geçiyor", "d2=/mnt/x" in cmd, True)
        app.screen.dismiss(None)
        await driver.pause()
        check("vazgeçince koşmadı", ran, [])

        # `h` önce HEDEF sorar (niyet), sonra onay.
        app.query_one("#rows").move_cursor(row=0)
        app.action_handover()
        await driver.pause()
        check("devir hedefi soruluyor",
              isinstance(app.screen, tui.HandoverTarget), True)
        check("hedef sorulurken koşmadı", ran, [])
        app.screen.dismiss("host")
        await driver.pause()
        check("hedef seçilince onay ekranı", isinstance(app.screen, tui.Confirm), True)
        hcmd = app.screen._command
        check("devir de CLI'dan", Path(hcmd[0]).name, "btbond-sync.py")
        check("handover komutu", (hcmd[1], hcmd[2], hcmd[3]),
              ("handover", "--to", "host"))
        app.screen.dismiss(None)
        await driver.pause()
        check("devir de onaysız koşmadı", ran, [])
    finally:
        await pilot.__aexit__(None, None, None)


print("\n=== TUI: yetenek eşitliği (s / h) ===")
run(t_parity())


async def t_no_handover_for_host():
    """`→ host` yazımı radyo host'tayken devir istemez: kapı stack-restart ile
    geçer ve komuta `--stop-bluetooth` eklenir. Eski davranış DURDU idi."""
    bondsync.survey_all = lambda *a, **k: survey([side(
        "d1", [row("AA:BB:CC:DD:EE:01", bondsync.GUEST_ONLY, "to-host",
                   guest={"LTK": "fp1"})],
        radio_host=True, radio_guest=False)])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        check("varsayılan: otomatik bağlama açık", app.automount, True)
        check("varsayılan: bluetoothd durdurma açık", app.stop_bluetooth, True)
        app.action_replicate()
        await driver.pause()
        check("host radyoyu tutarken onay ekranı AÇILDI (eskiden DURDU)",
              isinstance(app.screen, tui.Confirm), True)
        check("komut --stop-bluetooth taşıyor",
              "--stop-bluetooth" in app.screen._command, True)
        app.screen.dismiss(None)
        await driver.pause()
    finally:
        await pilot.__aexit__(None, None, None)

    # Kapatılabilir: --no-stop-bluetooth eski davranışı geri getirir.
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000", stop_bluetooth=False)
    pilot, driver = await boot(app)
    try:
        before = len(app.screen_stack)
        app.action_replicate()
        await driver.pause()
        check("stop_bluetooth kapalıyken kapı yine DURUR", len(app.screen_stack), before)
    finally:
        await pilot.__aexit__(None, None, None)


async def t_automounted_write():
    """Otomatik bağlanmış tarafa yazım: komut mount'u ŞİMDİ değil, `_run`da
    RW bağlanarak alır — onay ekranı bunu söyler."""
    st = side("d1", [row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
                         host={"LTK": "fp1"})],
              radio_host=True, radio_guest=False,
              channel="offline: /run/btbond/d1/Windows/System32/config/SYSTEM")
    st["automounted"] = True
    st["disk"] = {"kind": "image", "path": "/imaj/d1.qcow2", "how": "domain XML"}
    bondsync.survey_all = lambda *a, **k: survey([st])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        app.action_replicate()
        await driver.pause()
        check("onay açıldı", isinstance(app.screen, tui.Confirm), True)
        check("komutta henüz mount yok (RW bağlama _run'da)",
              "--offline" in app.screen._command, False)
        check("onay metni RW bağlanacağını söylüyor",
              "RW bağlanacak" in app.screen._body, True)
        app.screen.dismiss(None)
        await driver.pause()
    finally:
        await pilot.__aexit__(None, None, None)


async def t_read_empty_side():
    """Okunmuş ama BOŞ taraf tablodan kaybolmaz: "bakıldı, bond yok" bir ölçümdür."""
    empty = side("d2", [], channel="offline: /run/btbond/d2/…/SYSTEM")
    empty["warnings"] = ["host ile misafirin adaptörü kesişmiyor"]
    empty["adapter"] = None
    bondsync.survey_all = lambda *a, **k: survey([
        side("d1", [row("AA:BB:CC:DD:EE:01", bondsync.MATCH, None,
                        host={"LTK": "f"}, guest={"LTK": "f"})]), empty])
    app = tui.BtbondTui(["d1", "d2"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        check("boş taraf da satır", table.row_count, 2)
        check("hükmü OKUNDU — bond yok", table.get_row_at(1)[3], "OKUNDU — bond yok")
        table.move_cursor(row=1)
        app.action_detail()
        await driver.pause()
        check("ayrıntı çökmüyor", app.is_running, True)
    finally:
        await pilot.__aexit__(None, None, None)


print("\n=== TUI: okunmuş ama boş taraf ===")
run(t_read_empty_side())

print("\n=== TUI: → host için devir gerekmiyor ===")
run(t_no_handover_for_host())
print("\n=== TUI: otomatik bağlanmış tarafa yazım ===")
run(t_automounted_write())

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
