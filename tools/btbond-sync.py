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

Kullanım:
    sudo tools/btbond-sync.py status
    tools/btbond-sync.py status --json
    sudo tools/btbond-sync.py sync --dry-run
    sudo tools/btbond-sync.py sync --direction to-host --handover
    sudo tools/btbond-sync.py handover --to host --capture-hci
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
        f"radyo: {radio['where']}  "
        f"(host={'evet' if radio['host'] else 'hayır'}, "
        f"misafir={'okunamadı' if radio['guest'] is None else ('evet' if radio['guest'] else 'hayır')})",
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


def run_sync(args, state):
    rows = bondsync.actionable(state["rows"], args.direction)
    blocked = [r for r in state["rows"] if r["verdict"] == bondsync.KEY_MISMATCH]

    if blocked:
        print("Kendiliğinden çözülmeyen satırlar (hangi tarafın yeni olduğunu araç "
              "bilemez; yanlış seçim çalışan bond'u yok eder):")
        for row in blocked:
            for direction, script in WRITER.items():
                print(f"  {row['dev']}  {DIRECTION_ARROW[direction]:<10} "
                      f"sudo {script.relative_to(HERE.parent)} --only {row['dev']} --force")
        print()

    if not rows:
        print("Yapılacak bir şey yok: yönü belli satır kalmadı.")
        return 0

    directions = sorted({row["direction"] for row in rows})
    exit_code = 0
    for direction in directions:
        side = FORBIDDEN_SIDE[direction]
        here = state["radio"][side]
        picked = [r for r in rows if r["direction"] == direction]
        print(f"=== {DIRECTION_ARROW[direction]}  ({len(picked)} cihaz) ===")

        # KAPI: hedef taraf radyoyu tutuyorsa yazma etkisiz kalır.
        if here:
            print(f"  DURDU: hedef ({side}) radyoyu tutuyor. Bu sırada yazmak hata "
                  f"vermez, sessizce etkisiz kalır — önce radyo öbür tarafa alınır.")
            exit_code = 1
            continue
        if here is None:
            print(f"  DURDU: hedefin ({side}) radyoyu tutup tutmadığı ÖLÇÜLEMEDİ; "
                  f"kapı varsayımla geçilmez.")
            exit_code = 1
            continue

        # `--root` yazıcıya MUTLAKA geçer: geçmezse test kopyasına karşı
        # koşulan bir tur sessizce GERÇEK `/var/lib/bluetooth`a yazar —
        # durum tablosu kopyayı, yazım aslını konuşur ve ikisi arasındaki
        # fark hiçbir yerde görünmez.
        cmd = ["sudo", str(WRITER[direction]),
               "--domain", args.domain, "--root", args.root]
        for row in picked:
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
        exit_code = exit_code or result.returncode
        if result.returncode == 0 and args.handover:
            capture_to = (args.capture_hci if args.capture_hci
                          and capture_target(direction) else None)
            if args.capture_hci and not capture_target(direction):
                print("  [hci] bu yönde yakalama atlandı: radyo host'tan ÇIKIYOR, "
                      "cihazlar misafirin içinde bağlanır ve host hiçbir olay görmez.")
            exit_code = exit_code or handover(direction, args.domain, args.usb_id,
                                              args.dry_run, capture_to, args.settle,
                                              args.root, args.keep_hci_log)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "sync", "handover"))
    parser.add_argument("--domain", default=bondsync.DEFAULT_DOMAIN)
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
    if args.capture_hci == Path(CAPTURE_DEFAULT):
        args.capture_hci = state_path("remote-info.json")
    if args.capture_hci and not (args.handover or args.command == "handover"):
        parser.error("--capture-hci yalnız devirle anlamlı (`handover` komutu ya da "
                     "`sync --handover`): olaylar radyo host'a gelirken, adaptör "
                     "kurulurken geçiyor")

    if args.command == "handover":
        # Devir tek basina: yazilacak bir sey OLMASA da radyoyu tasimak ve
        # yakalamak gerekebiliyor. `sync --handover` yakalamayi ancak bir
        # yazim varken calistirir; bu komut o bagi cozuyor.
        if not args.to_side:
            parser.error("`handover` için --to host|guest gerekir")
        if os.geteuid() != 0 and args.capture_hci and not args.dry_run:
            sys.exit("--capture-hci root ister (btmon monitör soketi)")
        direction = "to-host" if args.to_side == "host" else "to-guest"
        capture_to = args.capture_hci if capture_target(direction) else None
        if args.capture_hci and not capture_to:
            print("  [hci] bu yönde yakalama atlandı: radyo host'tan ÇIKIYOR, "
                  "cihazlar misafirin içinde bağlanır ve host hiçbir olay görmez.")
        return handover(direction, args.domain, args.usb_id, args.dry_run,
                        capture_to, args.settle, args.root, args.keep_hci_log)

    try:
        state = bondsync.survey(args.domain, args.root, args.usb_id)
    except RuntimeError as exc:
        sys.exit(str(exc))

    if args.command == "status":
        print(json.dumps(state, indent=2, ensure_ascii=False) if args.json
              else render(state))
        return 0

    if args.json:
        parser.error("--json yalnız `status` ile anlamlı")
    if os.geteuid() != 0 and not args.dry_run:
        sys.exit("`sync` root ister (/var/lib/bluetooth 0700) — `sudo` ile çalıştırın")
    print(render(state))
    print()
    return run_sync(args, state)


if __name__ == "__main__":
    sys.exit(main())
