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
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bluezbond  # noqa: E402
import bondsync  # noqa: E402

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


def handover(direction, domain, usb_id, dry_run):
    """Radyoyu hedef tarafa geçir — yazımdan SONRA, çünkü hedef onu okurken alır."""
    action = "--detach" if direction == "to-host" else "--attach"
    cmd = ["vfioctl", "guest", "--name", domain, "usb", action, usb_id]
    print(f"\n[devir] {' '.join(cmd)}")
    if dry_run:
        print("  [dry-run] çalıştırılmadı")
        return 0
    sys.stdout.flush()
    return subprocess.run(cmd, timeout=300).returncode


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
            exit_code = exit_code or handover(direction, args.domain, args.usb_id,
                                              args.dry_run)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("status", "sync"))
    parser.add_argument("--domain", default=bondsync.DEFAULT_DOMAIN)
    parser.add_argument("--root", default=bluezbond.ROOT)
    parser.add_argument("--usb-id", default=bondsync.DEFAULT_USB_ID)
    parser.add_argument("--direction", choices=("to-host", "to-guest"),
                        help="yalnız bu yöndeki satırları uygula (varsayılan: ikisi de)")
    parser.add_argument("--handover", action="store_true",
                        help="yazımdan sonra radyoyu hedef tarafa geçir (vfioctl)")
    parser.add_argument("--force", action="store_true",
                        help="hedefte var olan kaydın üzerine yaz")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="durumu makine-okunur bas (arayüz katmanı için)")
    args = parser.parse_args()

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
