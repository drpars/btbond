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
    sudo tools/win-to-bluez.py --offline /mnt/win   # kapalı misafirden topla
    sudo tools/win-to-bluez.py --stop-bluetooth     # radyo host'tayken, devirsiz
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bluezbond  # noqa: E402
import winbond  # noqa: E402
import agentexec  # noqa: E402
import hivebond  # noqa: E402
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
    # Verilmezse TEK tanımlı domain; birden çoksa tahmin edilmez (yazıcı
    # yıkıcıdır) → `agentexec.single_domain`.
    parser.add_argument("--domain", default=None,
                        help="misafir domain'i (verilmezse tek tanımlı olan)")
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
    # KAPALI MİSAFİRDEN TOPLAMA: kaynak ajan yerine mount edilmiş kovan.
    # `bluez-to-win.py --offline`in aynası; kanal seçimi kaynağı değiştiriyor,
    # hedef (host) aynı.
    parser.add_argument("--offline", metavar="MOUNT",
                        help="misafiri ajan yerine offline kovandan oku "
                             "(mount kökü ya da SYSTEM kovanı; misafir KAPALI)")
    # DEVİRSİZ HOST YAZIMI. BlueZ bond'ları adaptör kurulurken okur; adaptör
    # host'tayken yazmak için eskiden radyoyu bir misafire verip geri almak
    # gerekiyordu — yazılımsal bir problem için donanım devri. `bluetoothd`yi
    # durdurup yazıp başlatmak aynı "kurulum" olayını üretir. SIRA ÖNEMLİ:
    # yazımdan SONRA restart, koşan bluetoothd'nin dosyayı bellekten ezme
    # riskini kaldırmaz; ÖNCE durdurmak kaldırır.
    parser.add_argument("--stop-bluetooth", action="store_true",
                        help="yazmadan önce `bluetoothd`yi durdur, sonra başlat "
                             "(radyo host'tayken devirsiz yazım; host BT "
                             "bağlantıları birkaç saniye düşer)")
    args = parser.parse_args()
    if not args.offline:
        args.domain, why = agentexec.single_domain(args.domain, "win-to-bluez")
        if why:
            parser.error(why)

    if args.offline:
        adapters, names, devices, _svc, meta = hivebond.read_bonds(args.offline)
        print(f"offline kovan {meta['hive']}  ({meta['control_set']})")
    else:
        exitcode, stdout, stderr = run_powershell(args.domain, winbond.DUMP_POWERSHELL)
        if exitcode != 0:
            sys.exit(f"misafir komutu exitcode={exitcode}\n{stderr}")
        adapters, names, devices, _svc = winbond.collect(winbond.parse_dump(stdout))
    if not adapters:
        sys.exit("misafirde hiç bond yok (Keys altında adaptör anahtarı bulunamadı)")

    only = {m.upper() for m in args.only}

    if args.verify:
        problems = verify(adapters, names, args.root, only)
        print("\ntüm anahtarlar eşleşiyor." if not problems
              else f"\n{problems} anahtar eşleşmiyor ya da host'ta yok.")
        sys.exit(1 if problems else 0)

    if args.stop_bluetooth and not args.dry_run:
        written = with_bluetooth_stopped(
            lambda: replicate(adapters, names, devices, args, only))
    else:
        written = replicate(adapters, names, devices, args, only)

    print(f"\n{written} info dosyası {'planlandı' if args.dry_run else 'yazıldı'}.")
    if not args.dry_run and written and not args.stop_bluetooth:
        print("BlueZ bunları yalnız adaptör kurulurken okur: radyo host'ta DEĞİLSE "
              "geldiğinde okur; host'taysa `--stop-bluetooth` ile yazın.")


def bluetoothctl_bonded():
    """Host'un şu an bond'lu gördüğü MAC'ler — yazımın OKUNDUĞUNUN kanıtı."""
    proc = subprocess.run(["bluetoothctl", "devices", "Bonded"],
                          capture_output=True, text=True, timeout=30)
    return {line.split()[1].upper() for line in proc.stdout.splitlines()
            if line.startswith("Device ") and len(line.split()) >= 2}


def with_bluetooth_stopped(work):
    """`bluetoothd`yi durdur → `work()` → başlat — başlatma `finally`de.

    Başlatma her durumda koşar: yazım düşerse bile Bluetooth kapalı KALMAZ.
    Sonra `bluetoothctl devices Bonded` ile yazılanların gerçekten okunduğu
    gösterilir; hüküm dosyanın varlığı değil, yığının onu görmesi.
    """
    before = bluetoothctl_bonded()
    print("bluetoothd durduruluyor (host Bluetooth bağlantıları düşecek)…")
    stop = subprocess.run(["systemctl", "stop", "bluetooth"],
                          capture_output=True, text=True, timeout=60)
    if stop.returncode != 0:
        sys.exit(f"bluetoothd durdurulamadı: {stop.stderr.strip()}")
    try:
        return work()
    finally:
        start = subprocess.run(["systemctl", "start", "bluetooth"],
                               capture_output=True, text=True, timeout=60)
        if start.returncode != 0:
            print(f"UYARI: bluetoothd BAŞLATILAMADI: {start.stderr.strip()} — "
                  f"elle: `systemctl start bluetooth`")
        else:
            # ÖLÇÜLDÜ (2026-09-04): `start` döner dönmez sorulunca adaptör
            # henüz kurulmamış oluyor ve sayı DÜŞÜK okunuyor (3 → 1), bir
            # saniye sonra 3. Yani hemen okumak yanlış negatif üretir — durum
            # okuması olayın bitmesini beklemez. Sayı en az eskisine ulaşana
            # ya da bütçe bitene kadar yoklanır.
            deadline = time.time() + 8
            after = bluetoothctl_bonded()
            while len(after) < len(before) and time.time() < deadline:
                time.sleep(0.3)
                after = bluetoothctl_bonded()
            gained = sorted(after - before)
            print(f"bluetoothd başlatıldı; bond'lu cihaz {len(before)} → {len(after)}"
                  + (f", yeni: {', '.join(gained)}" if gained else "")
                  + ("" if len(after) >= len(before)
                     else "  UYARI: 8 sn'de eski sayıya dönmedi"))


def replicate(adapters, names, devices, args, only):
    """Misafir bond'larını host `info` dosyalarına yaz. Döner: yazılan sayısı."""
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
    return written


if __name__ == "__main__":
    # Ajan hatasını MESAJA çeviren yer burası: `agentexec` artık `sys.exit`
    # çağırmıyor (kütüphane çağıranın kararına karışmaz), o yüzden tek satır
    # hata metnini çalıştırılabilir basar — yoksa traceback'e dönerdi.
    try:
        main()
    except (agentexec.AgentError, hivebond.HiveError) as exc:
        sys.exit(str(exc))
