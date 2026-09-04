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
import agentexec  # noqa: E402
import bluezbond  # noqa: E402
import bondsync  # noqa: E402
import hcicapture  # noqa: E402
import sidemount  # noqa: E402

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

DIRECTION_ARROW = {"to-host": "→ host", "to-guest": "→ misafir", None: ""}

# `--capture-hci` çıplak verildiğinde konan nöbetçi; `main` gerçek yola çevirir.
CAPTURE_DEFAULT = "@varsayilan"


def describe_unreached(side):
    """Ulaşılamayan tarafı ÜÇ DURUMLU anlat.

    *"Bond yok"* ile *"ölçmedim"* aynı görünmesin: disk bulunabiliyorsa taraf
    **var** ve yalnız ölçülmedi — `--offline DOMAIN=MOUNT` ile okunabilir.
    Bulunamıyorsa gerçekten ulaşılamaz.
    """
    disk = side.get("disk")
    if disk:
        return (f"ÖLÇÜLMEDİ (kapalı) — disk bulundu: {disk['path']} "
                f"[{disk['how']}]; `--offline {side['domain']}=<mount>` ile okunur"
                f"\n  sebep: {side['error']}")
    return f"ULAŞILAMADI (disk da bulunamadı) — {side['error']}"


def render(state):
    """Durumu insan için bas. Parmak izi basılır, anahtar baytı asla."""
    radio = state["radio"]
    lines = [
        f"domain {state['domain']}  |  adaptör {state['adapter'] or '(kesişim yok)'}"
        f"  |  kanal {state.get('channel', '?')}"
        f"{'  (otomatik bağlandı)' if state.get('automounted') else ''}",
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
    """→ `agentexec.resolve_scope`: argümansız koşuda TANIMLI HERKES.

    Eski davranış (tek varsayılan + *"dokunulmayan N domain var"* uyarısı)
    kullanıcıyı her koşuda üç `--domain` yazmaya mahkûm ediyordu; kaldırıldı.
    Sarmalayıcı yalnız iki ön yüzün aynı yeri çağırdığını görünür kılmak için.
    """
    return agentexec.resolve_scope(explicit)


# Durum dizini + sahipliği `hcicapture`ın: dosyayı o üretiyor, yerini o
# biliyor, ve `bluez-to-win` onu TÜKETİYOR. İki yerde tanımlanırsa biri
# ilerler öbürü donar — bu deponun kuralı.
state_path = hcicapture.state_path
give_back = hcicapture.give_back


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
    öğrenilecek bir şey de yok.

    KAPALI CİHAZ "ZARARSIZCA DÜŞMÜYOR" — bu cümle burada yazılıydı ve YANLIŞTI
    (ölçüldü 2026-09-04, Xbox kolu kapalıyken): `bluetoothctl connect` asılıyor,
    `subprocess.run` **`TimeoutExpired` FIRLATIYOR**, ve istisna yakalanmadığı
    için bütün `remote-info` turu ölüyordu — traceback'le, yani toplanmış
    olabilecek cihazlar da kaydedilmeden. Zaman aşımı artık bir SONUÇ:
    kapalı cihaz atlanır, tur sürer.
    """
    for dev in devices:
        if time.time() >= deadline:
            print(f"  [hci] süre doldu, kalan cihazlar denenmedi")
            return
        try:
            result = subprocess.run(["bluetoothctl", "connect", dev],
                                    capture_output=True, text=True, timeout=25)
            state = "bağlandı" if result.returncode == 0 else "bağlanmadı"
        except subprocess.TimeoutExpired:
            state = "yanıt yok (kapalı/menzil dışı olabilir)"
        except OSError as exc:
            state = f"denenemedi ({exc})"
        print(f"  [hci] {dev}: {state}")
        sys.stdout.flush()


def start_capture(capture_to, limit_seconds, keep_log):
    """btmon yakalamasını başlat ve `Capture`ı ver (dizin + sahiplik dahil)."""
    capture_to.parent.mkdir(parents=True, exist_ok=True)
    give_back(capture_to.parent)
    log = capture_to.with_suffix(".btmon.log")
    print(f"  [hci] yakalama başladı → {log}"
          + ("" if keep_log else " (ayrıştırmadan sonra silinir: anahtar taşıyor)"))
    return hcicapture.Capture(str(log), limit_seconds=limit_seconds,
                              keep_log=keep_log).start()


def harvest(capture, capture_to, deadline, root):
    """Cihazları tetikle, uzak bilgiyi İSTE, yakalamayı durdur, birleştir.

    Devir yolu da devirsiz yol da buradan geçiyor — tek sahip. Sıra önemli:
    önce bağlantı (`provoke`), sonra bağlantı listesi, sonra komutlar. Liste
    `provoke`dan SONRA okunuyor ki taze handle'lar görünsün, ve aynı liste
    `parse`a handle→adres TOHUMU olarak veriliyor (tohumsuz `parse` var olan
    bağlantıda boş döner — ölçüldü).
    """
    provoke(bonded_devices(root), deadline)
    cons = hcicapture.connections()
    sent = hcicapture.request_remote_info(cons)
    print(f"  [hci] {len(cons)} bağlantı, {sent} sorgu yollandı")
    sys.stdout.flush()
    # EN AZ 2 sn: son olay ~1 ms'de geliyor ama btmon'un satırı dosyaya
    # düşmesi bekleniyor; hemen durdurulursa son kayıt kaybolur.
    remaining = max(2, int(deadline - time.time()))
    seed = {handle: address for handle, address, _kind in cons}
    parsed = hcicapture.parse(capture.stop(settle_seconds=remaining),
                              by_handle=seed)
    print("  [hci] toplanan uzak cihaz bilgisi:")
    for line in hcicapture.summary(parsed, kinds={a: k for _h, a, k in cons}):
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
    return parsed


def remote_info(capture_to, usb_id, settle=25, root=bluezbond.ROOT,
                keep_log=False):
    """Devirsiz toplama — radyo host'ta ve cihazlar bağlanabiliyorsa yeter.

    Bu giriş noktası, yakalamanın devir penceresine bağlı olmasının GEREKSİZ
    olduğu ölçüldükten sonra açıldı (2026-09-04): sürüm komutunu çekirdek hiç
    yollamıyor, özellik komutunu da yalnız bağlantı kurulurken yolluyor — ama
    ikisi de var olan bir bağlantıda İSTENİNCE ateşliyor.

    Kapı: radyo host'ta değilse toplanacak bir şey yok, ve boş bir yakalama
    "cihazlar cevap vermedi" gibi görünürdü — o yüzden durum önce söylenir.
    """
    # Yalnız HOST yarısı okunuyor: sorulan şey "radyo nerede" değil, "host
    # denetleyicisi var mı". Nerede olduğu burada SORULMUYOR ve iddia da
    # edilmiyor — onu `status` kapsamıyla birlikte söylüyor.
    if not bondsync.radio_where(usb_id=usb_id)["host"]:
        print("host'ta adaptör yok (`/sys/class/bluetooth` altında `hciN` yok) "
              "— uzak bilgi yalnız host denetleyicisinden okunabilir. "
              "Radyonun nerede olduğu burada sorulmadı: `status` söyler.")
        return 1
    capture = start_capture(capture_to, settle + 90, keep_log)
    harvest(capture, capture_to, time.time() + settle, root)
    return 0


def handover(direction, domain, usb_id, dry_run, capture_to=None, settle=25,
             root=bluezbond.ROOT, keep_log=False):
    """Radyoyu hedef tarafa geçir — yazımdan SONRA, çünkü hedef onu okurken alır.

    `capture_to` verilirse devir **btmon yakalamasının içinde** koşar. Devir
    artık yakalamanın ÖNKOŞULU değil (→ `remote_info`), ama hâlâ en verimli
    anı: adaptör sıfırdan kurulurken bütün cihazlar taze bağlanır, yani
    `provoke`un işi kendiliğinden yapılmış olur. Yakalama devirden ÖNCE başlar;
    sonra başlatmak özellik olaylarını kaçırır (sürüm olayı istenerek
    üretildiği için ondan etkilenmez).
    """
    action = "--detach" if direction == "to-host" else "--attach"
    cmd = ["vfioctl", "guest", "--name", domain, "usb", action, usb_id]
    print(f"\n[devir] {' '.join(cmd)}")
    if dry_run:
        print("  [dry-run] çalıştırılmadı"
              + (f"  (yakalama da atlandı → {capture_to})" if capture_to else ""))
        return 0

    capture = start_capture(capture_to, settle + 90, keep_log) if capture_to else None

    sys.stdout.flush()
    code = subprocess.run(cmd, timeout=300).returncode

    if capture:
        deadline = time.time() + settle
        print(f"  [hci] adaptörün kurulması bekleniyor, sonra bond'lu cihazlar "
              f"tetiklenecek ({settle} sn bütçe)…")
        sys.stdout.flush()
        time.sleep(min(8, settle))
        harvest(capture, capture_to, deadline, root)
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

    print(f"  {DIRECTION_ARROW[direction]}  ({len(rows)} cihaz)")

    # KAPI — ölçüsü ve gerekçesi `bondsync.write_gate`de, TEK sahipte: aynı
    # kapıyı TUI de çağırıyor ve iki kopya tutulsaydı biri donardı.
    # `→ host` ve host radyoyu tutuyorsa bluetoothd durdurulup başlatılır
    # (`--no-stop-bluetooth` verilmedikçe): yazılımsal probleme donanım devri
    # dayatılmaz.
    stop_bt = (direction == "to-host" and bool(state["radio"]["host"])
               and not args.no_stop_bluetooth)
    allowed, reason = bondsync.write_gate(state["radio"], direction,
                                          stack_restart=stop_bt)
    if not allowed:
        print(f"  DURDU: {reason}")
        return 1
    if stop_bt:
        print("  bluetoothd yazım süresince duracak (host BT bağlantıları "
              "birkaç saniye düşer), sonra yeniden başlatılıp okunacak.")

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
    if stop_bt:
        cmd.append("--stop-bluetooth")

    # OFFLINE TARAF: yazıcı kovana yazacak. Kullanıcı mount'u verdiyse o
    # geçer; taraf otomatik bağlanmışsa (salt-okuma, çözülmüş) yazım için
    # burada RW yeniden bağlanır ve yazıcı bitince çözülür.
    user_mount = getattr(args, "_offline", {}).get(state["domain"])
    if user_mount:
        cmd += ["--offline", user_mount]
        return _run_writer(cmd, args, state, direction)
    if state.get("automounted"):
        if args.dry_run:
            print(f"  [dry-run] {state['domain']} yazım için RW bağlanacaktı "
                  f"({state['disk']['path']})")
            cmd += ["--offline", "<otomatik rw mount>"]
            print(f"  {' '.join(cmd)}")
            return 0
        with sidemount.Mounted(state["domain"], state["disk"], rw=True,
                               log=lambda m: print("  " + m)) as mount:
            cmd += ["--offline", str(mount)]
            return _run_writer(cmd, args, state, direction)
    return _run_writer(cmd, args, state, direction)


def _run_writer(cmd, args, state, direction):
    """Yazıcıyı koştur; başarılıysa istenen devri yap. Döner: çıkış kodu."""
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


def run_phases(args, survey, domains, directions, offline=None):
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
            survey = bondsync.survey_all(domains, args.root, args.usb_id, offline,
                                         automount=not args.no_auto_mount
                                         and os.geteuid() == 0, log=print)
        blocked = {item["dev"] for item in survey["cross"]}
        print(f"\n=== {PHASE_LABEL[direction]} ===")
        if blocked:
            print(f"  taraflar arası ayrışan {len(blocked)} cihaz bu fazda "
                  f"otomatik yazılmayacak")
        for side in survey["sides"]:
            if "error" in side:
                print(f"  domain {side['domain']}: {describe_unreached(side)}")
                exit_code = exit_code or 1
                continue
            print(f"  --- domain {side['domain']}")
            exit_code = run_phase(args, side, direction, blocked) or exit_code
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command",
                        choices=("status", "collect", "distribute", "sync",
                                 "handover", "remote-info"))
    # TEKRARLANABİLİR ve DARALTICI: verilmezse libvirt'teki BÜTÜN domain'ler
    # işlenir (kapalı olanlar otomatik bağlanıp okunur); `--domain` kapsamı
    # daraltır ve dokunulmayanlar notta adlandırılır → `agentexec.resolve_scope`.
    parser.add_argument("--domain", action="append", dest="domains", metavar="AD",
                        help="kapsamı bu domain(ler)e daralt; verilmezse "
                             "tanımlı bütün domain'ler")
    parser.add_argument("--root", default=bluezbond.ROOT)
    parser.add_argument("--usb-id", default=bondsync.DEFAULT_USB_ID)
    # OFFLINE TARAF: misafir KAPALI, kovan host'tan mount edilmiş. Biçim
    # `DOMAIN=MOUNT`, çünkü N taraflı bir yüzeyde mount'un hangi tarafa ait
    # olduğu söylenmek zorunda → `bondsync.parse_offline_specs`.
    parser.add_argument("--offline", action="append", dest="offline_specs",
                        metavar="DOMAIN=MOUNT", default=[],
                        help="bu domain'i ajan yerine offline kovandan oku "
                             "(misafir KAPALI olmalı); tekrarlanabilir")
    # KULLANICI DOSTU VARSAYILANLAR, ikisi de kapatılabilir:
    #  - kapalı misafir otomatik bağlanır (salt-okuma, hemen çözülür;
    #    `sidemount` garantili temizlik) — elle mount + `--offline` gerekmez.
    #  - host radyoyu tutarken `→ host` yazımı için bluetoothd durdurulup
    #    başlatılır — radyoyu bir misafire verip geri almak gerekmez.
    parser.add_argument("--no-auto-mount", action="store_true",
                        help="kapalı misafirin diskini kendiliğinden bağlama")
    parser.add_argument("--no-stop-bluetooth", action="store_true",
                        help="`→ host` yazımında bluetoothd'yi durdurma "
                             "(o zaman radyo host'tayken yazım kapıda durur)")
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
                        help="btmon ile uzak cihaz bilgisi topla (devirle: "
                             "--handover; devirsiz: `remote-info` komutu); "
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
    offline, offline_error = bondsync.parse_offline_specs(args.offline_specs)
    if offline_error:
        parser.error(offline_error)
    # Offline verilen domain kapsama da girer: kullanıcı onu adlandırdı.
    for domain in offline:
        if domain not in domains:
            domains.append(domain)
    if args.capture_hci == Path(CAPTURE_DEFAULT):
        args.capture_hci = state_path("remote-info.json")
    # ESKİ KAPI KALDIRILDI, ve sebebi ölçüm: *"--capture-hci yalnız devirle
    # anlamlı"* deniyordu, çünkü olayların yalnız bağlantı kurulurken geçtiği
    # sanılıyordu. Yanlış (2026-09-04): iki olay da var olan bir bağlantıda
    # İSTENİNCE ateşliyor. Kalan kısıt yalnız şu — bir yakalama komutu
    # gerekiyor, ve `remote-info` zaten kendi yakalamasını açıyor.
    if args.capture_hci and args.command not in ("handover", "remote-info") \
            and not args.handover:
        parser.error("--capture-hci bir yakalama turu ister: `remote-info` "
                     "(devirsiz), `handover` ya da `sync --handover`")
    if args.command == "remote-info" and args.capture_hci is None:
        args.capture_hci = state_path("remote-info.json")
    # Radyo TEK: birden çok tarafa aynı turda devretmek anlamsız, ve hangisi
    # olduğu tahmin edilmez.
    if args.handover and len(domains) > 1:
        parser.error(f"--handover tek domain ister; "
                     f"{'verilen' if args.domains else 'tanımlı'} {len(domains)}: "
                     f"{', '.join(domains)} — `--domain` ile seçin")

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

    if args.command == "remote-info":
        # Devirsiz toplama. Domain kapsamı burada anlamsız: okunan şey host
        # denetleyicisi, misafir hiç konuşmuyor — o yüzden `--domain`
        # daraltması sessizce yok sayılmıyor, söyleniyor.
        if args.domains:
            print("  [not] `remote-info` host denetleyicisini okur; --domain "
                  "bu komutta bir şey daraltmıyor.")
        if os.geteuid() != 0:
            sys.exit("`remote-info` root ister (btmon monitör soketi + hcitool)")
        return remote_info(args.capture_hci, args.usb_id, args.settle,
                           args.root, args.keep_hci_log)

    if args.command == "handover":
        # Devir tek basina: yazilacak bir sey OLMASA da radyoyu tasimak ve
        # yakalamak gerekebiliyor. `sync --handover` yakalamayi ancak bir
        # yazim varken calistirir; bu komut o bagi cozuyor.
        if not args.to_side:
            parser.error("`handover` için --to host|guest gerekir")
        # Devir TEK hedefe olur: radyo tek, ve nereye gideceği kullanıcının
        # niyeti. Kapsam artık varsayılanda "herkes" olduğu için burada
        # açıkça daraltılmış olması gerekir; tahmin edilmez.
        if len(domains) > 1:
            parser.error(f"`handover` tek domain ister; "
                         f"{'verilen' if args.domains else 'tanımlı'} "
                         f"{len(domains)}: {', '.join(domains)} — `--domain` ile seçin")
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
    automount = not args.no_auto_mount and os.geteuid() == 0
    survey = bondsync.survey_all(domains, args.root, args.usb_id, offline,
                                 automount=automount, log=print)

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
                print(f"domain {side['domain']}  |  {describe_unreached(side)}\n")
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

    args._offline = offline
    exit_code = run_phases(args, survey, domains, directions, offline)
    if survey["cross"]:
        print(render_cross(survey["cross"]))
    if scope_warning:
        print(f"\nKAPSAM: {scope_warning}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
