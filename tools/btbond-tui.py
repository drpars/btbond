#!/usr/bin/env python3
"""btbond TUI — bir yön sihirbazı DEĞİL, bir **diff görünümü**.

TASARIM KARARI (2026-09-04): açılışta "nereden nereye" diye sorulmuyor. O soru
cevabı ölçülebilen bir şeyi kullanıcıya sorar ve tek gerçek belirsizlikte
(aynı cihaz, iki tarafta, FARKLI anahtar) yeniyi eskiyle sessizce ezer. Burada
**yön satırın özelliği**: tek tarafta duran bond o yöne kopyalanır, iki tarafta
tutan iş istemez, ayrışan satır ise **hiçbir zaman kendiliğinden çözülmez** —
onun için soru sorulur, ve tam o anda iki parmak izi ekranda durur.

TAZELEME AÇIK VE BLOKLAMIYOR, çünkü ölçüldü (2026-09-04, bu makine):

    host yarısı (dosyalar)        0,6 ms
    radyonun yeri (iki kanal)      14 ms
    misafir yarısı (guest-exec)  1073 ms   <- baskın, taraf başına
    survey (tamamı)              1086 ms

Yani bluetui'nin canlı D-Bus okuması burada **taklit edilemez**: taraf başına
~1 sn'lik bir tur var. O yüzden tazeleme `r` ile ve bir iş parçacığında koşuyor,
ve başlık verinin **hangi saatte** ölçüldüğünü söylüyor — bir saniyelik veriyi
canlıymış gibi göstermek, durum okumasının geçmişi taşımaması tuzağıdır.

TOOLKIT KARARI: Textual (Python). Sebep depo mimarisi — bu uygulama modeli
`import` ediyor, yani `--json` şeklinin ikinci bir tüketicisi doğmuyor ve
düzenin/kapının ikinci bir sahibi olmuyor. Rust/Ratatui bir süreç sınırı ve
bir ayrıştırıcı daha eklerdi.

KAPI BURADA YENİDEN YAZILMADI: `bondsync.write_gate` çağrılıyor. İki kopya
tutulsaydı biri donar, ve donmuş olan yıkıcı tarafta durur.

Kullanım:  sudo tools/btbond-tui.py [--domain AD]...
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bluezbond  # noqa: E402
import bondsync  # noqa: E402

from textual import work  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import (Button, DataTable, Footer, Header,  # noqa: E402
                             RichLog, Static)

WRITER = {
    "to-host": HERE / "win-to-bluez.py",
    "to-guest": HERE / "bluez-to-win.py",
}

VERDICT_LABEL = {
    bondsync.MATCH: "eşleşiyor",
    bondsync.HOST_ONLY: "yalnız host'ta",
    bondsync.GUEST_ONLY: "yalnız misafirde",
    bondsync.KEY_MISMATCH: "ANAHTAR FARKLI",
}

ARROW = {"to-host": "→ host", "to-guest": "→ misafir", None: ""}

HELP = """\
[b]btbond TUI — diff görünümü[/b]

  r        tazele (taraf başına ~1 sn: misafir yarısı bir guest-exec turu)
  d        seçili satırın ayrıntısı (parmak izleri, teknoloji, adres tipi)
  Enter    satırın İMA ETTİĞİ yönde replike et — yön satırın özelliği
  ?        bu yardım
  q        çık

[b]Hükümler[/b]
  eşleşiyor          iki taraf aynı anahtar materyalini taşıyor, iş yok
  yalnız host'ta     → misafir yönünde kopyalanır
  yalnız misafirde   → host yönünde kopyalanır
  ANAHTAR FARKLI     kendiliğinden ÇÖZÜLMEZ; Enter iki parmak izini gösterip
                     sorar, çünkü hangi tarafın yeni olduğu ölçülemiyor ve
                     yanlış seçim çalışan bir bond'u yok eder

[b]Neden açılışta yön sorulmuyor[/b]
  Yönü veri söylüyor. Global bir yön kipi, tam o tek belirsizlikte yeniyi
  eskiyle sessizce ezerdi.

[b]AYRIŞMA işareti[/b]
  Aynı cihazın anahtarı taraflar arasında farklı. Çevre birim TEK anahtar
  tutar (en son eşleştirmeninkini), yani tek başına duran taraf çalışan tek
  taraf olabilir — ama bu bir oy değil: hakem cihazı o an bağlayabilen taraf.
"""


class Help(ModalScreen):
    """Yardım — okunur ve kapanır, başka bir şey yapmaz."""

    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "kapat")]

    def compose(self) -> ComposeResult:
        yield Vertical(Static(HELP, id="helptext"), id="helpbox")


class Confirm(ModalScreen):
    """Yazma onayı — çalıştırılacak komut TAM METİN olarak gösterilir.

    Komutun görünmesi tercih değil gereklilik: bu ekranda verilen `evet`
    yıkıcı bir yazımı başlatıyor, ve kullanıcının onayladığı şeyin ne olduğu
    ekranda durmalı.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "vazgeç")]

    def __init__(self, title, body, command):
        super().__init__()
        self._title, self._body, self._command = title, body, command

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"[b]{self._title}[/b]", classes="mtitle"),
            Static(self._body, classes="mbody"),
            Static(f"[dim]{' '.join(str(c) for c in self._command)}[/dim]",
                   classes="mcmd"),
            Horizontal(Button("Çalıştır", variant="error", id="run"),
                       Button("Vazgeç", id="cancel")),
            id="confirmbox")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self._command if event.button.id == "run" else None)


class Resolve(ModalScreen):
    """`ANAHTAR FARKLI` — iki parmak izi ekranda, seçim kullanıcının.

    Otomatik çözüm YOK: hangi tarafın yeni olduğu ölçülemiyor. Soru tam bu
    anda sorulur çünkü karar verebilmek için gereken iki değer ancak burada
    yan yana duruyor.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "vazgeç")]

    def __init__(self, row, domain):
        super().__init__()
        self._row, self._domain = row, domain

    def compose(self) -> ComposeResult:
        row = self._row
        lines = [f"[b]{row['dev']}  {row['name']}[/b]",
                 f"farklı alanlar: {', '.join(row['differing']) or '(ortak anahtar yok)'}",
                 ""]
        for side, label in (("host", "host"), ("guest", f"misafir ({self._domain})")):
            prints = row[side]
            shown = "  ".join(f"{k}={v}" for k, v in sorted(prints.items())) if prints \
                else "(yok)"
            lines.append(f"[b]{label}[/b]  {shown}")
        lines += ["",
                  "Cihaz TEK anahtar tutar — en son eşleştirmeninkini. Yani",
                  "tek başına duran taraf çalışan tek taraf OLABİLİR; bu bir oy",
                  "değil, hakem cihazı O AN bağlayabilen taraftır."]
        yield Vertical(
            Static("\n".join(lines), classes="mbody"),
            Horizontal(Button("host'u misafire yaz", variant="error", id="to-guest"),
                       Button("misafiri host'a yaz", variant="error", id="to-host"),
                       Button("Vazgeç", id="cancel")),
            id="resolvebox")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)


class BtbondTui(App):
    """Tek ekran: başlıkta radyonun yeri + ölçüm saati, gövdede satırlar."""

    CSS = """
    #band { padding: 0 1; }
    #stale { padding: 0 1; color: $warning; }
    DataTable { height: 1fr; }
    RichLog { height: 10; border-top: solid $primary; }
    #helpbox, #confirmbox, #resolvebox {
        width: 84; height: auto; max-height: 90%;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    .mtitle { padding-bottom: 1; }
    .mbody { padding-bottom: 1; }
    .mcmd { padding-bottom: 1; }
    Horizontal { height: auto; align: center middle; }
    Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "çık"),
        Binding("r", "refresh_survey", "tazele"),
        Binding("d", "detail", "ayrıntı"),
        Binding("enter", "replicate", "replike et"),
        Binding("question_mark", "help", "yardım"),
    ]

    def __init__(self, domains, root, usb_id):
        super().__init__()
        self.domains, self.root, self.usb_id = domains, root, usb_id
        self.survey = None
        self.measured_at = None
        self.stale = False          # yazımdan sonra tablo bayat sayılır
        self.rows = []              # tablo satırı -> (domain, row) eşlemesi

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("ölçülüyor…", id="band")
        yield Static("", id="stale")
        table = DataTable(id="rows", cursor_type="row", zebra_stripes=True)
        table.add_columns("taraf", "cihaz", "tek", "hüküm", "yön", "ad")
        yield table
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "btbond"
        self.sub_title = ", ".join(self.domains)
        self.action_refresh_survey()

    # --- ölçüm ------------------------------------------------------------
    #
    # İŞ PARÇACIĞINDA, çünkü misafir yarısı taraf başına ~1073 ms sürüyor
    # (ölçüldü). Ana döngüde koşarsa arayüz o süre boyunca donar ve kullanıcı
    # hangi verinin ekranda olduğunu bilemez.
    @work(thread=True, exclusive=True)
    def _measure(self) -> None:
        started = time.time()
        try:
            survey = bondsync.survey_all(self.domains, self.root, self.usb_id)
        except Exception as exc:                       # noqa: BLE001 - UI'ye taşınıyor
            self.call_from_thread(self._measure_failed, str(exc))
            return
        self.call_from_thread(self._measure_done, survey, time.time() - started)

    def _measure_failed(self, message):
        self.query_one("#band", Static).update(f"[red]ölçüm başarısız:[/red] {message}")
        self.query_one("#log", RichLog).write(f"[red]ölçüm başarısız[/red] {message}")

    def _measure_done(self, survey, elapsed):
        self.survey = survey
        self.measured_at = time.strftime("%H:%M:%S")
        self.stale = False
        self._paint(elapsed)

    def _paint(self, elapsed=None):
        survey = self.survey
        blocked = {item["dev"] for item in survey["cross"]}

        # Radyonun yeri BAŞLIKTA duruyor, çünkü neyin koşulabilir olduğunu o
        # belirliyor — kapının girdisi bu.
        bands = []
        for side in survey["sides"]:
            if "error" in side:
                bands.append(f"[red]{side['domain']}: ATLANDI[/red]")
            else:
                bands.append(f"{side['domain']}: radyo {side['radio']['where']}")
        took = f"  ({elapsed:.2f} sn)" if elapsed is not None else ""
        self.query_one("#band", Static).update(
            f"ölçüm {self.measured_at}{took}  |  " + "  |  ".join(bands))
        self.query_one("#stale", Static).update(
            "TABLO BAYAT: yazım yapıldı, `r` ile yeniden ölç." if self.stale else "")

        table = self.query_one("#rows", DataTable)
        table.clear()
        self.rows = []
        for side in survey["sides"]:
            if "error" in side:
                self.rows.append((side["domain"], None))
                table.add_row(side["domain"], "—", "—", "ATLANDI", "",
                              side["error"][:60])
                continue
            for row in side["rows"]:
                verdict = VERDICT_LABEL[row["verdict"]]
                if row["dev"] in blocked:
                    verdict += " +AYRIŞMA"
                self.rows.append((side["domain"], row))
                table.add_row(side["domain"], row["dev"], row["tech"], verdict,
                              ARROW[row["direction"]], row["name"])
        if not self.rows:
            self.query_one("#log", RichLog).write("karşılaştırılacak bond yok.")

    def _selected(self):
        """Seçili satır → `(domain, row)`; atlanan taraf satırında `row` None."""
        table = self.query_one("#rows", DataTable)
        if not self.rows or table.cursor_row is None:
            return None, None
        if table.cursor_row >= len(self.rows):
            return None, None
        return self.rows[table.cursor_row]

    # --- eylemler ---------------------------------------------------------
    def action_refresh_survey(self) -> None:
        self.query_one("#band", Static).update(
            f"ölçülüyor… ({len(self.domains)} taraf × ~1 sn)")
        self._measure()

    def action_help(self) -> None:
        self.push_screen(Help())

    def action_detail(self) -> None:
        domain, row = self._selected()
        log = self.query_one("#log", RichLog)
        if row is None:
            log.write("[dim]ayrıntı yok (bu satır bir taraf hatası).[/dim]")
            return
        log.write(f"[b]{row['dev']}[/b] {row['name']}  ({row['tech']}, "
                  f"taraf {domain})")
        for side, label in (("host", "host"), ("guest", "misafir")):
            prints = row[side]
            log.write(f"   {label:<7} " + ("  ".join(f"{k}={v}" for k, v in
                                                     sorted(prints.items()))
                                           if prints else "(yok)"))
        if row["address_type"] is not None:
            log.write(f"   adres tipi (Devices): {row['address_type']}")
        if row["verdict"] == bondsync.KEY_MISMATCH:
            log.write(f"   [yellow]farklı:[/yellow] "
                      f"{', '.join(row['differing']) or '(ortak anahtar yok)'}")

    def action_replicate(self) -> None:
        domain, row = self._selected()
        log = self.query_one("#log", RichLog)
        if row is None:
            return
        if row["verdict"] == bondsync.MATCH:
            log.write("[dim]iş yok: iki taraf aynı anahtarı taşıyor.[/dim]")
            return
        if row["verdict"] == bondsync.KEY_MISMATCH:
            # Otomatik çözüm YOK. Soru, iki parmak izi ekrandayken sorulur.
            self.push_screen(Resolve(row, domain),
                             lambda d: self._after_resolve(domain, row, d))
            return
        self._propose(domain, row, row["direction"], forced=False)

    def _after_resolve(self, domain, row, direction):
        if direction:
            self._propose(domain, row, direction, forced=True)

    def _propose(self, domain, row, direction, forced):
        """Kapıyı sor, sonra komutu ONAY ekranında göster."""
        side = next(s for s in self.survey["sides"]
                    if s.get("domain") == domain and "error" not in s)
        # KAPI — `bondsync.write_gate`, tek sahip. TUI'nin kendi kopyası YOK.
        allowed, reason = bondsync.write_gate(side["radio"], direction)
        log = self.query_one("#log", RichLog)
        if not allowed:
            log.write(f"[red]DURDU:[/red] {reason}")
            return
        cmd = [str(WRITER[direction]), "--domain", domain,
               "--root", self.root, "--only", row["dev"]]
        if forced:
            cmd.append("--force")
        body = (f"{row['dev']}  {row['name']}\n"
                f"yön: {ARROW[direction]}   taraf: {domain}\n"
                f"kapı: {reason}"
                + ("\n[b]--force[/b]: hedefteki kayıt ÜZERİNE yazılacak."
                   if forced else ""))
        self.push_screen(Confirm(f"Replike et — {ARROW[direction]}", body, cmd),
                         self._run_if_confirmed)

    def _run_if_confirmed(self, cmd):
        if cmd:
            self._run(cmd)

    @work(thread=True, exclusive=True)
    def _run(self, cmd) -> None:
        self.call_from_thread(
            self.query_one("#log", RichLog).write,
            f"[b]koşuyor:[/b] {' '.join(cmd)}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as exc:                       # noqa: BLE001
            self.call_from_thread(self.query_one("#log", RichLog).write,
                                  f"[red]çalıştırılamadı:[/red] {exc}")
            return
        self.call_from_thread(self._run_done, proc.returncode,
                              proc.stdout or proc.stderr)

    def _run_done(self, code, output):
        log = self.query_one("#log", RichLog)
        for line in (output or "").splitlines():
            log.write(f"  {line}")
        log.write(f"[{'green' if code == 0 else 'red'}]çıkış kodu {code}[/]")
        # Tablo artık YAZIMDAN ÖNCEKİ ölçümü gösteriyor. Kendiliğinden
        # tazelemek yerine bayat işaretlenir: 1 sn'lik bir tur, ve kullanıcı
        # ekranda hangi verinin durduğunu bilmek zorunda.
        self.stale = True
        if self.survey:
            self._paint()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", action="append", dest="domains", metavar="AD",
                        help="işlenecek domain; tekrarlanabilir "
                             f"(varsayılan: {bondsync.DEFAULT_DOMAIN})")
    parser.add_argument("--root", default=bluezbond.ROOT)
    parser.add_argument("--usb-id", default=bondsync.DEFAULT_USB_ID)
    args = parser.parse_args()

    domains = list(dict.fromkeys(args.domains)) if args.domains \
        else [bondsync.DEFAULT_DOMAIN]
    # Root ŞART: host yarısı `/var/lib/bluetooth` (0700) okuyor. Root değilken
    # `is_dir()` sessizce False döner, yani "bond yok" ile "okuyamadım" aynı
    # görünür — bu depoda ödenmiş bir tuzak.
    if os.geteuid() != 0:
        sys.exit("TUI root ister (/var/lib/bluetooth 0700) — `sudo` ile çalıştırın")
    BtbondTui(domains, args.root, args.usb_id).run()


if __name__ == "__main__":
    main()
