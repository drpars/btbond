#!/usr/bin/env python3
"""Misafir Windows'un bond'larını host BlueZ'e replike et (Windows → Linux).

Kaynak `HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters`,
kanal `qemu-guest-agent` (→ `agentexec`). Hedef
`/var/lib/bluetooth/<adaptör>/<cihaz>/info`.

Ölçülmüş kayıt defteri düzeni `winbond`, ölçülmüş BlueZ `info` biçimi
`bluezbond` modülünde — ikisinin de tek sahibi orası, bu betik yalnız yönü
kurar.

ÖLÇÜLMEMİŞ olan ve bu yüzden bayrağa bağlanan tek şey **bayt sırası**:
Windows'un `REG_BINARY` baytları BlueZ'in hex dizesiyle aynı sırada mı?
`--key-order asis` (varsayılan) ve `--key-order reverse` iki kolu verir;
doğru kol bağlantının kurulup kurulmadığından okunur.

GİZLİLİK: anahtar baytları stdout'a **basılmaz**, yalnız `info` dosyasına
0600 ile yazılır. `--dry-run` de baytları basmaz.

Kullanım:
    sudo tools/win-to-bluez.py                      # yaz (varsayılan domain)
    tools/win-to-bluez.py --dry-run                 # yalnız yapıyı göster
    sudo tools/win-to-bluez.py --key-order reverse --force
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bluezbond  # noqa: E402
import winbond  # noqa: E402
from agentexec import run_powershell  # noqa: E402

fingerprint = winbond.fingerprint


def compare_key(guest_hex, host_hex):
    """İki anahtarı karşılaştır; bayt sırasını da söyle."""
    if host_hex is None:
        return "host'ta yok", None
    guest = bytes.fromhex(guest_hex)
    host = bytes.fromhex(host_hex)
    if guest == host:
        return "EŞLEŞİYOR (aynı sıra)", fingerprint(guest_hex)
    if guest[::-1] == host:
        return "EŞLEŞİYOR (ters sıra)", fingerprint(guest_hex)
    return "EŞLEŞMİYOR", fingerprint(guest_hex)


# Doğrulanan LE anahtarları. `SIGNATURE_MAP`ten türetiliyor ki eşleme tek
# yerde kalsın: yeni bir anahtar bölümü eklendiğinde `--verify` kendiliğinden
# onu da karşılaştırsın — listeyi elle tutmak, sessizce doğrulanmayan bir
# anahtar bırakmanın yoludur.
LE_KEY_SECTIONS = (
    ("LTK", "LongTermKey"),
    ("IRK", "IdentityResolvingKey"),
) + tuple((key_field, section) for section, key_field, _ in bluezbond.SIGNATURE_MAP)


def verify(adapters, names, root, only):
    """Misafir ile host'un aynı anahtar materyalini taşıdığını doğrula."""
    problems = 0
    for adapter, entry in sorted(adapters.items()):
        print(f"adaptör {adapter}")
        for dev, link_key in sorted(entry["bredr"].items()):
            if only and dev not in only:
                continue
            info = bluezbond.read_info(root, adapter, dev)
            host_key = info["LinkKey"].get("Key") if info and info.has_section("LinkKey") else None
            verdict, fp = compare_key(link_key, host_key)
            problems += verdict != "EŞLEŞİYOR (aynı sıra)" and verdict != "EŞLEŞİYOR (ters sıra)"
            print(f"  BR/EDR {dev}  \"{names.get(dev, dev)}\"")
            print(f"    LinkKey  fp={fp}  {verdict}")

        for dev, bond in sorted(entry["le"].items()):
            if only and dev not in only:
                continue
            info = bluezbond.read_info(root, adapter, dev)
            print(f"  LE     {dev}  \"{names.get(dev, dev)}\"")
            for win_name, section in LE_KEY_SECTIONS:
                if win_name not in bond:
                    continue
                host_key = info[section].get("Key") if info and info.has_section(section) else None
                verdict, fp = compare_key(bond[win_name], host_key)
                problems += not verdict.startswith("EŞLEŞİYOR")
                print(f"    {win_name:<8} fp={fp}  {verdict}  [{section}]")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="win11-nvme")
    parser.add_argument("--root", default=bluezbond.ROOT,
                        help=f"BlueZ durum dizini (varsayılan {bluezbond.ROOT})")
    parser.add_argument("--key-order", choices=("asis", "reverse"), default="asis",
                        help="REG_BINARY baytlarının BlueZ'e yazılma sırası (ÖLÇÜLMEMİŞ)")
    parser.add_argument("--link-key-type", type=int, default=4,
                        help="BlueZ [LinkKey] Type (4 = unauthenticated combination, SSP)")
    parser.add_argument("--authenticated", type=int, default=0,
                        help="BlueZ [LongTermKey] Authenticated değeri")
    parser.add_argument("--only", action="append", default=[],
                        help="yalnız bu cihaz MAC'i (birden çok kez verilebilir)")
    parser.add_argument("--force", action="store_true", help="var olan info dosyasını yedekleyip değiştir")
    parser.add_argument("--dry-run", action="store_true", help="hiçbir şey yazma, yalnız yapıyı bas")
    parser.add_argument("--verify", action="store_true",
                        help="yazma; iki tarafın aynı anahtarı taşıdığını parmak iziyle doğrula")
    args = parser.parse_args()

    exitcode, stdout, stderr = run_powershell(args.domain, winbond.DUMP_POWERSHELL)
    if exitcode != 0:
        sys.exit(f"misafir komutu exitcode={exitcode}\n{stderr}")

    adapters, names, devices = winbond.collect(winbond.parse_dump(stdout))
    if not adapters:
        sys.exit("misafirde hiç bond yok (Keys altında adaptör anahtarı bulunamadı)")

    only = {m.upper() for m in args.only}

    if args.verify:
        problems = verify(adapters, names, args.root, only)
        print("\ntüm anahtarlar eşleşiyor." if not problems
              else f"\n{problems} anahtar eşleşmiyor ya da host'ta yok.")
        sys.exit(1 if problems else 0)

    written = 0

    for adapter, entry in sorted(adapters.items()):
        adapter_dir = Path(args.root) / adapter
        # `/var/lib/bluetooth` 0700 root: root değilken `is_dir()` sessizce
        # False döner, yani "yok" ile "okuyamadım" aynı görünür. Ayrılıyor.
        if adapter_dir.is_dir():
            state = "VAR"
        elif os.geteuid() != 0:
            state = "okunamadı (root değil)"
        else:
            state = "YOK"
        print(f"adaptör {adapter}  (host dizini: {state})")
        print(f"  CentralIRK: {'var' if entry['central_irk'] else 'yok'}"
              f"  |  BR/EDR bond: {len(entry['bredr'])}  |  LE bond: {len(entry['le'])}")

        if not adapter_dir.is_dir() and not args.dry_run:
            print("  ATLANDI: host'ta bu adaptörün dizini yok — başka radyo mu?")
            continue

        for dev, link_key in sorted(entry["bredr"].items()):
            if only and dev not in only:
                continue
            name = names.get(dev, dev)
            print(f"  BR/EDR {dev}  \"{name}\"")
            content = bluezbond.merge_preserved(
                bluezbond.read_info(args.root, adapter, dev),
                bluezbond.bredr_info(name, link_key, args.link_key_type, args.key_order))
            written += bluezbond.write_info(args.root, adapter, dev, content,
                                            args.force, args.dry_run)

        for dev, bond in sorted(entry["le"].items()):
            if only and dev not in only:
                continue
            name = names.get(dev, dev)
            addr_code, addr_source = winbond.le_address_type(bond, devices.get(dev, {}))
            # Anahtar baytları BASILMAZ: eleme ada göre değil değere göre
            # (`winbond.redact`) — ad bazlı liste `CSRK`i kaçırmıştı.
            fields = ", ".join(f"{k}={v}" for k, v in
                               sorted(winbond.redact(bond).items()))
            print(f"  LE     {dev}  \"{name}\"  "
                  f"AddressType={addr_code} (kaynak: {addr_source})")
            print(f"           [{fields}]")
            content = bluezbond.merge_preserved(
                bluezbond.read_info(args.root, adapter, dev),
                bluezbond.le_info(name, bond, args.key_order, args.authenticated,
                                  addr_code))
            written += bluezbond.write_info(args.root, adapter, dev, content,
                                            args.force, args.dry_run)

    print(f"\n{written} info dosyası {'planlandı' if args.dry_run else 'yazıldı'}.")
    if not args.dry_run and written:
        print("BlueZ bunları yalnız adaptör kurulurken okur: "
              "`systemctl restart bluetooth` (radyo host'ta olmalı).")


if __name__ == "__main__":
    main()
