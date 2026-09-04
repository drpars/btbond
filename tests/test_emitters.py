#!/usr/bin/env python3
"""ALTIN ÇIKTI: `winbond`in yazma emitörleri hâlâ aynı metni üretiyor mu?

NEDEN BU BİÇİM — emitörler artık PowerShell metni değil, yazma **işlemleri**
(IR) üretiyor ve renderer'lar onu metne çeviriyor (→ `winbond`, "Ara temsil").
O ayrımın amacı offline kovan yazma yolunu **ikinci bir düzen sahibi
yaratmadan** eklemek. Ama yazma yolunu değiştiren her adım sessizce bozabilir:
eksik bir işlem, hatalı bir tip ya da kayan bir alan sırası çıkış kodunu
değiştirmez — misafirde yanlış bir kayıt bırakır ve `paired` görünür.

Bu test tam onu kilitler: girdiler sabit, çıktı dosyada, ve karşılaştırma
**birebir**. Misafir gerekmez, root gerekmez, `--dry-run` bile gerekmez.

    tests/test_emitters.py              # karşılaştır (fark varsa rc=1)
    tests/test_emitters.py --update     # altın dosyayı bilerek yenile

`--update` yalnız çıktının **kasıtlı** olarak değiştiği durumda kullanılır ve
diff commit'te okunur; kaza sonucu değişen çıktı orada görünür.

MAKİNEYE ÖZEL KİMLİK YOK: MAC'ler uydurma, anahtarlar `aa/bb/cc/dd` dolgusu.
Depo public, ve gerçek `BD_ADDR`/anahtar buraya yazılmaz.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import winbond  # noqa: E402

GOLDEN = HERE / "golden-emitters.txt"

ADAPTER = "00:11:22:33:44:55"
DEV = "AA:BB:CC:DD:EE:FF"
UUID_A = "0000110b-0000-1000-8000-00805f9b34fb"
UUID_B = "0000110e-0000-1000-8000-00805f9b34fb"


def cases():
    """Her dalı geçen sabit girdiler — (etiket, metin) çiftleri."""
    yield "01 bredr", winbond.bredr_script(ADAPTER, DEV, "aa" * 16)

    yield "02 record BR/EDR tam", winbond.device_record_script(
        ADAPTER, DEV, "Test Kulaklık", False, {"COD": 2360324},
        [UUID_A, UUID_B], {"0": "bb" * 8, "1": "cc" * 8})

    # `DynamicCachedServices`: BlueZ'in kaydı (DES 8-bit, `35 LL …`) Windows'un
    # sarmalına (`36 00LL …`) çevrilerek yazılır — beş gerçek kayıtta bayt bayt
    # doğrulandı (→ `winbond.DYNAMIC_NOTU`). Vaka 02'nin `bb…`/`cc…` dolguları
    # 0x35 ile başlamadığı için oraya GİRMEZ (uydurma sarmal yazılmaz); burada
    # SDP biçimli bir dolgu var: 0x35, uzunluk 6, altı bayt gövde.
    yield "02b record BR/EDR + SDP biçimli kayıt", winbond.device_record_script(
        ADAPTER, DEV, "", False, {}, [UUID_A], {"00010000": "3506" + "0900" + "0a00" + "0100"})

    yield "03 record BR/EDR minimal", winbond.device_record_script(
        ADAPTER, DEV, "", False, {}, [], None)

    yield "04 record LE tam", winbond.device_record_script(
        ADAPTER, DEV, "Test Fare", True,
        {"LeContainerId": "dd" * 16, "LEAppearance": 962, "LEAddressType": 1,
         "VID": 1234, "PID": 5678, "VIDType": 1, "Version": 311,
         "LERemoteConnParamsIntervalMin": 6, "LERemoteConnParamsIntervalMax": 12,
         "LERemoteConnParamsLatency": 0, "LERemoteConnParamsLSTO": 500},
        [], None)

    yield "05 record LE minimal", winbond.device_record_script(
        ADAPTER, DEV, "", True, {}, [], None)

    # `LEFlags` iki dallı: verilmezse **hiç yazılmaz** (vaka 04/05), verilirse
    # yazılır. Sabit yazmak ölçülmüş biçimde yanlış olurdu, çünkü değer cihaza
    # göre değişiyor ve n=2'de türetilemedi → `winbond.LEFLAGS_NOTU`.
    yield "05b record LE + LEFlags verildi", winbond.device_record_script(
        ADAPTER, DEV, "", True, {}, [], None, le_flags=0x000B0000)

    yield "06 le tam", winbond.le_script(ADAPTER, DEV, {
        "LTK": "aa" * 16, "IRK": "bb" * 16, "KeyLength": 16, "EDIV": 12345,
        # Üst biti dolu QWORD: PowerShell onu işaretli basar ve `as_uint`
        # geri çevirir. Dolgu değer, gerçek eşleştirme materyali değil.
        "ERand": 0xFFFFFFFFFFFFFFFF, "Address": 0x8000000000000000,
        "AddressType": 1, "AuthReq": 45, "CEntralIRKStatus": 1,
        "CSRK": "cc" * 16, "OutboundSignCounter": 7,
        "CSRKInbound": "dd" * 16, "InboundSignCounter": 9})

    yield "07 le minimal", winbond.le_script(ADAPTER, DEV, {
        "LTK": "aa" * 16, "KeyLength": 16, "EDIV": 0, "ERand": 0,
        "Address": 0, "AddressType": 0, "AuthReq": 45, "CEntralIRKStatus": 1})

    yield "08 le imza sayaçsız", winbond.le_script(ADAPTER, DEV, {
        "LTK": "aa" * 16, "KeyLength": 16, "EDIV": 0, "ERand": 0,
        "Address": 0, "AddressType": 0, "AuthReq": 45, "CEntralIRKStatus": 1,
        "CSRK": "cc" * 16, "CSRKInbound": "dd" * 16})

    # ÖĞRENİLEN ALANLAR (→ `winbond.REMOTE_NOTU`). Üç dal: BR/EDR'de dördü de
    # yazılır; LE'de `LMPFeatures` ÖLÇÜLMÜŞ biçimde atlanır (Windows LE
    # cihazlarda o alanı tutmuyor); bilinmeyen alan hiç yazılmaz.
    REMOTE = {"LMPFeatures": 0x877BBFD8FE0DFEAF, "LmpVersion": 8,
              "LmpSubversion": 12850, "ManufacturerId": 148,
              "HostSupportedFeaturesMap": 7}
    yield "05c record BR/EDR + öğrenilen", winbond.device_record_script(
        ADAPTER, DEV, "", False, {}, [], None, remote=REMOTE)
    yield "05d record LE + öğrenilen (LMPFeatures ATLANIR)", \
        winbond.device_record_script(ADAPTER, DEV, "", True, {}, [], None,
                                     remote=REMOTE)
    yield "05e record BR/EDR + kısmi öğrenilen", winbond.device_record_script(
        ADAPTER, DEV, "", False, {}, [], None,
        remote={"LmpVersion": 10, "LMPFeatures": None})

    yield "09 remove LE", winbond.remove_script(ADAPTER, DEV, True)
    yield "10 remove BR/EDR", winbond.remove_script(ADAPTER, DEV, False)
    yield "11 WRITE_PRELUDE", winbond.WRITE_PRELUDE


def build():
    parts = []
    for label, text in cases():
        parts.append(f"===== {label} =====\n{text}\n")
    return "".join(parts)


def check_renderer():
    """Tanınmayan işlem SESSİZCE ATLANMAMALI — o, eksik yazımı gizler."""
    try:
        winbond.render_powershell([("boyle_bir_islem_yok", "x")])
    except ValueError:
        return True
    return False


def main():
    text = build()

    if "--update" in sys.argv:
        GOLDEN.write_text(text, encoding="utf-8")
        print(f"altın dosya yenilendi: {GOLDEN} "
              f"({len(text.splitlines())} satır / {len(text.encode())} bayt)")
        return 0

    if not check_renderer():
        print("HATA: render_powershell tanınmayan işlemi sessizce geçti")
        return 1
    print("[OK ] tanınmayan işlem gürültüyle düşüyor")

    if not GOLDEN.exists():
        print(f"HATA: altın dosya yok ({GOLDEN}) — `--update` ile üretin")
        return 1

    want = GOLDEN.read_text(encoding="utf-8")
    if text == want:
        print(f"[OK ] altın çıktı BİREBİR aynı "
              f"({len(text.splitlines())} satır / {len(text.encode())} bayt, "
              f"{sum(1 for _ in cases())} vaka)")
        return 0

    import difflib
    print("HATA: emitör çıktısı değişti — kasıtlıysa `--update`, değilse hata:")
    for line in difflib.unified_diff(want.splitlines(), text.splitlines(),
                                     "golden", "şimdi", lineterm="", n=2):
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
