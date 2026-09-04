#!/usr/bin/env python3
"""Kapalı bir tarafın diskini host'ta bağla — ve NE OLURSA OLSUN çöz.

Bu modül mount zincirinin TEK sahibi: imaj → `qemu-nbd` → `partprobe` → Windows
bölümünü **içerikle** bul → `mount`. Zincir ayrıcalıklı ve durumlu; araç
ortasında ölürse geriye bağlı bir nbd ve mount'lu bir dosya sistemi kalır — bu
depoda bedeli ödenmiş yetim-kaynak sınıfı. O yüzden üç güvence, üçü de
zorunlu:

  1. `Mounted` bir context manager: `__exit__` istisnada da koşar ve umount +
     disconnect'i **ayrı ayrı** dener (biri düşerse öbürü atlanmaz).
  2. Her mount `/run/btbond/mounted.json`a **PID ile** yazılır. `/run` tmpfs
     olduğu için yeniden başlatmada kendiliğinden temizlenir; çökme sonrası
     `cleanup_stale()` ölü PID'lerin kayıtlarını çözer — ölçüt süreçtir, kayıt
     değil (bu deponun kuralı).
  3. Domain KAPALI değilse **reddedilir**, çünkü koşan bir VM'in diskini host'ta
     bağlamak dosya sistemini bozar. Denetim bağlamadan hemen önce yapılır;
     eşzamanlı `virsh start` ile yarış küçük ama sıfır değil, ve belgeleniyor.

Ölçülmüş tuzak (2026-09-04): `qemu-nbd --connect`ten hemen sonra `lsblk` diski
`0B` gösteriyor ve bölüm düğümleri yok — `partprobe` **zorunlu**, ve düğümler
udev'den birkaç yüz ms sonra geliyor; burada beklenir.

Windows kurulumunu bulmanın ölçütü **"NTFS mi" DEĞİL** (ölçüldü: bir kurtarma
bölümü de NTFS'tir ve `hivebond` onu reddeder): `Windows/System32/config/SYSTEM`
dosyasının varlığı.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hivebond  # noqa: E402

MOUNT_ROOT = Path("/run/btbond")
MARKER = MOUNT_ROOT / "mounted.json"
LIBVIRT_URI = "qemu:///system"


class MountError(RuntimeError):
    """Mount zinciri tamamlanamadı; ne kadar ilerlediyse geri alındı."""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def domain_state(domain, uri=LIBVIRT_URI):
    proc = _run(["virsh", "-c", uri, "domstate", domain])
    return proc.stdout.strip() if proc.returncode == 0 else None


def free_nbd():
    """Boş bir `/dev/nbdN` — `/sys/block/nbdN/pid` yoksa bağlı değil."""
    for node in sorted(Path("/sys/block").glob("nbd*"),
                       key=lambda p: int(p.name[3:])):
        if not (node / "pid").exists():
            return f"/dev/{node.name}"
    raise MountError("boş nbd aygıtı yok (`modprobe nbd max_part=8`?)")


def partitions_of(block_dev):
    """`/dev/X`in bölümleri — sysfs'ten, ad tahmin etmeden."""
    base = Path(block_dev).name
    return [f"/dev/{p.name}" for p in sorted(Path("/sys/block", base).glob(f"{base}*"))
            if p.name != base]


def _wait_partitions(block_dev, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        parts = partitions_of(block_dev)
        if parts:
            return parts
        time.sleep(0.2)
    return partitions_of(block_dev)


# --- kayıt ------------------------------------------------------------------
#
# Kayıt PID taşıyor: "bu mount kimin?" sorusunun cevabı süreçtir. Ölü bir
# sürecin kaydı bayattır ve çözülür; canlı bir sürecinki başkasına aittir ve
# DOKUNULMAZ.
def _load_marker():
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_marker(data):
    MOUNT_ROOT.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _pid_alive(pid):
    return Path("/proc", str(pid)).exists()


def cleanup_stale(log=print):
    """Ölü süreçlerden kalan mount/nbd kayıtlarını çöz. Döner: çözülen sayısı."""
    data = _load_marker()
    freed = 0
    for key, entry in list(data.items()):
        if _pid_alive(entry.get("pid", -1)):
            continue
        log(f"[sidemount] bayat kayıt çözülüyor: {key} (pid {entry.get('pid')} ölü)")
        _teardown(entry.get("mount"), entry.get("nbd"), log)
        del data[key]
        freed += 1
    _save_marker(data)
    return freed


def _teardown(mount, nbd, log=print):
    """umount ve disconnect'i AYRI AYRI dene — biri düşerse öbürü atlanmasın."""
    if mount and Path(mount).is_mount():
        proc = _run(["umount", mount])
        if proc.returncode != 0:
            log(f"[sidemount] umount düştü ({mount}): {proc.stderr.strip()} — "
                f"tembel umount deneniyor")
            _run(["umount", "-l", mount])
    if mount:
        try:
            Path(mount).rmdir()
        except OSError:
            pass
    if nbd and Path("/sys/block", Path(nbd).name, "pid").exists():
        proc = _run(["qemu-nbd", "--disconnect", nbd])
        if proc.returncode != 0:
            log(f"[sidemount] nbd disconnect düştü ({nbd}): {proc.stderr.strip()}")


# --- asıl iş ----------------------------------------------------------------
class Mounted:
    """`with Mounted(domain, disk) as root:` — `root` Windows bölümünün kökü.

    `disk` `bondsync.side_disk` çıktısı: `{"kind": "image"|"block", "path": …}`.
    `rw=False` varsayılan ve **okuma için yeter**; yazma turu `rw=True` ister.
    """

    def __init__(self, domain, disk, rw=False, log=print):
        self.domain, self.disk, self.rw, self.log = domain, disk, rw, log
        self.nbd = None
        self.mount = None
        self.partition = None

    def __enter__(self):
        if os.geteuid() != 0:
            raise MountError("mount root ister")
        state = domain_state(self.domain)
        # KAPI: koşan VM'in diskini bağlamak dosya sistemini bozar. Eşzamanlı
        # `virsh start` ile yarış küçük ama sıfır değil; denetim bağlamadan
        # HEMEN önce.
        if state != "shut off":
            raise MountError(f"{self.domain} kapalı değil ({state or 'durum okunamadı'}) "
                             f"— koşan VM'in diski bağlanmaz")
        cleanup_stale(self.log)

        try:
            block = self._attach()
            self.partition = self._find_windows(block)
            self.mount = MOUNT_ROOT / self.domain
            self.mount.mkdir(parents=True, exist_ok=True)
            opts = "rw" if self.rw else "ro"
            proc = _run(["mount", "-t", "ntfs3", "-o", opts, self.partition, str(self.mount)])
            if proc.returncode != 0:
                raise MountError(f"mount düştü ({self.partition}, {opts}): "
                                 f"{proc.stderr.strip()}")
            self._record()
            self.log(f"[sidemount] {self.domain}: {self.partition} → {self.mount} ({opts})")
            return self.mount
        except BaseException:
            # Yarım kalan zincir GERİ ALINIR, sonra istisna yükselir.
            self._release()
            raise

    def __exit__(self, exc_type, exc, tb):
        self._release()
        return False

    def _attach(self):
        if self.disk["kind"] == "block":
            return self.disk["path"]
        self.nbd = free_nbd()
        cmd = ["qemu-nbd", "--connect=" + self.nbd]
        if not self.rw:
            cmd.append("--read-only")
        cmd.append(self.disk["path"])
        proc = _run(cmd)
        if proc.returncode != 0:
            self.nbd = None
            raise MountError(f"qemu-nbd düştü: {proc.stderr.strip()}")
        # ÖLÇÜLDÜ: partprobe olmadan bölüm düğümleri gelmiyor, disk 0B görünüyor.
        _run(["partprobe", self.nbd])
        return self.nbd

    def _find_windows(self, block):
        """Bölümleri sırayla salt-okuma dene; `SYSTEM` kovanı olanı seç."""
        probe = MOUNT_ROOT / f".probe-{self.domain}"
        probe.mkdir(parents=True, exist_ok=True)
        tried = []
        try:
            for part in _wait_partitions(block):
                if _run(["mount", "-t", "ntfs3", "-o", "ro", part, str(probe)]).returncode != 0:
                    tried.append(f"{part}: ntfs değil")
                    continue
                try:
                    found = (probe / "Windows/System32/config/SYSTEM").is_file()
                finally:
                    _run(["umount", str(probe)])
                if found:
                    return part
                tried.append(f"{part}: NTFS ama Windows kurulumu değil")
        finally:
            try:
                probe.rmdir()
            except OSError:
                pass
        raise MountError(f"{self.domain}: Windows kurulumu bulunamadı — "
                         + ("; ".join(tried) or "bölüm yok"))

    def _record(self):
        data = _load_marker()
        data[self.domain] = {"pid": os.getpid(), "mount": str(self.mount),
                             "nbd": self.nbd, "partition": self.partition,
                             "rw": self.rw}
        _save_marker(data)

    def _release(self):
        _teardown(str(self.mount) if self.mount else None, self.nbd, self.log)
        data = _load_marker()
        if data.get(self.domain, {}).get("pid") == os.getpid():
            del data[self.domain]
            _save_marker(data)
        if self.mount or self.nbd:
            self.log(f"[sidemount] {self.domain}: çözüldü")
        self.mount = self.nbd = None


def main():
    """`sidemount.py cleanup` — çökme sonrası elle temizlik."""
    if len(sys.argv) == 2 and sys.argv[1] == "cleanup":
        n = cleanup_stale()
        print(f"{n} bayat kayıt çözüldü; kalan: {list(_load_marker()) or 'yok'}")
        return 0
    print(__doc__.splitlines()[0])
    print("kullanım: sidemount.py cleanup")
    return 2


if __name__ == "__main__":
    sys.exit(main())
