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

YETENEK EŞİTLİĞİ: karar olan hiçbir şey CLI'a özel değil — offline taraf
(`--offline DOMAIN=MOUNT`), iki fazlı akış (`s`) ve devir (`h`) burada da var.
CLI'da kalanlar ölçüm kolları (`--key-order`, `--authreq`, `--le-flags`) ve
makine çıktısı (`--json`); onları ekrana koymak kullanıcıya ölçülmemiş bir
şeyi tek tuşla yaptırmak olurdu.

Kullanım:  sudo btbond tui                        # tanımlı bütün domain'ler
           sudo btbond tui --domain AD              # tek hedef
           sudo btbond tui --offline DOMAIN=MOUNT   # elle bağlanmış taraf
"""

import argparse
import os
import subprocess
import sys
import time

from . import agentexec
from . import bluezbond
from . import bondsync
from . import sidemount
from .runner import self_command

from rich.text import Text  # noqa: E402
from textual import work  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import (Horizontal, Vertical,  # noqa: E402
                                VerticalScroll)
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import (Button, DataTable, Footer, Header,  # noqa: E402
                             RichLog, Static)

# Yazıcılar ve iki fazlı akış AYNI ARACIN alt komutları; çağrı yolu tek yerde
# çözülüyor (→ `runner.self_command`), betik yolu elde tutulmuyor. Kurulu
# paketle `btbond to-host …`, depodan koşulurken `python -m btbond to-host …`
# koşar — ve onay ekranında görünen metin ikisinde de fiilen koşan komuttur.
WRITER = {"to-host": "to-host", "to-guest": "to-guest"}

# İKİ FAZLI AKIŞ ve DEVİR burada YENİDEN YAZILMADI: TUI `sync` alt komutunu
# çağırıyor, tıpkı onun yazıcıları çağırdığı gibi. Faz sırası, faz arası
# yeniden ölçüm, ayrışan cihazın engellenmesi ve devrin `vfioctl` çağrısı tek
# sahipte kalıyor — ikinci bir kopya, biri donduğunda yıkıcı tarafta durur.

VERDICT_LABEL = {
    bondsync.MATCH: "eşleşiyor",
    bondsync.HOST_ONLY: "yalnız host'ta",
    bondsync.GUEST_ONLY: "yalnız misafirde",
    bondsync.KEY_MISMATCH: "ANAHTAR FARKLI",
}

# HÜKÜM RENGİ — ve bu kozmetik DEĞİL: `ANAHTAR FARKLI` bu tabloda yıkıcı olan
# tek satır (Enter'ı iki parmak izli bir soruya çıkarır, `--force` yazar) ve
# renksiz tabloda `eşleşiyor` ile aynı ağırlıkta duruyordu. Renk tek taşıyıcı
# değil: metin de yön oku da yerinde kalıyor, yani renk körlüğünde hüküm hâlâ
# okunur — renk yalnız tarama hızını veriyor.
#
# ANSI adları BİLEREK: terminalin kendi paletine bağlanırlar, yani kullanıcının
# temasıyla birlikte gelirler. Sabit RGB seçmek koyu temada ölçülü görünüp açık
# temada okunmaz hâle gelirdi.
VERDICT_STYLE = {
    bondsync.MATCH: "dim",              # iş yok — göz buraya takılmasın
    bondsync.HOST_ONLY: "cyan",         # → misafir
    bondsync.GUEST_ONLY: "green",       # → host
    bondsync.KEY_MISMATCH: "bold red",  # yıkıcı, ve kendiliğinden ÇÖZÜLMEZ
}

# Taraf satırları hüküm değil DURUM taşıyor; üçü de birbirinden ayrı okunmalı,
# çünkü "ölçmedim" ile "baktım, yok" aynı görünürse tablo yalan söyler.
SIDE_STYLE = {
    "unmeasured": "yellow",     # taraf var, içeriği okunmadı
    "unreachable": "bold red",  # diski bile bulunamadı
    "empty": "dim",             # okundu ve boş — olumlu bir ölçüm
}

# Taraflar arası ayrışma: satırın hükmünden AYRI bir uyarı, o yüzden ayrı renk.
CROSS_STYLE = "bold yellow"

ARROW = {"to-host": "→ host", "to-guest": "→ misafir", None: ""}

HELP = """\
[b]Bu ekran bir diff görünümü, bir yön sihirbazı değil.[/b]

  r        tazele (taraf başına ~1 sn: misafir yarısı bir guest-exec turu)
  d        seçili satırın ayrıntısı (parmak izleri, teknoloji, adres tipi)
  Enter    satırın İMA ETTİĞİ yönde replike et — yön satırın özelliği
  s        TOPLA + DAĞIT (iki fazlı akış, kapsamın tamamı)
  h        radyoyu devret (seçili satırın domain'i) — yalnız cihazları
           o tarafta KULLANMAK için; yazımın etkili olması için gerekmez
  ?        bu yardım        ↑ ↓ PgUp PgDn   bu metni kaydırır
  q        çık

[b]Kapalı misafir[/b]
  Diski bulunuyor ve kendiliğinden salt-okuma bağlanıp okunuyor (koşan VM
  asla bağlanmaz). Yazım anında RW yeniden bağlanır, bitince çözülür.

[b]→ host yazımı ve radyo[/b]
  Radyo host'tayken yazmak için devir GEREKMEZ: bluetoothd durdurulur,
  yazılır, başlatılır — adaptör yeniden kurulur ve anahtarları taze okur.
  Bedeli: host BT bağlantıları birkaç saniye düşer.

[b]Neden `s` iki faz[/b]
  Çevre birim merkez adresi başına tek bond tutar, yani bir tarafta yapılan
  eşleştirme diğer BÜTÜN tarafları bayatlatır. Host merkez olmak zorunda:
  her taraftan host'a topla, sonra host'tan kapsama dağıt. Fazlar arasında
  yeniden ölçülür — yoksa ikinci fazın girdisi bayat olur.

[b]Hükümler[/b]  (tablodaki renklerin aynısı)
  [dim]eşleşiyor[/dim]          iki taraf aynı anahtar materyalini taşıyor, iş yok
  [cyan]yalnız host'ta[/cyan]     → misafir yönünde kopyalanır
  [green]yalnız misafirde[/green]   → host yönünde kopyalanır
  [bold red]ANAHTAR FARKLI[/bold red]     kendiliğinden ÇÖZÜLMEZ; Enter iki parmak izini gösterip
                     sorar, çünkü hangi tarafın yeni olduğu ölçülemiyor ve
                     yanlış seçim çalışan bir bond'u yok eder

[b]Neden açılışta yön sorulmuyor[/b]
  Yönü veri söylüyor. Global bir yön kipi, tam o tek belirsizlikte yeniyi
  eskiyle sessizce ezerdi.

[b][bold yellow]+AYRIŞMA[/bold yellow] işareti[/b]
  Aynı cihazın anahtarı taraflar arasında farklı. Çevre birim TEK anahtar
  tutar (en son eşleştirmeninkini), yani tek başına duran taraf çalışan tek
  taraf olabilir — ama bu bir oy değil: hakem cihazı o an bağlayabilen taraf.

[b]Taraf satırları[/b]
  [yellow]ÖLÇÜLMEDİ (kapalı)[/yellow]  taraf var, içeriği okunmadı
  [bold red]ULAŞILAMADI[/bold red]         diski de bulunamadı
  [dim]OKUNDU — bond yok[/dim]    bakıldı: bu tarafta eşleşmiş cihaz yok
"""


class Help(ModalScreen):
    """Yardım — okunur ve kapanır, başka bir şey yapmaz.

    KAYDIRILABİLİR, ve bu kozmetik değil: `HELP` 42 satır ve kutunun tavanı
    ekranın %90'ı. Ölçüldü (2026-09-04, 24 satırlık terminal): eski `Vertical`
    ile metnin ~19 satırı görünüyor, gerisi **sessizce** kesiliyordu — ne
    kaydırma çubuğu ne "devamı var" işareti vardı. Kesilen kısım tam da yeni
    kullanıcının ihtiyacı: hüküm sözlüğü ve `AYRIŞMA` açıklaması.
    """

    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "kapat")]

    def compose(self) -> ComposeResult:
        box = VerticalScroll(Static(HELP, id="helptext"), id="helpbox")
        box.border_title = "btbond — yardım"
        box.border_subtitle = "↑ ↓ PgUp PgDn kaydırır · q / Esc kapatır"
        yield box


class Confirm(ModalScreen):
    """Yazma onayı — çalıştırılacak komut TAM METİN olarak gösterilir.

    Komutun görünmesi tercih değil gereklilik: bu ekranda verilen `evet`
    yıkıcı bir yazımı başlatıyor, ve kullanıcının onayladığı şeyin ne olduğu
    ekranda durmalı.

    AÇILIŞ ODAĞI **VAZGEÇ**, ve bu ölçülmüş bir kusurun düzeltmesi
    (2026-09-04, Textual 8.2.8): varsayılan otomatik odak ilk odaklanabilir
    widget'a düşüyordu, yani ekran `Çalıştır` (`variant="error"`) **odakta**
    açılıyordu — ölçüldü, `screen.focused.id == "run"`. Tabloda `enter`
    öncelikli bağlı olduğu için iki ardışık `enter` onay metnini hiç okumadan
    yıkıcı yazımı başlatıyordu. Bu, deponun `on_data_table_row_selected`i
    bilerek reddetme gerekçesinin (tek tıklama yazım başlatmasın) aynısı;
    orada kapatılan kapı burada açık kalmıştı.
    """

    AUTO_FOCUS = "#cancel"

    BINDINGS = [Binding("escape", "dismiss(None)", "vazgeç")]

    def __init__(self, title, body, command):
        super().__init__()
        self._title, self._body, self._command = title, body, command

    def compose(self) -> ComposeResult:
        box = Vertical(
            Static(self._body, classes="mbody"),
            # `markup=False`: komut metni KULLANICI VERİSİ taşıyor (yol, domain
            # adı, MAC). İçindeki bir `[…]` markup sanılırsa onay ekranında
            # gösterilen komut, koşacak komuttan sessizce ayrılır.
            Static(" ".join(str(c) for c in self._command), markup=False,
                   classes="mcmd"),
            Horizontal(Button("Çalıştır", variant="error", id="run"),
                       Button("Vazgeç", id="cancel")),
            id="confirmbox")
        box.border_title = self._title
        box.border_subtitle = "Esc = vazgeç"
        yield box

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self._command if event.button.id == "run" else None)


class Resolve(ModalScreen):
    """`ANAHTAR FARKLI` — iki parmak izi ekranda, seçim kullanıcının.

    Otomatik çözüm YOK: hangi tarafın yeni olduğu ölçülemiyor. Soru tam bu
    anda sorulur çünkü karar verebilmek için gereken iki değer ancak burada
    yan yana duruyor.

    Açılış odağı **Vazgeç** — gerekçe `Confirm`inkiyle aynı, ve burada daha
    ağır: iki yazma düğmesi de `--force` yolunu açıyor.
    """

    AUTO_FOCUS = "#cancel"

    BINDINGS = [Binding("escape", "dismiss(None)", "vazgeç")]

    def __init__(self, row, domain):
        super().__init__()
        self._row, self._domain = row, domain

    def compose(self) -> ComposeResult:
        row = self._row
        lines = [f"[b]{row['dev']}  {row['name']}[/b]",
                 f"farklı alanlar: {', '.join(row['differing']) or '(ortak anahtar yok)'}",
                 ""]
        # İKİ PARMAK İZİ AYNI SÜTUNDA BAŞLAR. Bu ekranın tek işi onları
        # karşılaştırmak, ve etiketler farklı uzunlukta ("host" ↔ "misafir
        # (win11-nvme)") — sabit boşlukla yazılınca değerler kayıyor ve göz
        # ilk farklı karakteri bulmak için satır başı arıyor.
        labels = {"host": "host", "guest": f"misafir ({self._domain})"}
        column = max(len(text) for text in labels.values()) + 2
        for side, label in (("host", labels["host"]), ("guest", labels["guest"])):
            prints = row[side]
            shown = "  ".join(f"{k}={v}" for k, v in sorted(prints.items())) if prints \
                else "(yok)"
            lines.append(f"[b]{label}[/b]" + " " * (column - len(label)) + shown)
        lines += ["",
                  "Cihaz TEK anahtar tutar — en son eşleştirmeninkini. Yani",
                  "tek başına duran taraf çalışan tek taraf OLABİLİR; bu bir oy",
                  "değil, hakem cihazı O AN bağlayabilen taraftır."]
        box = Vertical(
            Static("\n".join(lines), classes="mbody"),
            Horizontal(Button("host'u misafire yaz", variant="error", id="to-guest"),
                       Button("misafiri host'a yaz", variant="error", id="to-host"),
                       Button("Vazgeç", id="cancel")),
            id="resolvebox")
        box.border_title = "ANAHTAR FARKLI — seçim sizin"
        box.border_subtitle = "yanlış seçim çalışan bir bond'u yok eder · Esc = vazgeç"
        yield box

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)


class HandoverTarget(ModalScreen):
    """Radyo nereye gitsin? Bu bir NİYET sorusu, ölçüm değil."""

    AUTO_FOCUS = "#cancel"          # gerekçe → `Confirm.__doc__`

    BINDINGS = [Binding("escape", "dismiss(None)", "vazgeç")]

    def __init__(self, domain):
        super().__init__()
        self._domain = domain

    def compose(self) -> ComposeResult:
        box = Vertical(
            Static("Radyonun NEREDE olduğu ölçülüyor; NEREYE gideceği "
                   "ölçülemez, o yüzden sorulur.", classes="mbody"),
            Horizontal(Button("host'a", variant="warning", id="host"),
                       Button("misafire", variant="warning", id="guest"),
                       Button("Vazgeç", id="cancel")),
            id="confirmbox")
        box.border_title = f"Radyoyu devret — {self._domain}"
        box.border_subtitle = "Esc = vazgeç"
        yield box

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)


class BtbondTui(App):
    """Tek ekran: başlıkta radyonun yeri + ölçüm saati, gövdede satırlar."""

    CSS = """
    /* ÖLÇÜM BANDI — başlığın altındaki durum çubuğu. Panel zemini onu
       tablodan ayırıyor, çünkü söylediği şey satırların değil EKRANIN
       durumu: radyo nerede, veri hangi saatte ölçüldü. */
    #band { padding: 0 1; background: $panel; }

    /* BAYAT UYARISI — tazeyken YER KAPLAMAZ, yazımdan sonra tam genişlikte
       bir şerit olur (`display` Python'dan çevriliyor). Sönük bir metin
       satırı olarak durduğunda bandın gürültüsüne karışıyordu, oysa
       söylediği şey "ekranda duran veri artık yanlış". */
    #stale {
        display: none; padding: 0 1;
        background: $warning; color: auto; text-style: bold;
    }

    /* Yatay çubuk 1 satır ve SÖNÜK: varsayılan hâlinde ekranın en parlak
       öğesiydi, oysa taşıdığı bilgi en az olan — "içerik ekrandan geniş".
       Kaldırılmıyor, çünkü o bilgi gerçek: `ad` sütunu kırpılıyor. */
    DataTable {
        height: 1fr; scrollbar-size-horizontal: 1;
        scrollbar-background: $surface; scrollbar-color: $panel-lighten-2;
        scrollbar-color-hover: $accent; scrollbar-color-active: $accent;
    }
    DataTable > .datatable--header { text-style: bold; }

    /* Günlük başlıklı bir çerçevede: başlıksız hâlinde tablonun devamı gibi
       okunuyordu, oysa içeriği ayrı — koşan komutlar ve çıktıları. */
    RichLog { height: 10; border-top: solid $primary; padding: 0 1; }

    /* Kipler ORTADA. Ölçüldü (2026-09-04, Textual 8.2.8): hizalama
       verilmeyince onay kutusu `Region(x=0, y=0)` ile sol üst köşeye
       yapışıyordu — 112 sütunluk ekranda 84 sütunluk bir kutu, arkasındaki
       tabloyla aynı hizada başlayınca ekranın bir parçası gibi okunuyor. */
    ModalScreen { align: center middle; }

    /* Kipler: başlık ARTIK KENARLIKTA (`border_title`), gövdenin ilk satırı
       değil — kutunun neyi sorduğu kaydırılsa da görünür kalıyor. */
    #helpbox, #confirmbox, #resolvebox {
        width: 84; height: auto; max-height: 90%;
        border: round $primary; background: $surface; padding: 1 2;
    }
    /* Yardım tavana dayanınca KESİLMEZ, kaydırılır (→ `Help` docstring'i).
       Çubuk aynı zamanda "devamı var" işaretidir. */
    #helpbox { overflow-y: auto; scrollbar-size-vertical: 1; }
    .mbody { padding-bottom: 1; }
    /* Çalıştırılacak komut: onayın konusu bu, o yüzden kendi kutusunda ve
       taşarsa kırpılmıyor — kaydırılıyor. */
    .mcmd {
        padding: 0 1; margin-bottom: 1; overflow-x: auto;
        background: $boost; color: $text-muted;
    }
    Horizontal { height: auto; align: center middle; }
    Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "çık"),
        Binding("r", "refresh_survey", "tazele"),
        Binding("d", "detail", "ayrıntı"),
        # `priority=True` ZORUNLU, ve sebebi ölçüldü (2026-09-04, Textual
        # 8.2.8): odak `DataTable`da ve o widget `enter`ı kendi
        # `select_cursor`ına harcıyor, yani uygulama bağı **hiç** koşmuyordu ve
        # aynı sebeple Footer'da da görünmüyordu — en önemli eylem hem gizli
        # hem ölüydü. Öncelikli bağ odaklı widget'tan ÖNCE denenir.
        #
        # `on_data_table_row_selected` BİLEREK kullanılmadı: o mesajı **fare
        # tıklaması** da gönderiyor, yani tek tıklama yıkıcı bir yazımı
        # başlatırdı. Tetiği tuşta tutmak tercih değil güvenlik.
        Binding("enter", "replicate", "replike et", priority=True),
        Binding("s", "sync_all", "topla+dağıt"),
        Binding("h", "handover", "radyoyu devret"),
        Binding("question_mark", "help", "yardım"),
    ]

    def __init__(self, domains, root, usb_id, offline=None, automount=True,
                 stop_bluetooth=True):
        super().__init__()
        self.domains, self.root, self.usb_id = domains, root, usb_id
        # `{domain: mount}` — bu domain'ler ajan yerine KOVANDAN okunuyor.
        self.offline = offline or {}
        # Kullanıcı dostu varsayılanlar (CLI ile aynı): kapalı misafir
        # kendiliğinden bağlanıp okunur; `→ host` yazımında bluetoothd
        # durdurulup başlatılır, radyo devri gerekmez.
        self.automount = automount
        self.stop_bluetooth = stop_bluetooth
        self.survey = None
        self.measured_at = None
        self.stale = False          # yazımdan sonra tablo bayat sayılır
        self.rows = []              # tablo satırı -> (domain, row) eşlemesi

    def compose(self) -> ComposeResult:
        # Saat BİLEREK açık: bandın söylediği "ölçüm 14:03:11" ancak şu ana
        # göre bir yaş taşır, ve bu ekranda veri taraf başına ~1 sn'lik bir
        # turla geliyor — yani hep geçmişten.
        yield Header(show_clock=True)
        yield Static("ölçülüyor…", id="band")
        yield Static("", id="stale")
        table = DataTable(id="rows", cursor_type="row", zebra_stripes=True)
        # "tür" iki şey taşıyor ve ikisi de bir tür: bond satırında teknoloji
        # (LE / BR/EDR), taraf satırında diskin cinsi (image / partition).
        table.add_columns("taraf", "cihaz", "tür", "hüküm", "yön", "ad")
        yield table
        log = RichLog(id="log", markup=True, wrap=True)
        log.border_title = "günlük"
        yield log
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
            survey = bondsync.survey_all(
                self.domains, self.root, self.usb_id, self.offline,
                automount=self.automount and os.geteuid() == 0,
                log=lambda m: self.call_from_thread(
                    self.query_one("#log", RichLog).write, f"[dim]{m}[/dim]"))
        except Exception as exc:                       # noqa: BLE001 - UI'ye taşınıyor
            self.call_from_thread(self._measure_failed, str(exc))
            return
        self.call_from_thread(self._measure_done, survey, time.time() - started)

    def _measure_failed(self, message):
        # Yükleniyor durumu BURADA da kapanmalı: açık kalırsa ekran ölçüm hâlâ
        # sürüyormuş gibi görünür ve hata yalnız bantta kalır.
        self.query_one("#rows", DataTable).loading = False
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
        # OKUNAMAYAN TARAF BANTTA SAYILIR, ANLATILMAZ: hangisi ve neden
        # okunamadığı tabloda kendi renkli satırında duruyor (ÖLÇÜLMEDİ ↔
        # ULAŞILAMADI). Aynı olguyu iki yere yazmak, biri ilerleyince
        # öbürünü sessizce yanlış yapar — üstelik ölçüldü ki bandı taşıran
        # şey buydu: üç taraflı kapsamda bant 110 sütuna sığmayıp **iki
        # satıra** sarıyordu (2026-09-04).
        bands, unread = [], 0
        for side in survey["sides"]:
            if "error" in side:
                unread += 1
                continue
            channel = "kovan" if side.get("channel", "").startswith("offline") \
                else "ajan"
            if side.get("automounted"):
                channel += ", otomatik bağlandı"
            bands.append(f"{side['domain']} ({channel}): "
                         f"radyo {side['radio']['where']}")
        if unread:
            bands.append(f"[yellow]{unread} taraf okunamadı[/yellow] (tabloda)")
        took = f"  ({elapsed:.2f} sn)" if elapsed is not None else ""
        self.query_one("#band", Static).update(
            f"ölçüm {self.measured_at}{took}  |  " + "  |  ".join(bands))
        # Şerit tazeyken YER KAPLAMIYOR (CSS'te `display: none`), bayatken
        # görünür oluyor — "uyarı yok" ile "uyarı var" arasındaki fark bir
        # rengin tonu değil, satırın varlığı.
        stale = self.query_one("#stale", Static)
        stale.update("TABLO BAYAT: yazım yapıldı, `r` ile yeniden ölç.")
        stale.display = self.stale

        table = self.query_one("#rows", DataTable)
        table.loading = False
        table.clear()
        self.rows = []
        for side in survey["sides"]:
            if "error" in side:
                # "Bond yok" ile "ölçmedim" AYNI GÖRÜNMEZ: disk bulunabiliyorsa
                # taraf var ve `--offline <domain>=<mount>` ile okunabilir.
                self.rows.append((side["domain"], None))
                disk = side.get("disk")
                if disk:
                    table.add_row(side["domain"], disk["path"], disk["kind"],
                                  Text("ÖLÇÜLMEDİ (kapalı)",
                                       style=SIDE_STYLE["unmeasured"]), "",
                                  f"--offline {side['domain']}=<mount> ile okunur")
                else:
                    table.add_row(side["domain"], "—", "—",
                                  Text("ULAŞILAMADI",
                                       style=SIDE_STYLE["unreachable"]), "",
                                  side["error"][:58])
                continue
            if not side["rows"]:
                # DÖRDÜNCÜ DURUM: taraf OKUNDU ve boş. Satırsız bırakılırsa
                # tablodan kaybolur ve "ölçülmedi" ile karışır — oysa bu bir
                # olumlu ölçüm: bakıldı, bond yok.
                self.rows.append((side["domain"], None))
                table.add_row(side["domain"], "—", "—",
                              Text("OKUNDU — bond yok", style=SIDE_STYLE["empty"]),
                              "",
                              (side["warnings"][0][:58] if side["warnings"]
                               else "bu tarafta eşleşmiş cihaz yok"))
                continue
            for row in side["rows"]:
                style = VERDICT_STYLE[row["verdict"]]
                verdict = Text(VERDICT_LABEL[row["verdict"]], style=style)
                if row["dev"] in blocked:
                    # Ayrışma hükmün PARÇASI değil, üstüne binen bir uyarı —
                    # o yüzden kendi rengini taşıyor.
                    verdict.append(" +AYRIŞMA", style=CROSS_STYLE)
                self.rows.append((side["domain"], row))
                table.add_row(side["domain"], row["dev"], row["tech"], verdict,
                              Text(ARROW[row["direction"]], style=style),
                              row["name"])
        if not self.rows:
            self.query_one("#log", RichLog).write("karşılaştırılacak bond yok.")

    def check_action(self, action, parameters):
        """`enter` bağı KİP EKRANI AÇIKKEN devre dışı.

        ÖLÇÜLDÜ (2026-09-04, Textual 8.2.8): `priority=True` bağ uygulama
        düzeyinde olduğu için **her** ekranda ateşliyor — onay kutusu
        açıkken basılan `enter` kutuyu kapatmıyor, üstüne **ikinci bir
        onay kutusu** yığıyordu (`screen_stack` → `[Screen, Confirm,
        Confirm]`). Öncelikli olmayan bağlar (`r`, `s`, `h`, `d`, `?`) aynı
        turda ölçüldü ve sızmıyor: kip onları zaten kesiyor, yani düzeltmesi
        gereken tek bağ bu.

        `False` dönmek bağı eşleşmez kılıyor, yani tuş kipin kendi odaklı
        düğmesine ULAŞIYOR — eylemi sessizce yutmak yerine doğru sahibine
        veriyor.
        """
        if action == "replicate" and len(self.screen_stack) > 1:
            return False
        return True

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
        # Tablo ÖLÇÜM BOYUNCA yükleniyor durumunda: eski satırlar ekranda
        # kalırsa taze sanılırlar, ve tur taraf başına ~1 sn sürüyor.
        self.query_one("#rows", DataTable).loading = True
        self._measure()

    def action_help(self) -> None:
        self.push_screen(Help())

    def action_sync_all(self) -> None:
        """İki fazlı akışı KAPSAMIN TAMAMI için koştur — CLI'ı çağırarak."""
        cmd = self_command() + ["sync", "--root", self.root,
                                "--usb-id", self.usb_id]
        for domain in self.domains:
            cmd += ["--domain", domain]
        for domain, mount in self.offline.items():
            cmd += ["--offline", f"{domain}={mount}"]
        if not self.automount:
            cmd.append("--no-auto-mount")
        if not self.stop_bluetooth:
            cmd.append("--no-stop-bluetooth")
        body = (f"kapsam: {', '.join(self.domains)}\n"
                f"TOPLA (taraflardan host'a) → yeniden ölç → DAĞIT (host'tan "
                f"taraflara)\n"
                f"Taraflar arası anahtarı ayrışan cihaz hiçbir fazda otomatik "
                f"yazılmaz.")
        self.push_screen(Confirm("Topla + Dağıt", body, cmd),
                         self._run_if_confirmed)

    def action_handover(self) -> None:
        """Radyoyu devret. HEDEF KULLANICININ NİYETİ, ölçüm değil.

        Radyonun *nerede olduğu* ölçülüyor (başlıktaki bant), *nereye
        gideceği* ölçülemez — o yüzden sorulur. Devir tek hedefe olur, o
        yüzden seçili satırın domain'i kullanılıyor.
        """
        domain, _row = self._selected()
        log = self.query_one("#log", RichLog)
        if domain is None:
            log.write("[dim]devir için bir satır seçin (domain'i o belirler).[/dim]")
            return
        self.push_screen(HandoverTarget(domain),
                         lambda to: self._after_handover(domain, to))

    def _after_handover(self, domain, to_side):
        if not to_side:
            return
        cmd = self_command() + ["handover", "--to", to_side,
                                "--domain", domain, "--usb-id", self.usb_id,
                                "--root", self.root]
        self.push_screen(
            Confirm(f"Radyoyu devret → {to_side}",
                    f"domain: {domain}\nradyo bu taraftan alınıp "
                    f"`{to_side}` tarafına verilecek (vfioctl).", cmd),
            self._run_if_confirmed)

    def action_detail(self) -> None:
        domain, row = self._selected()
        log = self.query_one("#log", RichLog)
        if row is None:
            side = next((x for x in self.survey["sides"]
                         if x["domain"] == domain and "error" in x), None)
            if side and side.get("disk"):
                d = side["disk"]
                log.write(f"[b]{domain}[/b] ÖLÇÜLMEDİ — taraf var, içeriği "
                          f"okunmadı")
                log.write(f"   disk: {d['path']}  ({d['kind']}, {d['how']})")
                log.write(f"   okumak için: mount edip "
                          f"`--offline {domain}=<mount>` ile açın")
            elif side:
                log.write(f"[b]{domain}[/b] ULAŞILAMADI — disk da bulunamadı")
                log.write(f"   {side['error']}")
            else:
                read = next((x for x in self.survey["sides"]
                             if x.get("domain") == domain and "error" not in x), None)
                if read is not None:
                    log.write(f"[b]{domain}[/b] OKUNDU — bond yok  (kanal: "
                              f"{read.get('channel', '?')})")
                    for warning in read["warnings"]:
                        log.write(f"   {warning}")
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
        # `→ host` ve host radyoyu tutuyorsa: bluetoothd durdurulup başlatılır,
        # radyo devri gerekmez (CLI ile aynı davranış).
        stop_bt = (direction == "to-host" and bool(side["radio"]["host"])
                   and self.stop_bluetooth)
        # KAPI — `bondsync.write_gate`, tek sahip. TUI'nin kendi kopyası YOK.
        allowed, reason = bondsync.write_gate(side["radio"], direction,
                                              stack_restart=stop_bt)
        log = self.query_one("#log", RichLog)
        if not allowed:
            log.write(f"[red]DURDU:[/red] {reason}")
            return
        cmd = self_command() + [WRITER[direction], "--domain", domain,
                                "--root", self.root, "--only", row["dev"]]
        if forced:
            cmd.append("--force")
        if stop_bt:
            cmd.append("--stop-bluetooth")
        # Offline taraf: kullanıcı mount'u varsa o; otomatik bağlanmışsa yazım
        # anında RW yeniden bağlanır (`_run` içinde, iş parçacığında).
        mount_for_write = None
        if domain in self.offline:
            cmd += ["--offline", self.offline[domain]]
        elif side.get("automounted"):
            mount_for_write = (domain, side["disk"])
        notes = []
        if stop_bt:
            notes.append("[b]bluetoothd kısa süre duracak[/b] (host BT bağlantıları "
                         "düşer), sonra başlatılıp okunacak — radyo devri gerekmiyor.")
        if mount_for_write:
            notes.append(f"[b]{domain} diski yazım için RW bağlanacak[/b] "
                         f"({side['disk']['path']}), bitince çözülecek.")
        if forced:
            notes.append("[b]--force[/b]: hedefteki kayıt ÜZERİNE yazılacak.")
        body = (f"{row['dev']}  {row['name']}\n"
                f"yön: {ARROW[direction]}   taraf: {domain}\n"
                f"kapı: {reason}" + "".join("\n" + n for n in notes))
        self.push_screen(Confirm(f"Replike et — {ARROW[direction]}", body, cmd),
                         lambda c: self._run_if_confirmed(c, mount_for_write))

    def _run_if_confirmed(self, cmd, mount_for_write=None):
        if cmd:
            self._run(cmd, mount_for_write)

    @work(thread=True, exclusive=True)
    def _run(self, cmd, mount_for_write=None) -> None:
        log = self.query_one("#log", RichLog)
        try:
            if mount_for_write:
                domain, disk = mount_for_write
                with sidemount.Mounted(domain, disk, rw=True,
                                       log=lambda m: self.call_from_thread(
                                           log.write, f"[dim]{m}[/dim]")) as mount:
                    full = cmd + ["--offline", str(mount)]
                    self.call_from_thread(log.write, f"[b]koşuyor:[/b] {' '.join(full)}")
                    proc = subprocess.run(full, capture_output=True, text=True,
                                          timeout=600)
            else:
                self.call_from_thread(log.write, f"[b]koşuyor:[/b] {' '.join(cmd)}")
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as exc:                       # noqa: BLE001
            self.call_from_thread(log.write, f"[red]çalıştırılamadı:[/red] {exc}")
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
                        help="kapsamı bu domain(ler)e daralt; verilmezse "
                             "tanımlı bütün domain'ler (CLI ile aynı)")
    parser.add_argument("--root", default=bluezbond.ROOT, metavar="DİZİN",
                        help=f"BlueZ durum dizini (varsayılan {bluezbond.ROOT})")
    parser.add_argument("--usb-id", default=bondsync.DEFAULT_USB_ID,
                        metavar="VID:PID",
                        help="Bluetooth radyosunun USB kimliği (varsayılan "
                             f"{bondsync.DEFAULT_USB_ID}); radyonun yerini "
                             "bununla arıyor")
    # CLI ile AYNI yüzey: karar olan bir yetenek CLI'a özel kalmamalı.
    parser.add_argument("--offline", action="append", dest="offline_specs",
                        metavar="DOMAIN=MOUNT", default=[],
                        help="bu domain'i ajan yerine offline kovandan oku "
                             "(misafir KAPALI olmalı); tekrarlanabilir")
    parser.add_argument("--no-auto-mount", action="store_true",
                        help="kapalı misafirin diskini kendiliğinden bağlama")
    parser.add_argument("--no-stop-bluetooth", action="store_true",
                        help="`→ host` yazımında bluetoothd'yi durdurma")
    args = parser.parse_args()
    offline, offline_error = bondsync.parse_offline_specs(args.offline_specs)
    if offline_error:
        parser.error(offline_error)

    # KAPSAM VARSAYILANDA HERKES — `agentexec.resolve_scope`, CLI ile aynı
    # yer. Kullanıcı tek hedef istediğinde `--domain` verir.
    domains, scope_note = agentexec.resolve_scope(args.domains)
    for domain in offline:                 # offline verilen domain kapsama girer
        if domain not in domains:
            domains.append(domain)
    if scope_note:
        print(scope_note)
    # Root ŞART: host yarısı `/var/lib/bluetooth` (0700) okuyor. Root değilken
    # `is_dir()` sessizce False döner, yani "bond yok" ile "okuyamadım" aynı
    # görünür — bu depoda ödenmiş bir tuzak.
    if os.geteuid() != 0:
        sys.exit("TUI root ister (/var/lib/bluetooth 0700) — `sudo` ile çalıştırın")
    BtbondTui(domains, args.root, args.usb_id, offline,
              automount=not args.no_auto_mount,
              stop_bluetooth=not args.no_stop_bluetooth).run()
