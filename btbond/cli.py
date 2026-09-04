"""`btbond` giriş noktası — tek ön kapı, alt komutlara dağıtır.

TASARIM: bu dosya BAYRAK TANIMLAMAZ. Her alt komutun bayraklarının tek sahibi
kendi modülü; buradaki iş yalnızca doğru `main()`e devretmek. İkinci bir
ayrıştırıcı yazılsaydı biri ilerler, öbürü donardı — bu depoda ödenmiş hata
tam olarak odur.

Devir `sys.argv` yeniden yazılarak yapılıyor, çünkü modüllerin `main()`i
argümanı `parse_args()` ile alıyor. `argv[0]`a kullanıcının fiilen yazdığı
komut konuyor, yani `--help` çıktısındaki `usage:` satırı da doğru okunuyor.

GEÇ IMPORT ZORUNLU: `tui` modülü `textual` istiyor ve o yalnız TUI'nin
bağımlılığı. Modül başında import edilseydi `textual` kurulmamış bir makinede
`btbond status` da düşerdi.
"""

import sys

# (alt komut, hangi modül, tek satırlık açıklama). Sıra yardımda görünen sıra.
#
# İlk altısı `sync` modülünün KENDİ konumsal alt komutları, o yüzden onlara
# devrederken `argv` olduğu gibi bırakılıyor — `btbond status` gerçekten
# `btbond` + konumsal `status` demek ve `usage:` satırı da öyle basılıyor.
SYNC_SUBCOMMANDS = {
    "status": "iki tarafı oku, cihaz cihaz karşılaştır ve her satırın hükmünü ver",
    "collect": "TOPLA: bütün taraflardan host'a çek (iki fazlı akışın 1. fazı)",
    "distribute": "DAĞIT: host'tan kapsamdaki taraflara yaz (2. faz)",
    "sync": "collect + yeniden ölç + distribute (iki fazlı akışın tamamı)",
    "handover": "radyoyu devret (vfioctl) — `--to host|guest`",
    "remote-info": "bağlı cihazlardan HCI ile öğrenilen alanları topla",
}

OTHER_SUBCOMMANDS = {
    "tui": ("tui", "diff görünümü (Textual) — aynı kapı, aynı model"),
    "to-host": ("tohost", "misafir Windows → host BlueZ replikasyonu"),
    "to-guest": ("toguest", "host BlueZ → misafir Windows replikasyonu"),
    "hive": ("hivebond", "kapalı misafirin `SYSTEM` kovanından bond'ları oku"),
    "guest-dump": ("guestdump", "misafirin bond YAPISINI bas (salt-okuma)"),
    "cleanup": ("sidemount", "çökme sonrası bayat mount/nbd kayıtlarını çöz"),
}

# Sistem paketi adı, çünkü bu araç Arch'ta yaşıyor ve bağımlılıkların çoğu
# zaten sistem paketi (→ `packaging/PKGBUILD`). pip yolu da yazılı, ama ikinci.
OPTIONAL_HINT = {
    "textual": "  Kurulum: `pacman -S python-textual`"
               "  (ya da `pip install 'btbond[tui]'`)",
    "hivex": "  Kurulum: `pacman -S hivex`",
}

USAGE = """\
kullanım: btbond <alt-komut> [seçenekler]

Bluetooth bond'larını host Linux ile misafir Windows arasında replike eder.
Tek fiziksel radyo, tek `BD_ADDR`: bir tarafta eşleştirmek diğer tarafın
anahtarını cihazda üzerine yazar. Çözüm iki tarafa aynı anahtarı koymak.

Durum ve akış:
{sync}

Tek yönlü ve yardımcı:
{other}

Her alt komutun kendi yardımı var:  btbond <alt-komut> --help
Bond okumak/yazmak root ister (`/var/lib/bluetooth` 0700, kovan için mount).
"""


def _render(mapping):
    return "\n".join(f"  {name:<12} {desc}" for name, desc in mapping.items())


def help_text():
    return USAGE.format(
        sync=_render(SYNC_SUBCOMMANDS),
        other=_render({n: d for n, (_m, d) in OTHER_SUBCOMMANDS.items()}))


def _dispatch(argv):
    """Alt komutu bul ve devret. Hata mesajına çevirme ÇAĞIRANDA."""
    name = argv[0]

    if name in SYNC_SUBCOMMANDS:
        from . import sync
        # `argv` OLDUĞU GİBİ geçiyor: `status` `sync.main()`in konumsalı.
        sys.argv = ["btbond"] + argv
        return sync.main()

    module_name, _desc = OTHER_SUBCOMMANDS[name]
    try:
        module = __import__(f"btbond.{module_name}", fromlist=[module_name])
    except ModuleNotFoundError as exc:
        # EKSİK BAĞIMLILIK SUSMAZ, ve traceback de basmaz. Ölçüldü
        # (2026-09-04, textual'sız temiz bir kurulumda): `btbond tui` ham
        # `ModuleNotFoundError` yığınıyla düşüyordu, oysa eksiklik bir kusur
        # değil bir kurulum adımı. Kendi paketimizin import hatası ise gerçek
        # bir arızadır ve olduğu gibi yükselir.
        if not exc.name or exc.name.split(".")[0] == "btbond":
            raise
        hint = OPTIONAL_HINT.get(exc.name, "")
        print(f"btbond {name}: `{exc.name}` kurulu değil — bu alt komut onu "
              f"istiyor.{hint}", file=sys.stderr)
        return 1
    # Alt komut TÜKETİLİYOR, ve `argv[0]` kullanıcının yazdığı komut olur.
    sys.argv = [f"btbond {name}"] + argv[1:]
    return module.main()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(help_text(), end="")
        return 0

    if argv[0] in ("-V", "--version"):
        from . import __version__
        print(f"btbond {__version__}")
        return 0

    name = argv[0]
    if name not in SYNC_SUBCOMMANDS and name not in OTHER_SUBCOMMANDS:
        print(f"btbond: bilinmeyen alt komut: {name}\n", file=sys.stderr)
        print(help_text(), end="", file=sys.stderr)
        return 2

    # TAŞIYICI HATASINI MESAJA ÇEVİREN TEK YER BURASI. Eskiden aynı `try`
    # beş ayrı betiğin `__main__` bloğunda duruyordu; beş kopya, ve biri
    # ilerlediğinde öbürleri donardı. Kütüphaneler `sys.exit` çağırmıyor
    # (çağıranın kararına karışmazlar), o yüzden çeviriyi ön kapı yapıyor —
    # yoksa kullanıcı tek satırlık bir mesaj yerine traceback görürdü.
    from . import agentexec
    from . import hivebond
    try:
        return _dispatch(argv) or 0
    except (agentexec.AgentError, hivebond.HiveError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
