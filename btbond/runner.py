"""Aracın KENDİSİNİ yeniden çağıran komutun tek sahibi.

TUI ve `sync` yazıcıları alt süreç olarak çağırıyor, ve bu tercih değil
tasarım: onay ekranı çalıştırılacak komutu **tam metin** olarak gösteriyor
(→ `tui.Confirm`). Gösterilen metin ile koşan komut aynı olmak zorunda, yoksa
kullanıcı onayladığı şeyden başkasını onaylamış olur.

O yüzden burada bir dize **kurulmuyor**, gerçekten koşacak argüman listesi
çözülüyor: kurulu giriş noktası varsa o, yoksa `python -m btbond`. İkisi de
aynı kodu çalıştırıyor; fark yalnız ekranda görünen metinde ve ikisi de
dürüst.

Neden modül olarak ayrı: `cli` alt komutları **geç** import ediyor (TUI
`textual` istiyor, o da CLI'ın bağımlılığı değil), ve `sync`/`tui` bunu
`cli`den alsaydı döngü olurdu. Tek işi olan küçük bir modül o döngüyü
tanımı gereği kapatıyor.
"""

import shutil
import sys

ENTRY_POINT = "btbond"


def self_command():
    """Bu aracı yeniden çağıran argüman listesinin ÖNEKİ.

    Kurulu `btbond` PATH'te ise o kullanılır — onay ekranında okunan şey de
    kullanıcının kendi yazacağı komut olur. Depodan koşulurken (kurulum yok)
    `python -m btbond`e düşülür; `sys.executable` sanal ortamı da izler.
    """
    found = shutil.which(ENTRY_POINT)
    if found:
        return [found]
    return [sys.executable, "-m", ENTRY_POINT]
