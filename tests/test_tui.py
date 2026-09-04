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
import sys
from pathlib import Path

from rich.text import Text as RichText

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))       # depo kökü → paket olarak import
from btbond import bondsync  # noqa: E402
from btbond import runner  # noqa: E402

# ÖNEMLİ: `bondsync` ile TUI'nin gördüğü `bondsync` AYNI modül nesnesi olmak
# zorunda — testler `bondsync.survey_all`i yamalayarak ölçüm yapıyor. Eskiden
# `spec_from_file_location` ayrı bir modül nesnesi üretme riski taşıyordu;
# paket import'u bunu tanımı gereği kapatıyor.
from btbond import tui  # noqa: E402

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


def plain(cell):
    """Hücrenin METNİ — hüküm hücreleri artık Rich `Text` taşıyor.

    SÖZLEŞME AYRIMI: metin ne yazdığını, renk yalnız onu ne kadar hızlı
    bulduğunu söylüyor. Bu yüzden var olan denetimler metne bakmaya devam
    ediyor (renk değişirse kırılmasınlar), rengi ölçen ayrı bir test var
    (`t_verdict_colours`). Dize eşitliğini `Text` üstünde bırakmak ikisini
    tek denetimde birleştirir ve hangisinin bozulduğunu söylemez.
    """
    return cell.plain if hasattr(cell, "plain") else cell


def style_of(cell):
    """Hücrenin rengi — `Text` değilse renk yok demektir."""
    return str(cell.style) if hasattr(cell, "style") else None


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
        check("hüküm etiketi modelden", plain(table.get_row_at(0)[3]),
              "yalnız host'ta")
        check("yön satırın özelliği", plain(table.get_row_at(0)[4]), "→ misafir")
        check("eşleşen satırın yönü boş", plain(table.get_row_at(1)[4]), "")
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
        check("ayrışan satırın yönü YOK", plain(table.get_row_at(0)[4]), "")
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
        # Şerit tazeyken YER KAPLAMIYOR: "uyarı yok" ile "uyarı var" farkı
        # bir rengin tonu değil, satırın varlığı olmalı.
        check("taze iken bayat şeridi görünmüyor",
              app.query_one("#stale").display, False)
        app._run_done(0, "OK record aabbccddee01")
        await driver.pause()
        check("yazımdan sonra BAYAT", app.stale, True)
        check("bayat şeridi görünür oldu", app.query_one("#stale").display, True)
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
        check("diski bulunan taraf ÖLÇÜLMEDİ", plain(table.get_row_at(1)[3]),
              "ÖLÇÜLMEDİ (kapalı)")
        check("disk yolu satırda", table.get_row_at(1)[1], "/imaj/d2.qcow2")
        check("diski bulunamayan ULAŞILAMADI", plain(table.get_row_at(2)[3]),
              "ULAŞILAMADI")
        # Üç DURUM üç ayrı renk: "ölçmedim" ile "ulaşamadım" aynı renkte
        # durursa tablo ikisini tek şeye indirir.
        check("ÖLÇÜLMEDİ ile ULAŞILAMADI ayrı renkte",
              style_of(table.get_row_at(1)[3]) != style_of(table.get_row_at(2)[3]),
              True)
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
        check("hüküm ve yön yerinde", (plain(table.get_row_at(0)[3]),
                                       plain(table.get_row_at(0)[4])),
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
        # ÖLÇÜLEN ŞEY TAŞIYICI DEĞİL SAHİPLİK: komut aracın kendi ön kapısına
        # gidiyor mu, ve alt komut doğru mu. Önek `self_command()`ten geliyor
        # (kurulu paketle `btbond`, depodan `python -m btbond`), o yüzden
        # burada dize sabitlenmiyor — sabitlenseydi test kurulum biçimine
        # bağlanır ve kurulu makinede yanlış yere düşerdi.
        prefix = runner.self_command()
        check("aracın kendi ön kapısını çağırıyor (ikinci kopya yok)",
              cmd[:len(prefix)], prefix)
        check("faz komutu sync", cmd[len(prefix)], "sync")
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
        check("devir de aynı ön kapıdan", hcmd[:len(prefix)], prefix)
        check("handover komutu", tuple(hcmd[len(prefix):len(prefix) + 3]),
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
        check("hükmü OKUNDU — bond yok", plain(table.get_row_at(1)[3]),
              "OKUNDU — bond yok")
        table.move_cursor(row=1)
        app.action_detail()
        await driver.pause()
        check("ayrıntı çökmüyor", app.is_running, True)
    finally:
        await pilot.__aexit__(None, None, None)



# --- TUŞA BASARAK: eylemi çağırmak yetmez -------------------------------
#
# BU TESTİN VAR OLMA SEBEBİ ÖLÇÜLDÜ (2026-09-04): bu dosyadaki her yazma
# testi `app.action_replicate()`i **doğrudan** çağırıyordu, yani kararı
# sınıyor ama **tuşu** sınamıyordu. O boşlukta `enter` aylarca ÖLÜYDÜ — odak
# `DataTable`da ve widget tuşu kendi `select_cursor`ına harcıyor, uygulama
# bağı hiç koşmuyordu. Çare `priority=True`, ve onu ancak tuşa basan bir test
# koruyabilir. Deponun kendi dersi: hükmü kayıt değil çalışması taşır.
async def t_enter_key_reaches_action():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"}),
    ])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        check("başta modal yok", type(app.screen).__name__, "Screen")
        await driver.press("enter")
        await driver.pause()
        check("ENTER onay ekranını açtı", type(app.screen).__name__, "Confirm")
    finally:
        await pilot.__aexit__(None, None, None)


# --- Yardım TAVANA DAYANINCA KAYDIRILIR, kesilmez -----------------------
#
# 42 satırlık metin 24 satırlık terminalde sessizce kırpılıyordu: ne çubuk ne
# "devamı var" işareti vardı, ve kesilen kısım hüküm sözlüğüydü.
async def t_help_scrolls():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot = app.run_test(size=(100, 24))
    driver = await pilot.__aenter__()
    for _ in range(80):
        if app.survey is not None:
            break
        await driver.pause(0.05)
    try:
        await driver.press("question_mark")
        await driver.pause()
        box = app.screen.query_one("#helpbox")
        check("yardım kutusu kaydırılabilir", box.max_scroll_y > 0, True)
        # Metnin TAMAMI kutuda: sanal yükseklik satır sayısını karşılıyor,
        # görünen pencere ondan küçük. Eski kırpılma tam burada görünürdü.
        check("metnin tamamı kutuda duruyor",
              box.virtual_size.height >= len(tui.HELP.splitlines()), True)
        check("görünen pencere metinden küçük",
              box.size.height < box.virtual_size.height, True)
    finally:
        await pilot.__aexit__(None, None, None)


print("\n=== TUI: ENTER tuşu eyleme ULAŞIYOR mu ===")
run(t_enter_key_reaches_action())

print("\n=== TUI: yardım kırpılmıyor, kaydırılıyor ===")
run(t_help_scrolls())

print("\n=== TUI: okunmuş ama boş taraf ===")
run(t_read_empty_side())

print("\n=== TUI: → host için devir gerekmiyor ===")
run(t_no_handover_for_host())
print("\n=== TUI: otomatik bağlanmış tarafa yazım ===")
run(t_automounted_write())


# --- DÖRT HÜKÜM AYIRT EDİLEBİLİR RENKTE ---------------------------------
#
# Renk burada süs değil TARAMA HIZI: `ANAHTAR FARKLI` bu tablodaki tek yıkıcı
# satır (Enter'ı iki parmak izli bir soruya çıkarır, `--force` yazar) ve
# renksiz hâlinde `eşleşiyor` ile aynı ağırlıkta duruyordu. Test rengin
# GÜZEL olduğunu değil, dört hükmün birbirinden AYRILDIĞINI ölçüyor — dizeler
# `tui.VERDICT_STYLE`ten okunuyor, yani palet değişince test değil yalnız
# tablo değişir.
async def t_verdict_colours():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.MATCH, None,
            host={"LTK": "f"}, guest={"LTK": "f"}),
        row("AA:BB:CC:DD:EE:02", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "f"}),
        row("AA:BB:CC:DD:EE:03", bondsync.GUEST_ONLY, "to-host",
            guest={"LTK": "f"}),
        row("AA:BB:CC:DD:EE:04", bondsync.KEY_MISMATCH, None,
            host={"LTK": "h"}, guest={"LTK": "g"}, differing=["LTK"]),
    ])], cross=[{"dev": "AA:BB:CC:DD:EE:04"}])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        table = app.query_one("#rows")
        styles = [style_of(table.get_row_at(i)[3]) for i in range(4)]
        check("dört hüküm DÖRT AYRI renk", len(set(styles)), 4)
        check("hiçbiri renksiz kalmadı", None in styles, False)
        check("ANAHTAR FARKLI yıkıcı olanın rengini taşıyor", styles[3],
              tui.VERDICT_STYLE[bondsync.KEY_MISMATCH])
        check("eşleşen satır sönük (iş yok)", styles[0],
              tui.VERDICT_STYLE[bondsync.MATCH])
        # Yön oku hükmün rengini paylaşıyor: satır tek sinyal olarak okunsun.
        check("yön oku hükmün rengini paylaşıyor",
              style_of(table.get_row_at(1)[4]), styles[1])

        # AYRIŞMA hükmün PARÇASI değil, üstüne binen ayrı bir uyarı — metinde
        # de renkte de ayrı durmalı.
        cross = table.get_row_at(3)[3]
        check("AYRIŞMA eki metinde", "+AYRIŞMA" in plain(cross), True)
        check("AYRIŞMA eki kendi rengini taşıyor",
              [s.style for s in cross.spans], [tui.CROSS_STYLE])
        check("AYRIŞMA rengi hükmün renginden farklı",
              tui.CROSS_STYLE != tui.VERDICT_STYLE[bondsync.KEY_MISMATCH], True)
    finally:
        await pilot.__aexit__(None, None, None)


# --- Kip başlıkları KENARLIKTA -------------------------------------------
#
# Başlık gövdenin ilk satırıyken kutu kaydırıldığında kayboluyordu; kenarlık
# başlığı kutuyla birlikte duruyor. Ölçülen şey görünüm değil SAHİPLİK:
# onay kutusunun neyi sorduğu hâlâ ekranda mı.
async def t_modal_titles():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"})])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        app.action_replicate()
        await driver.pause()
        box = app.screen.query_one("#confirmbox")
        check("onay kutusunun başlığı kenarlıkta",
              str(box.border_title), "Replike et — → misafir")
        check("vazgeçme yolu kutunun üstünde yazılı",
              "Esc" in str(box.border_subtitle), True)
        app.screen.dismiss(None)
        await driver.pause()

        await driver.press("question_mark")
        await driver.pause()
        check("yardım kutusunun da başlığı var",
              str(app.screen.query_one("#helpbox").border_title),
              "btbond — yardım")
    finally:
        await pilot.__aexit__(None, None, None)


# --- YIKICI DÜĞME ODAKTA AÇILMAZ ----------------------------------------
#
# ÖLÇÜLMÜŞ KUSURUN TESTİ (2026-09-04, Textual 8.2.8): varsayılan otomatik odak
# ilk odaklanabilir widget'a düşüyor, yani onay ekranı `Çalıştır`
# (`variant="error"`) odakta açılıyordu — `screen.focused.id` gerçekten "run"
# okuyordu. Tabloda `enter` öncelikli bağlı olduğu için **iki ardışık enter**
# onay metnini hiç okumadan yıkıcı yazımı başlatıyordu. Deponun
# `on_data_table_row_selected`i reddetme gerekçesiyle aynı sınıf.
async def t_destructive_button_not_focused():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"}),
        row("AA:BB:CC:DD:EE:02", bondsync.KEY_MISMATCH, None,
            host={"LTK": "h"}, guest={"LTK": "g"}, differing=["LTK"]),
    ])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot, driver = await boot(app)
    try:
        ran = []
        app._run = lambda cmd, mount=None: ran.append(cmd)

        await driver.press("enter")
        await driver.pause()
        check("onay ekranı açıldı", isinstance(app.screen, tui.Confirm), True)
        check("odak YIKICI düğmede DEĞİL", app.screen.focused.id, "cancel")
        # İkinci enter: odak vazgeçte olduğu için ekran kapanır, YAZMAZ.
        await driver.press("enter")
        await driver.pause()
        check("ikinci enter yazım BAŞLATMADI", ran, [])
        check("ikinci enter ekranı kapattı", isinstance(app.screen, tui.Confirm),
              False)
        # Ölçülmüş eski davranış: `[Screen, Confirm, Confirm]` — öncelikli
        # `enter` bağı kipin üstüne İKİNCİ bir onay kutusu yığıyordu.
        check("kip üstüne kip YIĞILMADI", len(app.screen_stack), 1)

        # `ANAHTAR FARKLI` kipinde de aynısı: iki düğme de --force yolu açıyor.
        app.query_one("#rows").move_cursor(row=1)
        await driver.press("enter")
        await driver.pause()
        check("çözüm ekranı açıldı", isinstance(app.screen, tui.Resolve), True)
        check("çözümde de odak vazgeçte", app.screen.focused.id, "cancel")
        # İKİ PARMAK İZİ AYNI SÜTUNDA: bu ekranın tek işi onları
        # karşılaştırmak, ve etiketler farklı uzunlukta ("host" ↔ "misafir
        # (d1)"). Ölçüm ÇİZİLEN metinde: markup dizesinde sayarsak etiket
        # uzunluğu değil `[b]…[/b]` sabiti ölçülür ve test kendi iddiasını
        # sınamaz.
        body = RichText.from_markup(
            str(app.screen.query_one(".mbody").content)).plain
        cols = [line.index("LTK=") for line in body.splitlines() if "LTK=" in line]
        check("iki parmak izi aynı sütunda başlıyor (2 satır)", len(cols), 2)
        check("iki parmak izi aynı sütunda başlıyor", len(set(cols)), 1)
        await driver.press("enter")
        await driver.pause()
        check("çözümde ikinci enter de yazmadı", ran, [])
    finally:
        await pilot.__aexit__(None, None, None)


# --- Bant TEK SATIR: okunamayan taraf SAYILIR, anlatılmaz -----------------
#
# Ölçüldü (2026-09-04): üç taraflı kapsamda bant 110 sütuna sığmıyor ve
# `#band` **iki satıra** sarıyordu. Sarmayı üreten şey tabloyla çakışan
# bilgiydi — hangi tarafın neden okunamadığı zaten kendi renkli satırında
# duruyor.
async def t_band_single_line():
    bondsync.survey_all = lambda *a, **k: survey([
        side("win11-nvme", [row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY,
                                "to-guest", host={"LTK": "f"})]),
        {"domain": "win11-test", "error": "win11-test: domain is not running",
         "disk": {"kind": "image", "path": "/home/x/.images/win11-test.qcow2",
                  "how": "domain XML"}},
        {"domain": "win11", "error": "win11: domain is not running", "disk": None},
    ])
    app = tui.BtbondTui(["win11-nvme", "win11-test", "win11"], "/tmp", "8087:0032")
    pilot = app.run_test(size=(112, 34))
    driver = await pilot.__aenter__()
    for _ in range(80):
        if app.survey is not None:
            break
        await driver.pause(0.05)
    try:
        await driver.pause()
        check("bant TEK satır (eskiden iki)", app.query_one("#band").size.height, 1)
        # Okunamayan taraflar kaybolmadı: tabloda kendi satırlarındalar.
        table = app.query_one("#rows")
        check("okunamayan taraflar tabloda", table.row_count, 3)
        check("biri ÖLÇÜLMEDİ", plain(table.get_row_at(1)[3]), "ÖLÇÜLMEDİ (kapalı)")
        check("biri ULAŞILAMADI", plain(table.get_row_at(2)[3]), "ULAŞILAMADI")
    finally:
        await pilot.__aexit__(None, None, None)


# --- Kip ORTADA açılıyor -------------------------------------------------
#
# Ölçüldü: hizalama verilmeyince onay kutusu `Region(x=0, y=0)` ile sol üst
# köşeye yapışıyordu.
async def t_modal_centered():
    bondsync.survey_all = lambda *a, **k: survey([side("d1", [
        row("AA:BB:CC:DD:EE:01", bondsync.HOST_ONLY, "to-guest",
            host={"LTK": "fp1"})])])
    app = tui.BtbondTui(["d1"], "/tmp", "0000:0000")
    pilot = app.run_test(size=(112, 34))
    driver = await pilot.__aenter__()
    for _ in range(80):
        if app.survey is not None:
            break
        await driver.pause(0.05)
    try:
        app.action_replicate()
        await driver.pause()
        box = app.screen.query_one("#confirmbox")
        screen = app.screen.size
        check("kutu sol üst köşeye yapışmıyor", (box.region.x, box.region.y) != (0, 0),
              True)
        check("yatayda ortalı",
              abs(box.region.x - (screen.width - box.region.width) // 2) <= 1, True)
        check("dikeyde ortalı",
              abs(box.region.y - (screen.height - box.region.height) // 2) <= 1, True)
    finally:
        await pilot.__aexit__(None, None, None)


print("\n=== TUI: dört hüküm dört renk ===")
run(t_verdict_colours())
print("\n=== TUI: kip başlıkları kenarlıkta ===")
run(t_modal_titles())
print("\n=== TUI: yıkıcı düğme odakta AÇILMIYOR ===")
run(t_destructive_button_not_focused())
print("\n=== TUI: bant tek satır ===")
run(t_band_single_line())
print("\n=== TUI: kip ortada açılıyor ===")
run(t_modal_centered())

print(f"\nSONUÇ: {OK} geçti / {FAIL} başarısız")
sys.exit(1 if FAIL else 0)
