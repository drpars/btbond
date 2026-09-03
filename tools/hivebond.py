#!/usr/bin/env python3
"""Offline `SYSTEM` kovanından BTHPORT bond'larını oku — **misafir kapalıyken**.

Bu modül **offline taşıyıcının TEK sahibi**dir; kayıt defteri düzeninin sahibi
`winbond`, model `bondsync`. Ayrım bilerek: çıktı `winbond.DUMP_POWERSHELL`in
satır biçimini (`V<TAB>yol<TAB>ad<TAB>tip<TAB>değer`) taklit ediyor, yani
`winbond.parse_dump` + `collect` **hiç değişmeden** çalışıyor. Düzen iki kez
yazılsaydı biri ilerler, öbürü donardı.

NEDEN VAR — *"misafir koşmalı"* kısıtı problemin değil **ajan kanalının**
kısıtı (ölçüldü 2026-09-04): bond, misafirin diskindeki bir dosyada
(`Windows/System32/config/SYSTEM`) duruyor ve o dosya misafir kapalıyken
host'tan okunabiliyor. Dual boot bunun kanıtı — orada Windows, Linux'la aynı
anda hiç koşamaz. Yani offline kovan **genel** kanal, ajan koşan misafir için
bir optimizasyon.

ÖLÇÜLDÜ (2026-09-04, `win11-nvme` kapalı, `ntfs3 -o ro`, hivex 1.3.24-8):
altı parmak izinin altısı ajan kanalıyla **birebir aynı** (LinkKey, LTK, IRK,
CSRK, CSRKInbound; üç cihaz), adaptör ve cihaz kümesi aynı, adlar
`Devices\\<mac>\\Name` blob'undan doğru çözüldü.

ÖLÇÜLMEDİ — ve bu modül o yüzden **yalnız okuyor**: `hivex` commit'i,
`ntfs3`ün rw güvenilirliği, ve Windows'un yeniden açılışta değiştirilmiş
kovanı kabul etmesi. Yazma yolu eklenirse önce şu ikisi ölçülür:
`HiberbootEnabled` ve `hiberfil.sys` — hızlı başlatmayla kapanmış bir Windows
kovan değişikliğini dönüşte **sessizce** kaybeder.

Kullanım:
    tools/hivebond.py /mnt/win                 # mount kökü → kovanı kendi bulur
    tools/hivebond.py /mnt/win/Windows/System32/config/SYSTEM
    tools/hivebond.py /mnt/win --dump          # ham `V…` satırları
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import winbond  # noqa: E402

try:
    from hivex import Hivex
except ImportError:                                          # pragma: no cover
    Hivex = None

# Kovanın içindeki hive-göreli yol. `Windows/System32/config` altındaki ad
# NTFS'te büyük/küçük harfe duyarsız, ama `ntfs3` gerçek yazımı gösterir.
HIVE_RELATIVE = ("Windows/System32/config/SYSTEM",
                 "windows/system32/config/SYSTEM",
                 "Windows/System32/Config/SYSTEM")

# `hivex` ham REG_* tip numarası döndürür, PowerShell `GetValueKind()` adı.
# Eşleme **taşıyıcı olgusudur**, düzen değil — o yüzden burada duruyor.
REG_KIND = {
    0: "None", 1: "String", 2: "ExpandString", 3: "Binary",
    4: "DWord", 5: "DWordBigEndian", 6: "Link", 7: "MultiString",
    11: "QWord",
}


class HiveError(RuntimeError):
    """Offline kovan okunamadı. `agentexec.AgentError`in offline karşılığı."""


def find_system_hive(target):
    """Verilen yol kovanın kendisi mi, bir mount kökü mü? İÇERİKLE karar ver.

    Windows kurulumunu bulmanın ölçütü **"NTFS mi" DEĞİL** (ölçüldü: bu
    makinede NTFS ve `Oyunlar` etiketli bir bölüm var, içi SteamLibrary —
    Windows kurulumu değil). Ölçüt okunacak dosyanın kendisi.
    """
    path = Path(target)
    if path.is_file():
        return path
    if not path.is_dir():
        raise HiveError(f"yol ne dosya ne dizin: {target}")
    for relative in HIVE_RELATIVE:
        candidate = path / relative
        if candidate.is_file():
            return candidate
    raise HiveError(
        f"{target} altında `Windows/System32/config/SYSTEM` yok — burada bir "
        f"Windows kurulumu bulunmuyor (NTFS olması yetmez)")


def open_hive(hive_path):
    if Hivex is None:
        raise HiveError("`hivex` Python bağlaması yok: `pacman -S hivex` "
                        "(paket `python`i opsiyonel bağımlılık olarak sayıyor)")
    try:
        return Hivex(str(hive_path), write=False)             # SALT-OKUMA
    except Exception as exc:                                  # hivex kendi tipini vermiyor
        raise HiveError(f"kovan açılamadı ({hive_path}): {exc}") from exc


def resolve_control_set(hive):
    """`ControlSet00N` adını kovanın KENDİSİNDEN çöz.

    `CurrentControlSet` boot'ta kurulan uçucu bir bağdır ve offline kovanda
    **hiç yoktur** — `winbond.PARAMS`in canlı yolu burada çözünmez. Doğru
    yol `Select\\Current`in gösterdiği set, ve o değer canlı ölçüme değil
    kovana sorulur (kovanı okurken canlı sistem ayakta olmayabilir bile).
    """
    select = hive.node_get_child(hive.root(), "Select")
    if select is None:
        raise HiveError("kovanda `Select` yok — bu bir SYSTEM kovanı değil")
    for value in hive.node_values(select):
        if hive.value_key(value).lower() == "current":
            _vtype, data = hive.value_value(value)
            return f"ControlSet{int.from_bytes(data[:4], 'little'):03d}"
    raise HiveError("`Select\\Current` okunamadı")


def _render(vtype, data):
    """Değeri PowerShell'in bastığı gösterime çevir.

    İşaretli sayı BİLEREK: PowerShell `GetValue` bir QWORD'ü `[Int64]`,
    DWORD'ü `[Int32]` döndürüyor ve üst biti dolu değer negatif basılıyor —
    `winbond.as_uint` tam o gösterimi bekliyor. Burada işaretsiz basmak
    ayrıştırıcıyı sessizce ikiye bölerdi.
    """
    kind = REG_KIND.get(vtype, str(vtype))
    if vtype == 3:
        return kind, data.hex()
    if vtype == 4:
        return kind, str(int.from_bytes(data[:4], "little", signed=True))
    if vtype == 11:
        return kind, str(int.from_bytes(data[:8], "little", signed=True))
    if vtype in (1, 2, 7):
        return kind, data.decode("utf-16-le", "replace").rstrip("\x00")
    return kind, data.hex()


def _emit(hive, node, path, lines):
    for value in hive.node_values(node):
        kind, text = _render(*hive.value_value(value))
        lines.append(f"V\t{path}\t{hive.value_key(value)}\t{kind}\t{text}")
    for kid in hive.node_children(node):
        _emit(hive, kid, f"{path}\\{hive.node_name(kid)}", lines)


def dump(target):
    """`winbond.parse_dump`ın beklediği metni ver — ajan çıktısıyla aynı sözleşme.

    Döner: `(metin, kovan-yolu, control-set-adı)`.
    """
    hive_path = find_system_hive(target)
    hive = open_hive(hive_path)
    control_set = resolve_control_set(hive)

    node = hive.root()
    for name in (control_set, "Services", "BTHPORT", "Parameters"):
        node = hive.node_get_child(node, name)
        if node is None:
            raise HiveError(
                f"kovanda yok: {control_set}\\Services\\BTHPORT\\Parameters "
                f"(`{name}` bulunamadı) — bu Windows'ta Bluetooth yığını hiç "
                f"kurulmamış olabilir")

    base = f"\\SYSTEM\\{control_set}\\Services\\BTHPORT\\Parameters"
    lines = []
    for sub in ("Keys", "Devices"):
        child = hive.node_get_child(node, sub)
        if child is None:
            # Ajan tarafının `MISSING` satırıyla aynı biçim: `parse_dump` onu
            # yok sayar, ama okuyan insan yokluğu görür.
            lines.append(f"MISSING\t{base}\\{sub}")
            continue
        _emit(hive, child, f"{base}\\{sub}", lines)
    return "\n".join(lines), hive_path, control_set


def read_bonds(target):
    """Offline kovandan `winbond.collect` üçlüsünü ver — model katmanı için."""
    text, hive_path, control_set = dump(target)
    adapters, names, devices = winbond.collect(winbond.parse_dump(text))
    return adapters, names, devices, {"hive": str(hive_path),
                                      "control_set": control_set}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", metavar="YOL",
                        help="mount kökü ya da doğrudan `SYSTEM` kovanı")
    parser.add_argument("--dump", action="store_true",
                        help="ham `V…` satırlarını bas (ajan çıktısıyla aynı biçim)")
    args = parser.parse_args()

    # `bondsync` MODEL katmanı; taşıyıcı ona bağımlı olmasın diye import
    # burada, modül başında değil.
    import bondsync

    if args.dump:
        text, hive_path, control_set = dump(args.target)
        print(f"# {hive_path}  ({control_set})", file=sys.stderr)
        print(text)
        return 0

    adapters, names, _devices, meta = read_bonds(args.target)
    print(f"kovan   : {meta['hive']}")
    print(f"set     : {meta['control_set']}")
    if not adapters:
        print("bond yok: `Keys` altında adaptör anahtarı bulunamadı.")
        return 0

    for adapter in sorted(adapters):
        entry = adapters[adapter]
        irk = "var" if entry["central_irk"] else "yok"
        rows = bondsync.guest_state(entry, names)
        print(f"\nadaptör {adapter}  (CentralIRK: {irk})  {len(rows)} bond")
        for dev in sorted(rows):
            row = rows[dev]
            prints = "  ".join(f"{k}={v}" for k, v in sorted(row["fp"].items()))
            print(f"  {dev}  {row['tech']:<9} {row['name']}")
            print(f"  {'':18}  {prints}")
    return 0


if __name__ == "__main__":
    # Hatayı MESAJA çeviren yer burası; kütüphane `sys.exit` çağırmaz
    # → `agentexec.AgentError` yanındaki aynı gerekçe.
    try:
        sys.exit(main())
    except HiveError as exc:
        sys.exit(str(exc))
