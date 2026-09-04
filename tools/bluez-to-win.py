#!/usr/bin/env python3
"""Host BlueZ'in bond'larını misafir Windows'a replike et (Linux → Windows).

`win-to-bluez.py`'nin simetriği. Kaynak
`/var/lib/bluetooth/<adaptör>/<cihaz>/info` (root gerekir), hedef
`BTHPORT\\Parameters`.

**İKİ KANAL.** Varsayılan `qemu-guest-agent` (→ `agentexec`), misafir koşarken.
`--offline <mount>` ise kovana doğrudan yazar (→ `hivebond`) ve misafirin
**kapalı** olmasını ister — dual boot'un ve kapalı bir misafirin tek yolu,
çünkü ajan Windows'un içinde koşan bir programdır. Hangi kanal seçilirse
hedefin **mevcut durumu da o kanaldan** okunur; yanlış kanaldan okumak
`ATLANDI`/`ÜZERİNE YAZILIYOR` hükmünü sessizce tersine çevirirdi.

Yazılacak şey her iki kanalda **aynı ara temsilden** (`winbond.*_ops`) gelir;
kanallar yalnız onu render eder. İki kopya düzen tutulsaydı biri donardı.

Ölçülmüş kayıt defteri düzeni `winbond`, ölçülmüş BlueZ `info` biçimi
`bluezbond` modülünde; bu betik yalnız yönü kurar.

SIRA ÖNEMLİ — ve ters yöndeki tuzağın aynası. Windows tarafında bond'ları
okuyan şey BTHPORT sürücüsüdür ve okuma sürücü BAŞLARKEN olur. Bu yüzden
kayıt defteri radyo misafirde **değilken** yazılır; radyo sonra
`vfioctl … usb --attach` ile verilir ve yığın anahtarları taze okur. Radyo
misafirdeyken yazmak ölçülmedi: Windows çalışan yığını bellekten geri
yazabilir.

TÜRETİLMİŞ (ölçülmedi) — LE bond'unun `Address` QWORD alanı. Değer, cihazın
BD_ADDR'inin 48-bit tamsayı okunuşu olarak yazılıyor (`0C:35:26:73:33:63` →
`0x0C3526733363`), yani Windows'un `BTH_ADDR` sözleşmesi ve alt anahtar adının
sırası. Doğruluğu ancak bağlantının kurulmasından okunur.

`AuthReq` ve `CEntralIRKStatus` de BlueZ'de karşılığı olmayan alanlar: Windows
kendi eşleştirdiği bondda ne yazdıysa (45 / 1) o yazılıyor → `winbond`.

GİZLİLİK: anahtar baytları stdout'a **basılmaz**; ekrana yalnız parmak izi
(sha256'nın ilk 12 hex'i) düşer. `--dry-run` de baytları basmaz.

Kullanım:
    sudo tools/bluez-to-win.py --dry-run            # ne yazılacak
    sudo tools/bluez-to-win.py                      # misafire yaz
    sudo tools/bluez-to-win.py --only E8:07:BF:A0:55:B4 --force
    sudo tools/bluez-to-win.py --remove --only <mac> # bond'u misafirden sil
    sudo tools/bluez-to-win.py --offline /mnt/win     # kapalı misafir / dual boot
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bluezbond  # noqa: E402
import winbond  # noqa: E402
import agentexec  # noqa: E402
import hivebond  # noqa: E402
from agentexec import run_powershell  # noqa: E402


# Betik misafire `-EncodedCommand` ile gidiyor: UTF-16LE + base64, yani komut
# satırındaki uzunluk kabaca kaynağın 2,67 katı. Windows'un `CreateProcess`
# sınırı 32767 karakter; aşılırsa `guest-exec` "Failed to execute helper
# program (Invalid argument)" ile düşer (ölçüldü 2026-09-03). Bütçe kaynak
# karakteri üzerinden ve sınırın altında tutuluyor.
BATCH_BUDGET = 8000


def batches(chunks, budget=BATCH_BUDGET):
    """Parçaları, her partinin kaynak boyu bütçenin altında kalacak şekilde böl.

    Tek başına bütçeyi aşan bir parça yine de kendi partisinde gider — bölmek
    kaydı yarım bırakırdı.
    """
    batch, size = [], 0
    for chunk in chunks:
        if batch and size + len(chunk) > budget:
            yield batch
            batch, size = [], 0
        batch.append(chunk)
        size += len(chunk)
    if batch:
        yield batch


def guest_state(domain):
    """Misafirdeki mevcut durumu oku → `winbond.collect` dörtlüsü."""
    exitcode, stdout, stderr = run_powershell(domain, winbond.DUMP_POWERSHELL)
    if exitcode != 0:
        sys.exit(f"misafir okuma komutu exitcode={exitcode}\n{stderr}")
    return winbond.collect(winbond.parse_dump(stdout))


def le_fields(info, dev, authreq, irk_hex, order):
    """BlueZ LE bond'unu Windows alan sözlüğüne çevir."""
    general = info["General"] if info.has_section("General") else {}
    ltk = info["LongTermKey"]
    fields = {
        "LTK": bluezbond.section_key(info, "LongTermKey"),
        "IRK": irk_hex,
        "KeyLength": int(ltk.get("EncSize", 16)),
        "EDIV": int(ltk.get("EDiv", 0)),
        "ERand": int(ltk.get("Rand", 0)),
        "AddressType": 0 if general.get("AddressType", "public") == "public" else 1,
        "AuthReq": authreq,
        # IRK yoksa Windows'a "merkez IRK'si çözümlenmedi" demek gerekiyor;
        # değerin anlamı yorumlanmadı, yalnız Windows'un yazdığı gözlendi.
        "CEntralIRKStatus": winbond.CENTRAL_IRK_STATUS_DEFAULT if irk_hex else 0,
        "Address": int(winbond.hex12(dev), 16),
    }

    # İmza anahtarları — bu makinede ölçülmedi (iki test cihazı da dağıtmıyor).
    local_csrk = bluezbond.section_key(info, "LocalSignatureKey")
    remote_csrk = bluezbond.section_key(info, "RemoteSignatureKey")
    if local_csrk:
        fields["CSRK"] = winbond.key_hex(local_csrk, order).lower()
        fields["OutboundSignCounter"] = int(info["LocalSignatureKey"].get("Counter", 0))
    if remote_csrk:
        fields["CSRKInbound"] = winbond.key_hex(remote_csrk, order).lower()
        fields["InboundSignCounter"] = int(info["RemoteSignatureKey"].get("Counter", 0))
    return fields


def le_attrs(info, adapter, dev, address_type, with_container):
    """LE cihaz kaydının `Devices\\<mac>` alanlarını BlueZ'den türet."""
    attrs = dict(bluezbond.device_id(info))
    attrs["LEAppearance"] = bluezbond.general_int(info, "Appearance")
    attrs["LEAddressType"] = address_type
    if with_container:
        attrs["LeContainerId"] = winbond.container_guid_hex(adapter, dev)
    if info.has_section("ConnectionParameters"):
        params = info["ConnectionParameters"]
        for win_name, bluez_name in (
            ("LERemoteConnParamsIntervalMin", "MinInterval"),
            ("LERemoteConnParamsIntervalMax", "MaxInterval"),
            ("LERemoteConnParamsLatency", "Latency"),
            ("LERemoteConnParamsLSTO", "Timeout"),
        ):
            if params.get(bluez_name) is not None:
                attrs[win_name] = int(params[bluez_name])
    return attrs


def plan(root, adapter, bonds, guest, only, order, authreq, force, container=True,
         guest_svc=None, le_flags_override=None):
    """Yazılacak işleri belirle. Döner: (parçalar, rapor satırları).

    `guest_svc`: hedefin `ServicesFor<adaptör>` alanları (→ `winbond.collect`).
    `LEFlags` **cihaza göre değişiyor ve türetilemedi** → `winbond.LEFLAGS_NOTU`,
    o yüzden sıra: kullanıcı verdiyse o, hedefte varsa **korunur**, ikisi de
    yoksa **hiç yazılmaz** ve rapor bunu söyler.
    """
    chunks, report = [], []
    guest_svc = guest_svc or {}
    guest_entry = guest.get(adapter, {"bredr": {}, "le": {}, "central_irk": None})

    for dev, info in sorted(bonds.items()):
        if only and dev not in only:
            continue
        name = bluezbond.device_name(info, dev)
        techs = bluezbond.technologies(info)

        link_key = bluezbond.section_key(info, "LinkKey")
        if link_key:
            exists = dev in guest_entry["bredr"]
            fp = winbond.fingerprint(link_key)
            if exists and not force:
                report.append(f"  BR/EDR {dev}  \"{name}\"  ATLANDI (misafirde var, --force yok)")
            else:
                uuids = bluezbond.services(info)
                attrs = {"COD": bluezbond.general_int(info, "Class")}
                sdp = bluezbond.service_records(root, adapter, dev)
                chunks.append(winbond.bredr_ops(
                    adapter, dev, winbond.key_hex(link_key, order).lower()))
                chunks.append(winbond.device_record_ops(
                    adapter, dev, name, False, attrs, uuids, sdp))
                report.append(f"  BR/EDR {dev}  \"{name}\"  LinkKey fp={fp}"
                              f"{'  (ÜZERİNE YAZILIYOR)' if exists else ''}")
                report.append(f"           [COD={attrs['COD']}, profil={len(uuids)}, "
                              f"SDP kaydı={len(sdp)}]")
                if not sdp:
                    report.append("           UYARI: BlueZ cache'inde SDP kaydı yok — "
                                  "profil devnode'ları doğmayabilir")

        ltk = bluezbond.section_key(info, "LongTermKey")
        if ltk:
            irk = bluezbond.section_key(info, "IdentityResolvingKey")
            exists = dev in guest_entry["le"]
            if exists and not force:
                report.append(f"  LE     {dev}  \"{name}\"  ATLANDI (misafirde var, --force yok)")
            else:
                fields = le_fields(info, dev, authreq,
                                   winbond.key_hex(irk, order).lower() if irk else None, order)
                fields["LTK"] = winbond.key_hex(ltk, order).lower()
                attrs = le_attrs(info, adapter, dev, fields["AddressType"], container)
                # `LEFlags`: kullanıcı > hedefte var olan > hiç yazma.
                existing = winbond.existing_le_flags(guest_svc, dev, adapter)
                le_flags = le_flags_override if le_flags_override is not None else existing
                if le_flags_override is not None:
                    flags_note = f"LEFlags=0x{le_flags:08X} (--le-flags)"
                elif existing is not None:
                    flags_note = f"LEFlags=0x{existing:08X} (hedeften KORUNDU)"
                else:
                    flags_note = ("LEFlags=YAZILMIYOR (cihaza göre değişiyor, "
                                  "türetilemedi; yokluğunun etkisi ölçülmedi)")
                chunks.append(winbond.le_ops(adapter, dev, fields))
                chunks.append(winbond.device_record_ops(
                    adapter, dev, name, True, attrs, [], le_flags=le_flags))
                extra = ", ".join(f"{k}={fields[k]}" for k in
                                  ("KeyLength", "EDIV", "ERand", "AddressType",
                                   "AuthReq", "CEntralIRKStatus", "Address"))
                report.append(f"  LE     {dev}  \"{name}\"  LTK fp={winbond.fingerprint(ltk)}"
                              f"  IRK fp={winbond.fingerprint(irk) if irk else '-'}"
                              f"{'  (ÜZERİNE YAZILIYOR)' if exists else ''}")
                report.append(f"           [{extra}]")
                shown = {k: (f"{v[:8]}…" if k == "LeContainerId" else v)
                         for k, v in sorted(attrs.items()) if v is not None}
                report.append(f"           [{', '.join(f'{k}={v}' for k, v in shown.items())}]")
                report.append(f"           {flags_note}")

        if not link_key and not ltk:
            report.append(f"  ATLANDI {dev}  \"{name}\"  — anahtar bölümü yok "
                          f"(teknoloji: {','.join(sorted(techs)) or 'bilinmiyor'})")

    return chunks, report


def plan_removals(adapter, guest, only):
    """Misafirden silinecek bond'ları belirle."""
    chunks, report = [], []
    guest_entry = guest.get(adapter, {"bredr": {}, "le": {}, "central_irk": None})
    for dev in sorted(set(guest_entry["bredr"]) | set(guest_entry["le"])):
        if only and dev not in only:
            continue
        is_le = dev in guest_entry["le"]
        chunks.append(winbond.remove_ops(adapter, dev, is_le))
        report.append(f"  SİL    {dev}  ({'LE' if is_le else 'BR/EDR'})")
    return chunks, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default=agentexec.DEFAULT_DOMAIN)
    parser.add_argument("--root", default=bluezbond.ROOT,
                        help=f"BlueZ durum dizini (varsayılan {bluezbond.ROOT})")
    parser.add_argument("--adapter", help="host adaptör MAC'i (varsayılan: tek olanı)")
    parser.add_argument("--key-order", choices=("asis", "reverse"), default="asis",
                        help="anahtar baytlarının kayıt defterine yazılma sırası")
    parser.add_argument("--authreq", type=int, default=winbond.AUTHREQ_DEFAULT,
                        help=f"LE bond `AuthReq` alanı (varsayılan {winbond.AUTHREQ_DEFAULT}, "
                             "Windows'un kendi yazdığı değer — anlamı yorumlanmadı)")
    parser.add_argument("--only", action="append", default=[],
                        help="yalnız bu cihaz MAC'i (birden çok kez verilebilir)")
    parser.add_argument("--force", action="store_true",
                        help="misafirde zaten olan bond'un üzerine yaz")
    parser.add_argument("--no-container-id", action="store_true",
                        help="LE cihaz kaydına `LeContainerId` yazma (gerekliliği ölçülmedi)")
    # `LEFlags` cihaza göre değişiyor ve n=2'de TÜRETİLEMEDİ
    # (→ `winbond.LEFLAGS_NOTU`). Sabit yazmak ölçülmüş biçimde yanlış olurdu,
    # o yüzden değer ya hedeften korunur ya buradan verilir ya hiç yazılmaz.
    parser.add_argument("--le-flags", type=lambda v: int(v, 0), metavar="DEĞER",
                        help="LE cihaz kaydına yazılacak `LEFlags` (0x… kabul "
                             "edilir); verilmezse hedefteki korunur, o da "
                             "yoksa alan hiç yazılmaz")
    parser.add_argument("--remove", action="store_true",
                        help="yazma; misafirdeki bond'ları sil (--only ile daraltılır)")
    parser.add_argument("--dry-run", action="store_true",
                        help="hiçbir şey yazma, yalnız planı bas")
    # OFFLINE TARAF: misafir KAPALI, kovan host'tan mount edilmiş. Dual boot'un
    # ve kapalı bir misafirin tek yolu — ajan, Windows'un içinde koşan bir
    # program olduğu için orada yok. Yazma sırası kapısı burada tanımı gereği
    # sağlanıyor: kapalı Windows radyoyu tutamaz.
    parser.add_argument("--offline", metavar="MOUNT",
                        help="ajan yerine offline kovana yaz: verilen mount kökü "
                             "(ya da doğrudan SYSTEM kovanı). Misafir KAPALI olmalı.")
    args = parser.parse_args()
    if args.offline and args.key_order == "reverse":
        # Tek sebep: ters sıra kolu hiçbir yönde ölçülmedi, ve offline yolda
        # onu ilk kez denemek iki ölçülmemiş şeyi birden değiştirmek olur.
        parser.error("--offline ile --key-order reverse birlikte ölçülmedi")

    adapters = bluezbond.list_adapters(args.root)
    if not adapters:
        sys.exit(f"{args.root} altında adaptör yok — radyo host'ta mı?")
    if args.adapter:
        adapter = args.adapter.upper()
        if adapter not in adapters:
            sys.exit(f"host'ta {adapter} yok; bulunanlar: {', '.join(adapters)}")
    elif len(adapters) > 1:
        sys.exit(f"birden çok adaptör var, --adapter verin: {', '.join(adapters)}")
    else:
        adapter = adapters[0]

    bonds = bluezbond.list_bonds(args.root, adapter)
    only = {m.upper() for m in args.only}

    # Hedefin MEVCUT durumu da hedefin kanalından okunur: offline yolda ajan
    # yok, ve `--force` kararı ("misafirde zaten var mı") bu okumaya bağlı.
    # Yanlış kanaldan okumak `ATLANDI`/`ÜZERİNE YAZILIYOR` hükmünü sessizce
    # tersine çevirirdi.
    if args.offline:
        guest, _guest_names, _guest_devices, guest_svc, hive_meta = \
            hivebond.read_bonds(args.offline)
        print(f"offline kovan {hive_meta['hive']}  ({hive_meta['control_set']})")
    else:
        guest, _guest_names, _guest_devices, guest_svc = guest_state(args.domain)
    print(f"adaptör {adapter}  (host bond: {len(bonds)})")
    if adapter not in guest:
        print("  misafirde bu adaptörün anahtarı YOK — ilk yazımda oluşturulacak.")
        other = [a for a in guest if a != adapter]
        if other:
            print(f"  DİKKAT: misafirde başka adaptör var ({', '.join(other)}) — başka radyo mu?")
    else:
        entry = guest[adapter]
        print(f"  misafir: CentralIRK {'var' if entry['central_irk'] else 'yok'}"
              f"  |  BR/EDR {len(entry['bredr'])}  |  LE {len(entry['le'])}")

    if args.remove:
        chunks, report = plan_removals(adapter, guest, only)
    else:
        chunks, report = plan(args.root, adapter, bonds, guest, only,
                              args.key_order, args.authreq, args.force,
                              container=not args.no_container_id,
                              guest_svc=guest_svc,
                              le_flags_override=args.le_flags)

    for line in report:
        print(line)

    if not chunks:
        print("\nyapılacak bir şey yok.")
        return

    if args.dry_run:
        print(f"\n[dry-run] {len(chunks)} bond işlemi planlandı, misafire hiçbir şey yazılmadı.")
        return

    if args.offline:
        # Tek commit: `chunks` düzleştirilip bir kerede uygulanıyor. Ajan
        # yolundaki partileme Windows'un komut satırı sınırı için vardı;
        # offline'da o sınır yok, ve tek commit yarım kalmış bir kaydı da
        # imkânsız kılıyor.
        flat = [op for chunk in chunks for op in chunk]
        marks, meta = hivebond.apply_ops(args.offline, flat, dry_run=False)
        for mark in marks:
            print(f"  {mark}")
        print(f"\n{len(marks)} bond işlemi offline kovana yazıldı "
              f"({meta['ops']} işlem, tek commit).")
        print(f"  kapı: {meta['gate']}")
        if not args.remove:
            print("Windows bunları BTHPORT sürücüsü başlarken okur — misafir "
                  "açıldığında (ya da radyo o tarafa verildiğinde) okunacak.")
        return

    done = []
    for batch in batches([winbond.render_powershell(chunk) for chunk in chunks]):
        script = winbond.WRITE_PRELUDE + "\n" + "\n\n".join(batch) + "\n"
        exitcode, stdout, stderr = run_powershell(args.domain, script)
        if exitcode != 0:
            sys.exit(f"misafir yazma komutu exitcode={exitcode}\n{stderr}")
        done += [line for line in stdout.splitlines() if line.startswith("OK ")]

    for line in done:
        print(f"  {line}")
    print(f"\n{len(done)} bond işlemi misafirde tamamlandı.")
    if not args.remove:
        print("Windows bunları BTHPORT sürücüsü başlarken okur — radyoyu ŞİMDİ verin:")
        print(f"  vfioctl guest --name {args.domain} usb --attach 8087:0032")


if __name__ == "__main__":
    # Taşıyıcı hatasını mesaja çeviren yer → `win-to-bluez.py`deki aynı yorum.
    # İki kanal, iki hata tipi; ikisi de tek satıra iner.
    try:
        main()
    except (agentexec.AgentError, hivebond.HiveError) as exc:
        sys.exit(str(exc))
