#!/usr/bin/env python3
"""İki tarafın bond'larını tek komutla hizala (`status` ve `sync`).

Elle koşan üç adımı (yaz → devret → bağlan) tek yere alır ve **ölçülmüş bir
tuzağı kural olarak uygular: hedef tarafta radyo YOKKEN yazılır.** İki yönde
de aynı kural, iki ayrı sebeple — BlueZ bond'ları adaptör kurulurken okur,
Windows `BTHPORT` sürücü başlarken. Yanlış sırada yazmak hata vermez, sessizce
etkisiz kalır; bu yüzden sıra tavsiye değil **kapı**.

Yön satırın özelliğidir, oturumun değil → `bondsync`. `key-mismatch` satırı
(aynı cihaz, iki tarafta, farklı anahtar) **hiçbir zaman kendiliğinden
çözülmez**: hangi tarafın yeni olduğunu araç bilemez ve yanlış seçim çalışan
bir bond'u yok eder. O satır için komut basılır, kararı kullanıcı verir.

Yazma işini bu betik kendi yapmaz; her yönün tek sahibi kendi betiğidir
(`win-to-bluez.py`, `bluez-to-win.py`) ve buradan `--only <mac>` ile
çağrılır — mantığın ikinci bir kopyası çıkmasın diye.

Kapsam kullanıcının seçimi: `--domain` **tekrarlanabilir**. Verilmezse
varsayılan domain işlenir ve tanımlı başka domain'ler *dokunulmadı* diye
adlandırılır — sessizce birini seçmek, üç Windows domain'i olan bir makinede
temiz görünen bir eksik işlemdir. Ulaşılamayan taraf (kapalı misafir) atlanır,
döngüyü öldürmez.

**AKIŞ İKİ FAZLI, ve sıra zorunlu: `topla` sonra `dağıt`.** Fizik bunu
gerektiriyor — çevre birim merkez adresi başına tek bond tutar ve bütün
taraflar aynı `BD_ADDR`ı gösterir, yani bir tarafta yapılan eşleştirme diğer
**bütün** tarafları bayatlatır. Host merkez olmak zorunda (aracı o koşturuyor,
`/var/lib/bluetooth`u o tutuyor, her misafire yalnız o ulaşıyor), o yüzden
akış: her taraftan host'a **topla**, sonra host'tan kapsamın tamamına **dağıt**.

Fazlar arasında **yeniden ölçülür**: durum yazımlardan önce okunuyor, yani
`topla` host'u değiştirdiği anda `dağıt`ın girdisi bayatlar. Tek döngüde
taraf taraf iki yönü birden yürüten eski biçim bu yüzden **yakınsamıyordu** —
A'dan çekilen cihaz B'nin bayat "yalnız host'ta" kümesine hiç girmiyordu.

Kullanım:
    sudo tools/btbond-sync.py status
    sudo tools/btbond-sync.py status --domain win11-nvme --domain win11
    tools/btbond-sync.py status --json          # {"sides": […], "cross": […]}
    sudo tools/btbond-sync.py sync --dry-run          # topla + dağıt
    sudo tools/btbond-sync.py collect                 # yalnız taraflardan host'a
    sudo tools/btbond-sync.py distribute --domain a --domain b   # host'tan taraflara
    sudo tools/btbond-sync.py sync --handover         # tek domain ister
    sudo tools/btbond-sync.py handover --to host --capture-hci   # tek domain
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bluezbond  # noqa: E402
import bondsync  # noqa: E402
import hcicapture  # noqa: E402

WRITER = {
    "to-host": HERE / "win-to-bluez.py",
    "to-guest": HERE / "bluez-to-win.py",
}

# Yazmadan ÖNCE radyonun bulunmaması gereken taraf. Kapının kendisi.
FORBIDDEN_SIDE = {"to-host": "host", "to-guest": "guest"}

VERDICT_LABEL = {
    bondsync.MATCH: "eşleşiyor",
    bondsync.HOST_ONLY: "yalnız host'ta",
    bondsync.GUEST_ONLY: "yalnız misafirde",
    bondsync.KEY_MISMATCH: "ANAHTAR FARKLI",
}

DIRECTION_ARROW = {"to-host": "→ host", "to-guest": "→ misafir", None: ""}

# `--capture-hci` çıplak verildiğinde konan nöbetçi; `main` gerçek yola çevirir.
CAPTURE_DEFAULT = "@varsayilan"


def render(state):
    """Durumu insan için bas. Parmak izi basılır, anahtar baytı asla."""
    radio = state["radio"]
    lines = [
        f"domain {state['domain']}  |  adaptör {state['adapter'] or '(kesişim yok)'}",
        # Misafir sütunu HANGİ domain'i konuştuğunu söylüyor: tek domain
        # varsayan bir okuyucu, radyo başka bir domain'deyken basılan
        # `misafir=hayır`ı "radyo hiçbir misafirde değil" diye okur.
        f"radyo: {radio['where']}  "
        f"(host={'evet' if radio['host'] else 'hayır'}, "
        f"misafir[{state['domain']}]="
        f"{'okunamadı' if radio['guest'] is None else ('evet' if radio['guest'] else 'hayır')})",
        "",
    ]
    for warning in state["warnings"]:
        lines.append(f"UYARI: {warning}")
    if state["warnings"]:
        lines.append("")

    if not state["rows"]:
        lines.append("karşılaştırılacak bond yok.")
        return "\n".join(lines)

    lines.append(f"{'cihaz':<18} {'tek':<9} {'hüküm':<16} {'yön':<10} ad")
    lines.append("-" * 78)
    for row in state["rows"]:
        lines.append(
            f"{row['dev']:<18} {row['tech']:<9} {VERDICT_LABEL[row['verdict']]:<16} "
            f"{DIRECTION_ARROW[row['direction']]:<10} {row['name']}")
        if row["verdict"] == bondsync.KEY_MISMATCH:
            detail = ", ".join(row["differing"]) or "(ortak anahtar yok)"
            lines.append(f"{'':<18} farklı: {detail}")
            for side in ("host", "guest"):
                if row[side]:
                    prints = "  ".join(f"{k}={v}" for k, v in sorted(row[side].items()))
                    lines.append(f"{'':<18}   {side:<6} {prints}")
    return "\n".join(lines)


def render_cross(cross):
    """Taraflar arası ayrışmayı bas — ve azınlığın ne demek OLMADIĞINI da."""
    lines = ["", "=== TARAFLAR ARASI AYRIŞMA ===",
             "Çevre birim TEK anahtar tutar (en son eşleştirmeninkini), yani tek",
             "başına duran taraf çalışan tek taraf OLABİLİR ve çoğunluk bayat.",
             "Bu bir OY DEĞİL: hakem, cihazı O AN bağlayabilen taraftır.", ""]
    for item in cross:
        lines.append(f"{item['dev']}  {item['name']}")
        for label, info in sorted(item["labels"].items()):
            lines.append(f"  {label}")
            for fp, sides in sorted(info["groups"].items()):
                mark = "  <- AZINLIK" if all(s in info["minority"] for s in sides) else ""
                lines.append(f"    {fp}  {', '.join(sides)}{mark}")
    return "\n".join(lines)


def resolve_domains(explicit):
    """İşlenecek domain'ler + (varsa) kapsam uyarısı.

    UYARI, DURDURMA DEĞİL — ve bu, yol haritasında yazdığımdan bilerek bir
    sapma. "Birden fazla domain varken durup listele" ergonomiyi bu makinede
    hemen bozardı (üç domain tanımlı, ama gerçek misafir bir tane ve varsayılan
    kodda *bilinçli* bir seçim). Kapatılması gereken tuzak "sessizce birini
    seçmek"ti; onu kapatan şey durmak değil, **dokunulmayanı adlandırmak** —
    ve atlamanın yönü zaten güvenli: eksik işlem bond bozmaz, fazlası bozar.
    """
    if explicit:
        return list(dict.fromkeys(explicit)), None
    others = bondsync.other_domains(bondsync.DEFAULT_DOMAIN)
    if not others:
        return [bondsync.DEFAULT_DOMAIN], None
    return [bondsync.DEFAULT_DOMAIN], (
        f"--domain verilmedi: yalnız `{bondsync.DEFAULT_DOMAIN}` işlendi. "
        f"DOKUNULMAYAN {len(others)} domain tanımlı: {', '.join(others)}. "
        f"Hepsini istiyorsanız her biri için --domain verin.")


def state_path(name):
    """Çağıran kullanıcının durum dizini — `sudo` altında root'un evi DEĞİL.

    `sync` root koşuyor; `~` genişletmesi `/root`a gider ve dosya kullanıcının
    göremeyeceği bir yere düşerdi.
    """
    user = os.environ.get("SUDO_USER")
    home = Path(f"~{user}").expanduser() if user else Path.home()
    base = os.environ.get("XDG_STATE_HOME") or (home / ".local/state")
    return Path(base) / "btbond" / name


def give_back(path):
    """`sudo` altında oluşturulan yolu çağıran kullanıcıya devret.

    ÖLÇÜLDÜ (2026-09-04): yol kullanıcının evine çözülüyor ama `mkdir`/`open`
    root koştuğu için dizin de dosya da **root'un** oluyordu
    (`drwxr-xr-x root root`) — kullanıcı kendi durum dizinini yönetemiyor,
    silemiyor. Yol doğru, sahiplik yanlıştı.
    """
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid is None or gid is None:
        return
    try:
        os.chown(path, int(uid), int(gid))
    except OSError:
        pass


def capture_target(direction):
    """HCI yakalaması yalnız radyo **host'a** gelirken anlamlı.

    Ters yönde radyo host'tan çıkar: cihazlar misafirin içinde yeniden bağlanır
    ve host denetleyicisi hiçbir olay görmez.
    """
    return direction == "to-host"


def bonded_devices(root):
    """Host'ta bond'u olan cihaz MAC'leri — yakalama sırasında bağlanacaklar."""
    devices = []
    for adapter in bluezbond.list_adapters(root):
        devices += sorted(bluezbond.list_bonds(root, adapter))
    return devices


def provoke(devices, deadline):
    """Bond'lu cihazlara bağlanmayı dene, olaylar ateşlesin diye.

    ÖLÇÜLDÜ (2026-09-04): devirden sonra yalnız beklemek YETMİYOR — 40 sn'lik
    pencerede hiçbir cihaz kendiliğinden bağlanmadı ve yakalama boş döndü.
    Uzak sürüm/özellik olayları bağlantı KURULURKEN geçtiği için, bağlantı yoksa
    öğrenilecek bir şey de yok. Kapalı ya da menzil dışı cihazda deneme
    zararsızca düşer.
    """
    for dev in devices:
        if time.time() >= deadline:
            print(f"  [hci] süre doldu, kalan cihazlar denenmedi")
            return
        result = subprocess.run(["bluetoothctl", "connect", dev],
                                capture_output=True, text=True, timeout=25)
        state = "bağlandı" if result.returncode == 0 else "bağlanmadı"
        print(f"  [hci] {dev}: {state}")
        sys.stdout.flush()


def handover(direction, domain, usb_id, dry_run, capture_to=None, settle=25,
             root=bluezbond.ROOT, keep_log=False):
    """Radyoyu hedef tarafa geçir — yazımdan SONRA, çünkü hedef onu okurken alır.

    `capture_to` verilirse devir **btmon yakalamasının içinde** koşar: adaptör
    host'ta sıfırdan kurulurken bütün cihazlar taze bağlanır ve uzak sürüm /
    özellik olayları tam o anda geçer. Yakalama devirden ÖNCE başlar; sonra
    başlatmak hiçbir şey görmez.
    """
    action = "--detach" if direction == "to-host" else "--attach"
    cmd = ["vfioctl", "guest", "--name", domain, "usb", action, usb_id]
    print(f"\n[devir] {' '.join(cmd)}")
    if dry_run:
        print("  [dry-run] çalıştırılmadı"
              + (f"  (yakalama da atlandı → {capture_to})" if capture_to else ""))
        return 0

    capture = None
    if capture_to:
        capture_to.parent.mkdir(parents=True, exist_ok=True)
        give_back(capture_to.parent)
        log = capture_to.with_suffix(".btmon.log")
        print(f"  [hci] yakalama başladı → {log}"
              + ("" if keep_log else " (ayrıştırmadan sonra silinir: anahtar taşıyor)"))
        capture = hcicapture.Capture(str(log), limit_seconds=settle + 90,
                                     keep_log=keep_log).start()

    sys.stdout.flush()
    code = subprocess.run(cmd, timeout=300).returncode

    if capture:
        deadline = time.time() + settle
        print(f"  [hci] adaptörün kurulması bekleniyor, sonra bond'lu cihazlar "
              f"tetiklenecek ({settle} sn bütçe)…")
        sys.stdout.flush()
        time.sleep(min(8, settle))
        provoke(bonded_devices(root), deadline)
        remaining = max(0, int(deadline - time.time()))
        parsed = hcicapture.parse(capture.stop(settle_seconds=remaining))
        print("  [hci] toplanan uzak cihaz bilgisi:")
        for line in hcicapture.summary(parsed):
            print("  " + line)
        merged = {}
        if capture_to.exists():
            merged = json.loads(capture_to.read_text(encoding="utf-8"))
        for address, entry in parsed.items():
            merged.setdefault(address, {}).update(hcicapture.to_windows_fields(entry))
        capture_to.write_text(json.dumps(merged, indent=2, sort_keys=True),
                              encoding="utf-8")
        give_back(capture_to)
        print(f"  [hci] kaydedildi → {capture_to}")
    return code


def print_mismatch_advice(state):
    """`ANAHTAR FARKLI` satırları için komut bas — karar kullanıcının."""
    blocked = [r for r in state["rows"] if r["verdict"] == bondsync.KEY_MISMATCH]
    if not blocked:
        return
    print("Kendiliğinden çözülmeyen satırlar (hangi tarafın yeni olduğunu araç "
          "bilemez; yanlış seçim çalışan bond'u yok eder):")
    for row in blocked:
        for direction, script in WRITER.items():
            print(f"  {row['dev']}  {DIRECTION_ARROW[direction]:<10} "
                  f"sudo {script.relative_to(HERE.parent)} --only {row['dev']} --force")
    print()


def run_phase(args, state, direction, blocked_devs=()):
    """TEK yönde TEK taraf için yazımı koştur — iki fazlı akışın yapı taşı.

    `blocked_devs`: taraflar arası anahtarı ayrışan cihazlar. Bunlar hiçbir
    fazda otomatik yazılmaz, çünkü hangi tarafın yeni olduğu ölçülemiyor ve
    yanlış seçim çalışan bir bond'u yok eder. `ANAHTAR FARKLI` satırındaki
    yasağın taraflar arası hâli.
    """
    rows = [r for r in bondsync.actionable(state["rows"], direction)
            if r["dev"] not in blocked_devs]
    skipped = [r for r in bondsync.actionable(state["rows"], direction)
               if r["dev"] in blocked_devs]
    for row in skipped:
        print(f"  ATLANDI {row['dev']}  taraflar arası anahtar AYRIŞIYOR — "
              f"otomatik yazılmaz")

    if not rows:
        print(f"  yapılacak bir şey yok ({DIRECTION_ARROW[direction]}).")
        return 0

    side = FORBIDDEN_SIDE[direction]
    here = state["radio"][side]
    print(f"  {DIRECTION_ARROW[direction]}  ({len(rows)} cihaz)")

    # KAPI: hedef taraf radyoyu tutuyorsa yazma etkisiz kalır.
    #
    # ÇOKLU DOMAIN'DE DE DOĞRU, ve bu tesadüf değil: kapının sorduğu şey
    # "radyo nerede" değil, "**hedef** onu tutuyor mu". Misafir kanalı
    # yalnız adı verilen domain'in XML'ini okuduğu için ölçü tam o soruyu
    # cevaplıyor — radyo ÜÇÜNCÜ bir domain'de olsa `guest=False` doğrudur,
    # çünkü hedef Windows'un `BTHPORT` sürücüsü koşmuyor ve yazım o taraf
    # radyoyu aldığında okunacak. Buradaki `radio[side]`i "radyonun yeri"
    # sanıp düzeltmeye kalkmayın; düzeltilmesi gereken yer `where`
    # dizesinin KAPSAMI idi ve o ayrıca yazıldı (`bondsync.radio_where`).
    if here:
        print(f"  DURDU: hedef ({side}) radyoyu tutuyor. Bu sırada yazmak hata "
              f"vermez, sessizce etkisiz kalır — önce radyo öbür tarafa alınır.")
        return 1
    if here is None:
        print(f"  DURDU: hedefin ({side}) radyoyu tutup tutmadığı ÖLÇÜLEMEDİ; "
              f"kapı varsayımla geçilmez.")
        return 1

    # `--root` yazıcıya MUTLAKA geçer: geçmezse test kopyasına karşı
    # koşulan bir tur sessizce GERÇEK `/var/lib/bluetooth`a yazar —
    # durum tablosu kopyayı, yazım aslını konuşur ve ikisi arasındaki
    # fark hiçbir yerde görünmez.
    # Domain `state`ten alınır, `args`tan DEĞİL: `--domain` tekrarlanabilir
    # ve bu fonksiyon taraf başına bir kez koşuyor.
    cmd = ["sudo", str(WRITER[direction]),
           "--domain", state["domain"], "--root", args.root]
    for row in rows:
        cmd += ["--only", row["dev"]]
    if args.force:
        cmd.append("--force")
    if args.dry_run:
        cmd.append("--dry-run")
    print(f"  {' '.join(cmd)}")
    # Alt süreç terminale DOĞRUDAN yazıyor; kendi çıktımız tamponda
    # beklerse rapor yazıcının çıktısından SONRA görünür ve sıra
    # tersine döner (ölçüldü). Her alt süreçten önce boşaltılıyor.
    sys.stdout.flush()
    result = subprocess.run(cmd, timeout=600)
    exit_code = result.returncode
    if result.returncode == 0 and args.handover:
        capture_to = (args.capture_hci if args.capture_hci
                      and capture_target(direction) else None)
        if args.capture_hci and not capture_target(direction):
            print("  [hci] bu yönde yakalama atlandı: radyo host'tan ÇIKIYOR, "
                  "cihazlar misafirin içinde bağlanır ve host hiçbir olay görmez.")
        exit_code = exit_code or handover(direction, state["domain"], args.usb_id,
                                          args.dry_run, capture_to, args.settle,
                                          args.root, args.keep_hci_log)
    return exit_code


# Komut → faz sırası. `sync` iki fazı SIRAYLA koşturuyor; ayrı komutlar tek faz.
PHASES = {
    "collect": ["to-host"],
    "distribute": ["to-guest"],
    "sync": ["to-host", "to-guest"],
}

PHASE_LABEL = {
    "to-host": "TOPLA  (taraflardan host'a — host kanonik kopya olur)",
    "to-guest": "DAĞIT  (host'tan seçilen kapsama)",
}


def run_phases(args, survey, domains, directions):
    """Fazları sırayla koştur, ve faz aralarında YENİDEN ÖLÇ.

    YENİDEN ÖLÇÜM BU AKIŞIN TAMAMI. `survey_all` bütün tarafları yazımlardan
    **önce** okuyor; `topla` fazı host'u değiştirdiği anda sonraki fazın
    girdisi bayatlar. Tek koşuda yakınsama ancak fazlar arasında yeniden
    ölçülürse mümkün: eskiden tek döngü taraf taraf iki yönü birden
    yürütüyordu ve A'dan host'a çekilen bir cihaz B'nin (bayat) "yalnız
    host'ta" kümesine hiç girmiyordu — yani B tek koşu sonunda eksik kalıyordu.

    Faz **içinde** yeniden ölçüm gerekmiyor: `dağıt` yalnız misafirlere yazar
    (host sabit), `topla`nın kendi içindeki çakışması ise taraflar arası
    ayrışma olarak zaten engelleniyor (`blocked`).
    """
    exit_code = 0
    for index, direction in enumerate(directions):
        if index:
            # Cümle "host DEĞİŞTİ" demiyor: bu tur onu ölçmüyor (`--dry-run`da
            # hiçbir şey yazılmaz ve satır yine basılır). Söylediği tek şey
            # ölçümün tekrarlandığı.
            print("\n[yeniden ölçüm] fazlar arası: önceki faz host'u "
                  "değiştirmiş olabilir, sonraki faz taze durumla koşuyor.")
            sys.stdout.flush()
            survey = bondsync.survey_all(domains, args.root, args.usb_id)
        blocked = {item["dev"] for item in survey["cross"]}
        print(f"\n=== {PHASE_LABEL[direction]} ===")
        if blocked:
            print(f"  taraflar arası ayrışan {len(blocked)} cihaz bu fazda "
                  f"otomatik yazılmayacak")
        for side in survey["sides"]:
            if "error" in side:
                print(f"  domain {side['domain']}: ATLANDI — {side['error']}")
                exit_code = exit_code or 1
                continue
            print(f"  --- domain {side['domain']}")
            exit_code = run_phase(args, side, direction, blocked) or exit_code
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command",
                        choices=("status", "collect", "distribute", "sync", "handover"))
    # TEKRARLANABİLİR: kapsam kullanıcının seçimi, ve bu makinede üç Windows
    # domain'i tanımlı. Verilmezse varsayılan işlenir ve dokunulmayanlar
    # UYARI olarak adlandırılır → `resolve_domains`.
    parser.add_argument("--domain", action="append", dest="domains", metavar="AD",
                        help="işlenecek domain; tekrarlanabilir "
                             f"(varsayılan: {bondsync.DEFAULT_DOMAIN})")
    parser.add_argument("--root", default=bluezbond.ROOT)
    parser.add_argument("--usb-id", default=bondsync.DEFAULT_USB_ID)
    parser.add_argument("--direction", choices=("to-host", "to-guest"),
                        help="yalnız bu yöndeki satırları uygula (varsayılan: ikisi de)")
    parser.add_argument("--to", choices=("host", "guest"), dest="to_side",
                        help="`handover` komutu: radyonun gideceği taraf")
    parser.add_argument("--handover", action="store_true",
                        help="yazımdan sonra radyoyu hedef tarafa geçir (vfioctl)")
    # Çıplak `--capture-hci` nöbetçi verir, `main` onu varsayılan yola çevirir.
    # `const=None` yazılırsa çıplak biçim sessizce "yakalama kapalı" olurdu —
    # bayrak verilmiş görünür, hiçbir şey toplanmaz.
    parser.add_argument("--capture-hci", nargs="?", type=Path, const=Path(CAPTURE_DEFAULT),
                        default=None, metavar="DOSYA",
                        help="devir sırasında btmon ile uzak cihaz bilgisi topla "
                             "(yalnız --handover ve radyo host'a gelirken); "
                             "varsayılan hedef $XDG_STATE_HOME/btbond/remote-info.json")
    parser.add_argument("--keep-hci-log", action="store_true",
                        help="ham btmon log'unu silme (DİKKAT: bond anahtarları içerir)")
    parser.add_argument("--settle", type=int, default=25, metavar="SN",
                        help="devirden sonra cihazların bağlanması için beklenecek süre")
    parser.add_argument("--force", action="store_true",
                        help="hedefte var olan kaydın üzerine yaz")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="durumu makine-okunur bas (arayüz katmanı için)")
    args = parser.parse_args()
    domains, scope_warning = resolve_domains(args.domains)
    if args.capture_hci == Path(CAPTURE_DEFAULT):
        args.capture_hci = state_path("remote-info.json")
    if args.capture_hci and not (args.handover or args.command == "handover"):
        parser.error("--capture-hci yalnız devirle anlamlı (`handover` komutu ya da "
                     "`sync --handover`): olaylar radyo host'a gelirken, adaptör "
                     "kurulurken geçiyor")
    # Radyo TEK: birden çok tarafa aynı turda devretmek anlamsız, ve hangisi
    # olduğu tahmin edilmez.
    if args.handover and len(domains) > 1:
        parser.error(f"--handover tek domain ister, {len(domains)} verildi: "
                     f"{', '.join(domains)}")

    # Faz seçimi BURADA doğrulanıyor, tabloları basmadan önce: `parser.error`
    # stderr'e tamponsuz yazıyor, stdout ise tamponlu — sonraya bırakılırsa
    # hata mesajı kendisinden sonra basılacak tablonun ÖNÜNDE görünüyor
    # (ölçüldü). Aynı sıra tuzağı bu dosyada bir kez daha yazılı.
    directions = PHASES.get(args.command, [])
    if args.direction and directions:
        # `sync --direction to-host` == `collect`; süzme fazı daraltıyor.
        narrowed = [d for d in directions if d == args.direction]
        if not narrowed:
            parser.error(f"`{args.command}` komutu {args.direction} fazını "
                         f"içermiyor (fazları: {', '.join(directions)})")
        directions = narrowed

    if args.command == "handover":
        # Devir tek basina: yazilacak bir sey OLMASA da radyoyu tasimak ve
        # yakalamak gerekebiliyor. `sync --handover` yakalamayi ancak bir
        # yazim varken calistirir; bu komut o bagi cozuyor.
        if not args.to_side:
            parser.error("`handover` için --to host|guest gerekir")
        # Devir TEK hedefe olur: radyo tek, ve nereye gideceği kullanıcının
        # niyeti. Birden çok domain verilmişse hangisi olduğu belirsizdir ve
        # tahmin edilmez.
        if len(domains) > 1:
            parser.error(f"`handover` tek domain ister, {len(domains)} verildi: "
                         f"{', '.join(domains)}")
        if os.geteuid() != 0 and args.capture_hci and not args.dry_run:
            sys.exit("--capture-hci root ister (btmon monitör soketi)")
        direction = "to-host" if args.to_side == "host" else "to-guest"
        capture_to = args.capture_hci if capture_target(direction) else None
        if args.capture_hci and not capture_to:
            print("  [hci] bu yönde yakalama atlandı: radyo host'tan ÇIKIYOR, "
                  "cihazlar misafirin içinde bağlanır ve host hiçbir olay görmez.")
        return handover(direction, domains[0], args.usb_id, args.dry_run,
                        capture_to, args.settle, args.root, args.keep_hci_log)

    # Ulaşılamayan taraf ATLANIR, döngüyü öldürmez → `bondsync.survey_all`.
    survey = bondsync.survey_all(domains, args.root, args.usb_id)

    if args.command == "status":
        if args.json:
            # ŞEKİL DEĞİŞTİ (2026-09-04): tek durum nesnesi yerine daima
            # `{"sides": [...], "cross": [...]}`. Tek domainde eski şekli
            # döndürmek daha "uyumlu" olurdu ama şekli girdiye göre değişen
            # bir JSON, ayrıştırıcı için değişmiş bir şekilden kötüdür.
            print(json.dumps(survey, indent=2, ensure_ascii=False))
            return 0
        for side in survey["sides"]:
            if "error" in side:
                print(f"domain {side['domain']}  |  ATLANDI: {side['error']}\n")
                continue
            print(render(side))
            print()
        if survey["cross"]:
            print(render_cross(survey["cross"]))
        if scope_warning:
            print(f"\nKAPSAM: {scope_warning}")
        return 0

    if args.json:
        parser.error("--json yalnız `status` ile anlamlı")
    if os.geteuid() != 0 and not args.dry_run:
        sys.exit(f"`{args.command}` root ister (/var/lib/bluetooth 0700) — "
                 f"`sudo` ile çalıştırın")

    for side in survey["sides"]:
        if "error" not in side:
            print(render(side))
            print()
            print_mismatch_advice(side)

    exit_code = run_phases(args, survey, domains, directions)
    if survey["cross"]:
        print(render_cross(survey["cross"]))
    if scope_warning:
        print(f"\nKAPSAM: {scope_warning}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
