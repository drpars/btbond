# btbond

🇹🇷 Tek bir Bluetooth radyosunu paylaşan iki işletim sistemi arasında
**eşleşme bond'larını** replike eder — böylece radyo hangi taraftaysa aynı
cihazlar yeniden eşleştirmeye gerek kalmadan bağlanır.
🇬🇧 Replicates Bluetooth pairing bonds between two operating systems that share
one radio, so your devices keep working on whichever side currently owns it.

> **Durum: ölçüm aşaması. Uçtan uca çalışan bir şey henüz yok.** Bugün depoda
> yalnız salt-okuma bir ölçüm aracı var (`tools/guest-keys-dump.py`). Senkron
> yolu, misafirdeki `REG_BINARY` düzeni birinci elden ölçülene kadar
> yazılmıyor — bu bilerek: ölçülmemiş bir biçime göre yazılmış anahtar kopyası
> sessizce yanlış bond üretir ve arıza "cihaz bağlanmıyor" diye görünür.

---

## Problem

Radyo tek, adaptörün `BD_ADDR`'i tek. Bir Bluetooth çevre birimi **merkez
adresi başına tek bond** tutar. İki OS aynı radyoyu paylaştığı için ikisi de
aynı adresle görünür — dolayısıyla birinde eşleştirmek, cihazın öbür taraftaki
anahtarını **üzerine yazar**. Klasik belirti: Windows'ta eşleştir, Linux'a dön,
fare bağlanmıyor; Linux'ta yeniden eşleştir, Windows'ta bağlanmıyor.

Çözüm bond'ları birleştirmek değil, **iki tarafa aynı anahtar materyalini
koymak**. O zaman cihazın yeniden eşleşmesi hiç gerekmez.

Bu iki kurulumda da aynı problemdir:

- **Dual boot** — aynı makinede Linux ve Windows.
- **VM passthrough** — Linux host ve Windows misafir, radyo USB olarak devredilir.

## Neden bu şekilde: kanal seçimi

Yaygın reçete Windows bölümünü host'tan mount edip `SYSTEM` kovanını `chntpw`
ile açmaktır. Bu araç **bunu yapmıyor**, çünkü passthrough kurulumunda o yol
tanımı gereği tıkalı: misafirin diski `vfio-pci`'ye bağlıysa host'ta blok
aygıtı olarak **yoktur**, ve üstüne BitLocker gelirse ikinci bir katman daha
eklenir.

Bunun yerine **`qemu-guest-agent`** kullanılıyor. Ajan misafirde
`NT AUTHORITY\SYSTEM` olarak koşar, ve `HKLM\SYSTEM\CurrentControlSet\Services\
BTHPORT\Parameters\Keys` tam olarak SYSTEM'e açık bir anahtardır. Sonuç:
misafiri **kapatmadan**, diski **rebind etmeden**, şifrelemeye **hiç
dokunmadan** okunup yazılabiliyor.

Dual boot kurulumunda ajan yoktur; orada offline kovan yolu doğru yoldur ve
ayrı bir arka uç olarak eklenecek.

## Kapsam dışı

**Radyonun devri bu aracın işi değil.** VM tarafında onu
[`vfioctl`](https://github.com/drpars/vfioctl) yapıyor:

```
vfioctl guest --name <domain> usb --attach <vendor>:<product>
vfioctl guest --name <domain> usb --detach <vendor>:<product>
```

`btbond` bu komutları kullanır, yerine geçmez.

## Gereksinimler

- **Host:** Python 3.11+, `bluez`, `libvirt` (`virsh`)
- **Misafir:** `qemu-guest-agent` kurulu ve yanıt veriyor
- Bond'ları okumak/yazmak root gerektirir (`/var/lib/bluetooth` 0700)

## Kullanım

Bugün tek yüzey, misafirdeki bond'ların **yapısını** basan salt-okuma ölçüm:

```
tools/guest-keys-dump.py [domain]      # varsayılan: win11-nvme
```

Çıktı `KEY <yol>` satırları ve altlarında `ad : tip len=N`. **Anahtar baytları
basılmaz** — yalnız uzunluk — yani çıktı bir hata kaydına ya da nota güvenle
yapıştırılabilir.

## Güvenlik

Bu deponun konusu tanımı gereği sırdır: `LinkKey`, `LTK`, `IRK`, `CSRK`. Bir
bond'u ele geçiren, o cihazın trafiğini çözebilir ve cihaz taklidi yapabilir.

- Araçlar anahtar baytını **stdout'a basmaz**; basan bir yol eklenirse açıkça
  bir bayrağın arkasına konur.
- `.gitignore` bond dökümlerini, kayıt defteri dışa aktarımlarını ve kopyalanmış
  BlueZ `info` dosyalarını kapsıyor; `pre-commit` kancası `gitleaks` koşturur.
  Yeni klonda bir kez: `git config core.hooksPath .githooks`

## Durum ve yol haritası

- [x] Kanal seçimi ölçülerek yapıldı (ajan ↔ offline kovan)
- [x] Misafir tarafını salt-okuma dökebilen ölçüm aracı
- [ ] Windows `REG_BINARY` bond düzenini birinci elden ölç (gerçek bir eşleşme gerekiyor)
- [ ] BlueZ `info` biçimini birinci elden ölç (BR/EDR ve LE ayrı)
- [ ] Windows → Linux replikasyonu
- [ ] Linux → Windows replikasyonu
- [ ] TUI
- [ ] Dual boot (offline kovan) arka ucu
