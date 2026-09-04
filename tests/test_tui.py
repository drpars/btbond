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


def side(domain, rows, radio_host=True, radio_guest=False):
    return {"domain": domain, "adapter": ADAPTER, "warnings": [],
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
    bondsync.survey_all = lambda *a, **k: survey(
        [side("d1", [row("AA:BB:CC:DD:EE:01", bondsync.MATCH, None,
                         host={"LTK": "f"}, guest={"LTK": "f"})]),
         {"domain": "d2", "error": "d2: domain is not running"}])
    app = tui.BtbondTui(["d1", "d2"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        check("atlanan taraf da satır", table.row_count, 2)
        check("hükmü ATLANDI", table.get_row_at(1)[3], "ATLANDI")
        # O satırda `Enter` hiçbir şey yapmamalı (row None).
        app.action_replicate()
        await driver.pause()
        check("atlanan satırda Enter sessiz", len(app.screen_stack), 1)
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
print("\n=== TUI: ulaşılamayan taraf ===")
run(t_error_side())

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
