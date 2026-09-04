"""Misafirde PowerShell koşturmak için `qemu-guest-agent` kanalı.

Kanal `virsh qemu-agent-command` → `guest-exec`. Ajan misafirde
`NT AUTHORITY\\SYSTEM` olarak koştuğu için `BTHPORT\\Parameters` misafir
KOŞARKEN okunup yazılabilir: disk rebind, BitLocker açma ve offline kovan
düzenleme gerekmez. Gerekçe → README, "Neden bu şekilde: kanal seçimi".

Bu modül anahtar materyalini yorumlamaz; yalnız komutu taşır.
"""

import base64
import json
import subprocess
import time

LIBVIRT_URI = "qemu:///system"
AGENT_TIMEOUT = 90


class AgentError(RuntimeError):
    """Ajan kanalı bu domain'e konuşamadı.

    KÜTÜPHANE SÜRECİ ÖLDÜRMEZ (2026-09-04): buradaki iki hata yolu eskiden
    `sys.exit` çağırıyordu, ve o biçim taraf üzerinde dönen HERHANGİ bir
    döngüyü ilk kapalı misafirde öldürüyordu — ölçüldü, `status --domain
    win11` → `virsh rc=1: domain is not running` ve iş orada bitiyordu.
    Bu makinede üç Windows domain'i tanımlı, yani kapalı bir taraf istisna
    değil kural.

    `RuntimeError`dan türüyor çünkü `bondsync.survey` zaten onu atıyor ve
    `btbond` onu yakalayıp tek satır mesaja çeviriyor — yani
    çalıştırılabilirlerin davranışı korunuyor, yalnız kütüphane artık
    çağıranın kararına karışmıyor.
    """

# Bu makinenin misafiri — artık yalnız SON ÇARE: libvirt hiç okunamıyorsa.
# Varsayılan kapsam keşiftir (`discover_domains`), çünkü tek fiziksel
# radyo → tek BD_ADDR → her tarafta aynı anahtar materyali; yani "hangi
# taraflar" sorusunun doğal cevabı "hepsi", ve kullanıcı daraltmak istediğinde
# `--domain` verir (2026-09-04, kullanıcı isteği: argümansız koşuda bütün
# domain'ler, tek hedefte `--domain`). Tek olgu tek sahip: burada.
DEFAULT_DOMAIN = "win11-nvme"


def discover_domains(uri=LIBVIRT_URI):
    """libvirt'te TANIMLI bütün domain adları (koşan + kapalı); okunamazsa `None`.

    Windows olup olmadığı burada sorulmuyor: her taraf kendi kanalından
    okunurken cevabını kendisi verir (Windows kurulumu olmayan disk
    `sidemount`ta reddedilir ve satır `ULAŞILAMADI` der — kapsamı yazılı bir
    olumsuz). Filtrelemek, hiç okunmayan bir tarafı sessizce gizlerdi.
    """
    try:
        proc = subprocess.run(["virsh", "-c", uri, "list", "--all", "--name"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return sorted(name for name in proc.stdout.split() if name)


def resolve_scope(explicit):
    """İşlenecek domain'ler + (varsa) kapsam notu. İki ön yüz için ORTAK.

    - `--domain` verildiyse: o liste (tekilleştirilmiş, sıra korunur) ve
      daraltıldığını söyleyen bir not — daraltma kullanıcının seçimi ve
      dokunulmayan taraf adıyla yazılır.
    - verilmediyse: keşfedilen HERKES, not yok. Eskiden burada tek bir
      varsayılan işlenip *"dokunulmayan N domain var"* diye uyarılıyordu;
      bu, kullanıcıyı her koşuda üç `--domain` yazmaya mahkûm eden bir
      sürtünmeydi ve kaldırıldı.
    - keşif olanaksızsa (libvirt yok): `DEFAULT_DOMAIN` + bunu söyleyen not.
    """
    discovered = discover_domains()
    if explicit:
        chosen = list(dict.fromkeys(explicit))
        left = [d for d in (discovered or []) if d not in chosen]
        note = (f"kapsam daraltıldı: {', '.join(chosen)} "
                f"(dokunulmayan: {', '.join(left)})") if left else None
        return chosen, note
    if discovered:
        return discovered, None
    return [DEFAULT_DOMAIN], (f"libvirt okunamadı; varsayılana düşüldü: "
                              f"{DEFAULT_DOMAIN}")


def single_domain(explicit, purpose="bu komut"):
    """Tek hedefli araçlar için domain: verildiyse o, yoksa TEK tanımlı olan.

    Birden çok domain tanımlıyken tahmin edilmez — hangisi olduğu belirsizdir
    ve yazıcılar yıkıcıdır. Döner: `(domain, hata|None)`.
    """
    if explicit:
        return explicit, None
    discovered = discover_domains()
    if discovered is None:
        return DEFAULT_DOMAIN, None
    if len(discovered) == 1:
        return discovered[0], None
    if not discovered:
        return None, f"{purpose} bir domain ister ve libvirt'te hiç tanımlı yok"
    return None, (f"{purpose} tek domain ister ama {len(discovered)} tanımlı: "
                  f"{', '.join(discovered)} — `--domain` ile seçin")


def agent_command(domain, payload, uri=LIBVIRT_URI):
    """Ajana bir QMP komutu gönder, `return` gövdesini döndür."""
    proc = subprocess.run(
        ["virsh", "-c", uri, "qemu-agent-command", domain, json.dumps(payload)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AgentError(
            f"{domain}: virsh rc={proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["return"]


def run_powershell(domain, script, timeout=AGENT_TIMEOUT, uri=LIBVIRT_URI):
    """PowerShell'i misafirde koştur; (exitcode, stdout, stderr) döndür.

    Betik `-EncodedCommand` ile gider: UTF-16LE + base64, yani tırnak,
    ters bölü ve satır sonu kabuk ayrıştırmasına hiç uğramaz.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    pid = agent_command(domain, {
        "execute": "guest-exec",
        "arguments": {
            "path": "powershell.exe",
            "arg": ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            "capture-output": True,
        },
    }, uri)["pid"]

    for _ in range(timeout):
        status = agent_command(domain, {
            "execute": "guest-exec-status", "arguments": {"pid": pid},
        }, uri)
        if status.get("exited"):
            break
        time.sleep(1)
    else:
        raise AgentError(f"{domain}: ajan komutu {timeout} s'de bitmedi (pid={pid})")

    def decode(field):
        data = status.get(field)
        return base64.b64decode(data).decode("utf-8", "replace") if data else ""

    # PowerShell stderr'e CLIXML ilerleme gürültüsü yazar; hükmü çıkış kodu
    # taşır, stderr'in doluluğu değil.
    return status.get("exitcode"), decode("out-data"), decode("err-data")
