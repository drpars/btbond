# btbond

🇹🇷 Tek bir Bluetooth radyosunu paylaşan iki işletim sistemi arasında
**eşleşme bond'larını** replike eder — böylece radyo hangi taraftaysa aynı
cihazlar yeniden eşleştirmeye gerek kalmadan bağlanır.
🇬🇧 Replicates Bluetooth pairing bonds between two operating systems that share
one radio, so your devices keep working on whichever side currently owns it.

> **Durum: iki yön de koştu** (2026-09-03, bluez 5.87, Windows 11 misafir).
>
> **Windows → Linux uçtan uca ölçüldü:** bir BR/EDR kulaklık ve bir LE oyun kolu
> Windows'ta eşleştirildi, bond'lar host'a kopyalandı, radyo host'a alındı ve
> **iki cihaz da yeniden eşleştirilmeden bağlandı**. Radyo misafire geri
> verildiğinde de öyle — anahtarlar değişmedi.
>
> **Linux → Windows:** LE tamamen çalışıyor — kol misafirde yeniden
> eşleştirmesiz bağlandı ve HID cihazı olarak göründü. BR/EDR'de bond, kimlik
> doğrulaması ve SDP çalışıyor; ama Windows profil devnode'larını (A2DP, AVRCP,
> HFP) ancak cihazdan **öğrenilen** dört alanı bildiğinde kuruyor ve o alanlar
> BlueZ dosyalarında yok → "Bilinen boşluk". TUI henüz yok.

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

**İki kanal var, ve genel olan offline kovan.** Bond, misafirin diskindeki bir
dosyada duruyor (`Windows/System32/config/SYSTEM`), yani onu okumak için
Windows'un koşması **gerekmiyor**. Dual boot bunun kanıtı: orada Windows,
Linux'la aynı anda hiç koşamaz — replikasyon koşan Windows gerektirseydi dual
boot prensipte imkânsız olurdu.

**`qemu-guest-agent`** (`agentexec.py`) koşan misafir için. Ajan misafirde
`NT AUTHORITY\SYSTEM` olarak koşar, ve `HKLM\SYSTEM\CurrentControlSet\Services\
BTHPORT\Parameters` tam olarak SYSTEM'e açık bir anahtardır. Sonuç: misafiri
**kapatmadan**, diski **rebind etmeden**, şifrelemeye **hiç dokunmadan**
okunup yazılabiliyor. Bugün yazma yolu **yalnız** bunda var.

**Offline kovan** (`hivebond.py`) kapalı misafir ve dual boot için —
şu an **salt-okuma**. Zincir: domain kapalı → disk host'ta → bölüm mount →
`hivex` → `ControlSet00N\Services\BTHPORT\Parameters`.

Ajanın tek gerçek üstünlüğü **diske erişmek zorunda olmaması** — hız değil,
çünkü koşan Windows'a yazılan anahtar da ancak `BTHPORT` sürücüsü başlarken
okunuyor. Ajanı *zorunlu* kılan üç durum var: (a) misafiri kapatmak
istememek, (b) birim şifreli ve anahtar elde değil, (c) disk host'a hiç
dönmüyor (`managed='no'` + boot'ta vfio'ya bağlanmış disk). **Passthrough tek
başına bunlardan biri değil:** `managed='yes'` bir disk kapanışta host'a geri
döner, ve bir qcow2 imajı ayrılmış diskten daha da kolay erişilir.

Offline kanalın bir üstünlüğü de var: **yazma sırası kapısı bedava**. Kapalı bir
Windows radyoyu tutamaz, yani "hedef tarafta radyo yokken yaz" kuralı tanımı
gereği sağlanır.

## Ölçülmüş düzen

Aşağıdakiler belgeden değil, iki gerçek cihaz eşleştirilerek **birinci elden**
ölçüldü (2026-09-03; Windows 11 misafir, bluez 5.87).

**Windows tarafı — iki teknoloji iki ayrı biçimde duruyor.** Klasik bond
adaptör anahtarının altında **bir değer**, LE bond ise **bir alt anahtar**:

```
HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\
├── Keys\<adaptör-mac>
│      CentralIRK   : REG_BINARY  16          <- adaptörün kendi IRK'si
│      <cihaz-mac>  : REG_BINARY  16          <- BR/EDR link key (DEĞER)
├── Keys\<adaptör-mac>\<cihaz-mac>            <- LE bond (ALT ANAHTAR)
│      LTK, IRK     : REG_BINARY  16
│      KeyLength, EDIV, AddressType, AuthReq, CEntralIRKStatus : REG_DWORD
│      ERand, Address : REG_QWORD             <- Address = cihazın BD_ADDR'i
└── Devices\<cihaz-mac>                       <- CİHAZ/PROFİL KAYDI
       Name, LEName : REG_BINARY              <- UTF-8, NUL ile biten cihaz adı
       COD | LEAppearance, LEAddressType, VID, PID, VIDType, Version : REG_DWORD
       LeContainerId : REG_BINARY 16          <- LeContainerIDSource=1 ile
       CachedServices\<0001000N> : REG_BINARY <- ham SDP kayıtları (BR/EDR)
       ServicesFor<adaptör-mac>               <- eşleşmenin "kullanılabilir" yarısı
          BR/EDR: SSP Paired/MITM/Supported, AuthenticationRequirements,
                  RemoteAuthenticationRequirements, IoCapability,
                  BasebandSupport, BRFlags, BRExtendedDeviceInfoFlags
          LE:     AuthenticationRequirementsLE, RemoteAuthenticationRequirementsLE,
                  IoCapabilityLE, BasebandSupport, LEFlags, LEExtendedDeviceInfoFlags
          BR/EDR profil alt anahtarları: {uuid}\C00000000
                  Enabled=1, Instance=1, CounterInstanceId=0,
                  PriLangServiceName = 256 bayt SIFIR (ölçüldü)
```

**Bond iki parçalıdır, ve ikisi de gerekir.** `Keys` kriptoyu taşır; `Devices`
cihaz kaydını. Yalnız `Keys` yazıldığında Windows cihazı **`paired` gösterir**
ve yazılan anahtarla **gerçek bir kimlik doğrulamalı bağlantı kurar** — ama
cihaz kullanılamaz: LE'de `Enum\BTHLE` düğümü hiç doğmaz, BR/EDR'de yalnız
jenerik devnode doğar. Enum düğümlerini elle yazmak **gerekmez**; Windows onları
bu iki parçadan kendisi kurar.

**BlueZ karşılığı doğrudan var:** `COD` ← `Class`, `LEAppearance` ←
`Appearance`, `VID/PID/VIDType/Version` ← `[DeviceID]`, `LEAddressType` ←
`AddressType`, profil `{uuid}` alt anahtarları ← `Services=`, ve
`CachedServices` ← `cache/<mac>` dosyasındaki `[ServiceRecords]` — **baytlar
birebir aynı**, yeniden üretmek gerekmiyor.

**BlueZ karşılığı** (`/var/lib/bluetooth/<adaptör>/<cihaz>/info`):

| Windows | BlueZ bölümü / alanı |
|---|---|
| `Keys\<adaptör>\<cihaz-mac>` (değer) | `[LinkKey] Key`, `SupportedTechnologies=BR/EDR;` |
| `LTK` | `[LongTermKey] Key` |
| `IRK` | `[IdentityResolvingKey] Key` |
| `KeyLength` / `EDIV` / `ERand` | `EncSize` / `EDiv` / `Rand` |
| `AddressType` 0 / 1 | `AddressType=public` / `static` |
| `Devices\<cihaz-mac>\Name` | `[General] Name` |

**Bayt sırası aynı** — `REG_BINARY` baytları BlueZ'in hex dizesine olduğu gibi
yazılır, ters çevrilmez (`--key-order asis`, ölçüldü: iki cihaz da bağlandı).
Ters kol bayrakla duruyor çünkü başka bir Windows sürümünde sınanmadı.

**Yazılması gereken alan az.** `[LinkKey] Type=4` + `PINLength=0` ve
`[LongTermKey] Authenticated=0` ile bağlantı kuruldu; `Class`, `Services`,
`Appearance`, `ConnectionParameters`, `DeviceID` **yazılmadı** — BlueZ ilk
bağlantıda kendisi ekledi ve bizim yazdığımız anahtar alanlarına dokunmadı.

**`bluetooth.service`'i durdurmak gerekmedi.** Dosyalar radyo misafirdeyken
(host'ta adaptör yokken) yazıldı; radyo host'a alınınca bluetoothd adaptörü
kurarken bond'ları **kendiliğinden** okudu — `restart` gerekmedi. Adaptör host'ta
**zaten varken** yazılırsa bu geçerli değil: bluetoothd `info`'yu bellekten geri
yazabilir, o durum ölçülmedi.

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
- **Misafir (ajan kanalı):** `qemu-guest-agent` kurulu ve yanıt veriyor
- **Offline kanal:** `hivex` (Python bağlamasıyla), NTFS sürücüsü (`ntfs3`
  çekirdekte yeter, `ntfs-3g` gerekmez); imaj dosyası için ayrıca `qemu-nbd`
- Bond'ları okumak/yazmak root gerektirir (`/var/lib/bluetooth` 0700; kovan
  için mount)

## Kullanım

**Önce durum: `btbond-sync.py status`.** İki tarafı okur, cihaz cihaz
karşılaştırır ve her satırın hükmünü verir — `eşleşiyor`, `yalnız host'ta`,
`yalnız misafirde`, `ANAHTAR FARKLI`. Radyonun nerede olduğunu iki bağımsız
kanaldan söyler (host'ta `hciN` düğümü; domain'in canlı XML'inde hostdev).

```
sudo tools/btbond-sync.py status               # tablo
     tools/btbond-sync.py status --json        # {"sides": […], "cross": […]}
sudo tools/btbond-sync.py sync --dry-run       # topla + dağıt, ne yapılacak
sudo tools/btbond-sync.py sync --handover      # yaz, sonra radyoyu devret (tek domain)
sudo tools/btbond-sync.py sync --handover --capture-hci   # + HCI'dan uzak bilgi topla
```

**Akış iki fazlı, ve sıra zorunlu: `collect` sonra `distribute`.**

```
sudo tools/btbond-sync.py collect                    # taraflardan host'a
sudo tools/btbond-sync.py distribute --domain a --domain b   # host'tan taraflara
sudo tools/btbond-sync.py sync                       # ikisi, sırayla
```

Sebebi fizik: çevre birim **merkez adresi başına tek bond** tutar ve bütün
taraflar aynı `BD_ADDR`ı gösterir, yani bir tarafta yapılan eşleştirme diğer
**bütün** tarafları bayatlatır. Host merkez olmak zorunda — aracı o koşturuyor,
`/var/lib/bluetooth`u o tutuyor, her misafire libvirt üzerinden yalnız o
ulaşıyor. Akış bu yüzden yıldız: her taraftan host'a topla, sonra host'tan
kapsamın tamamına dağıt.

**Fazlar arasında yeniden ölçülür.** Durum yazımlardan önce okunuyor, yani
`collect` host'u değiştirdiği anda `distribute`ın girdisi bayatlar. Tek döngüde
taraf taraf iki yönü birden yürüten biçim bu yüzden **yakınsamıyordu**: A'dan
host'a çekilen cihaz, B'nin bayat "yalnız host'ta" kümesine hiç girmiyor ve B
tek koşu sonunda eksik kalıyordu.

**Taraflar arası anahtarı ayrışan cihaz hiçbir fazda otomatik yazılmaz** —
`ANAHTAR FARKLI` yasağının taraflar arası hâli. `ATLANDI` satırı sebebi söyler;
komutu `status` basar, kararı kullanıcı verir.

**Kapsam kullanıcının seçimi: `--domain` tekrarlanabilir.**

```
sudo tools/btbond-sync.py status --domain win11-nvme --domain win11
```

Verilmezse varsayılan domain işlenir ve tanımlı **başka** domain'ler
*dokunulmadı* diye adlandırılır (`KAPSAM:` satırı). Bunun sebebi ölçüm: tek
radyo, tek `BD_ADDR`, ve çevre birim **merkez adresi başına tek bond** tutuyor
— yani bir misafirde yapılan eşleştirme diğer **bütün** tarafları bayatlatır,
ve "sessizce birini işlemek" temiz görünen bir eksik işlemdir. Atlamanın yönü
güvenli (eksik işlem bond bozmaz, fazlası bozar), o yüzden araç durmaz, adını
koyar.

Ulaşılamayan taraf — kapalı misafir, ajanı yanıt vermeyen misafir — **atlanır**
ve döngüyü öldürmez; `ATLANDI` satırı sebebi söyler. Devir (`handover`) ise tek
hedef ister: radyo tek, ve nereye gideceği tahmin edilecek bir şey değil.

İkiden çok taraf okunduğunda **TARAFLAR ARASI AYRIŞMA** bölümü basılır.
Eşleştirmeli tablolar bunu göremez: host↔A ve host↔B ayrı ayrı okunduğunda
A ile B'nin birbirine göre durumu hiçbir tabloda yoktur. Sinyal sezginin
tersi — cihaz **tek** anahtar tutar (en son eşleştirmeninkini), yani tek başına
duran taraf çalışan tek taraf olabilir ve çoğunluk bayattır. Çıktı bu yüzden
azınlığı işaretler ama **hüküm vermez**: hakem, cihazı o an bağlayabilen
taraftır.

`--capture-hci` devir **btmon yakalamasının içinde** koşar ve cihazlardan
`LMPFeatures` / `LmpVersion` / `LmpSubversion` / `ManufacturerId` toplar —
"Bilinen boşluk"un ihtiyaç duyduğu dört alan. Yalnız radyo **host'a gelirken**
anlamlı: ters yönde cihazlar misafirin içinde bağlanır ve host denetleyicisi
hiçbir olay görmez. Sonuç `$XDG_STATE_HOME/btbond/remote-info.json`a birikir.
Ham log `.gitignore`da: yakalama bir eşleştirmeye denk gelirse anahtar
dağıtımı da o log'a girer.

**Yön satırın özelliğidir, oturumun değil.** Araç "nereden nereye" diye
sormaz: tek tarafta duran bond o yöne kopyalanır, iki tarafta tutan hiçbir şey
istemez. Geriye tek gerçek belirsizlik kalır — aynı cihaz, iki tarafta,
**farklı anahtar** — ve `sync` onu **hiçbir zaman kendiliğinden çözmez**:
hangi tarafın yeni olduğunu bilemez ve yanlış seçim çalışan bir bond'u yok
eder. O satır için iki komut basılır, kararı kullanıcı verir.

**`sync` yazma sırasını kural olarak uygular:** hedef taraf radyoyu tutuyorsa
durur. Yanlış sırada yazmak hata vermez, sessizce etkisiz kalır — bu yüzden
sıra tavsiye değil kapı. Hedefin radyoyu tutup tutmadığı **ölçülemezse** de
durur; varsayımla geçilmez.

Aşağıdaki iki bölüm tek tek yönleri anlatır; `sync` bu betikleri
`--only <mac>` ile çağırır, mantığın ikinci kopyası yoktur.

**Windows → Linux replikasyonu.** Sıra önemli: önce yaz, sonra radyoyu al.

```
tools/win-to-bluez.py --dry-run                 # ne yazılacak (anahtar basılmaz)
sudo tools/win-to-bluez.py                      # /var/lib/bluetooth'a yaz
vfioctl guest --name <domain> usb --detach 8087:0032   # radyoyu host'a al
bluetoothctl devices Bonded                     # bond'lar yüklendi mi
bluetoothctl connect <cihaz-mac>                # asıl sınama
```

Var olan bir `info` dosyası **üzerine yazılmaz**; `--force` verilirse önce
`info.bak-<zaman>` olarak yedeklenir. `--only <mac>` tek cihazı seçer.

**Linux → Windows replikasyonu.** Aynı kural, ayna simetrisi: **hedef tarafta
radyo yokken yazılır.** BlueZ bond'ları adaptör kurulurken okur, Windows ise
BTHPORT sürücüsü başlarken — yani radyo devri her iki tarafta da "taze oku"
anıdır.

```
sudo tools/bluez-to-win.py --dry-run            # ne yazılacak
sudo tools/bluez-to-win.py                      # misafirin kayıt defterine yaz
vfioctl guest --name <domain> usb --attach 8087:0032   # radyoyu misafire ver
sudo tools/bluez-to-win.py --remove --only <mac>       # bond'u misafirden sil
```

Misafirde zaten olan bond **üzerine yazılmaz**; `--force` gerekir. Betik
misafire `-EncodedCommand` ile gider ve Windows'un komut satırı sınırı aşılırsa
`guest-exec` *"Failed to execute helper program (Invalid argument)"* ile düşer;
bu yüzden yazım partilere bölünür.

**Doğrulama** — iki tarafın aynı anahtar materyalini taşıdığını radyoyu
oynatmadan söyler. Yönsüzdür: iki tarafı karşılaştırır, hangi yönde replike
edildiğinden bağımsızdır. Karşılaştırma sha256'nın ilk 12 hex'i üzerinden yapılır,
yani çıktı anahtar sızdırmaz ve bayt sırasını da adlandırır:

```
sudo tools/win-to-bluez.py --verify
  BR/EDR xx:…  "Soundcore Life Q10"
    LinkKey  fp=3de37ff1c11a  EŞLEŞİYOR (aynı sıra)
```

**Yapı dökümü** — misafirdeki bond'ların yalnız *şeklini* basan salt-okuma araç
(`ad : tip len=N`, baytlar basılmaz):

```
tools/guest-keys-dump.py [domain]      # varsayılan: win11-nvme
```

**Kapalı misafir / dual boot — offline kovan** (`hivebond.py`).
Misafir **kapalı** olmalı; disk host'ta blok aygıtı olarak görünmeli.

```
# doğrudan bir bölüm (dual boot, ya da kapalı passthrough disk)
sudo mount -t ntfs3 -o ro /dev/<bölüm> /mnt/win
sudo tools/hivebond.py /mnt/win                 # mount kökünü verin, kovanı bulur
sudo tools/hivebond.py /mnt/win --dump          # ham satırlar (ajanla aynı biçim)

# imaj dosyası (kapalı bir domain'in qcow2'si)
sudo modprobe nbd max_part=8
sudo qemu-nbd --read-only --connect=/dev/nbd0 <imaj>.qcow2
sudo partprobe /dev/nbd0                        # ZORUNLU: aksi hâlde lsblk 0B gösterir
sudo mount -t ntfs3 -o ro /dev/nbd0p<N> /mnt/win
...
sudo umount /mnt/win && sudo qemu-nbd --disconnect /dev/nbd0
```

Windows kurulumunu bulmanın ölçütü **"NTFS mi" DEĞİL** — sonda okunacak
dosyanın kendisi (`Windows/System32/config/SYSTEM`). Bir kurtarma bölümü de
NTFS'tir ve araç onu reddeder. `CurrentControlSet` offline kovanda **yoktur**;
gerçek set `Select\Current`ten çözülür.

**Aynı kanaldan yazma** (`bluez-to-win.py --offline`) — mount `rw` olmalı:

```
sudo mount -t ntfs3 /dev/<bölüm> /mnt/win
sudo tools/bluez-to-win.py --offline /mnt/win --dry-run
sudo tools/bluez-to-win.py --offline /mnt/win
```

Hedefin **mevcut durumu da bu kanaldan** okunur; yanlış kanaldan okumak
`ATLANDI`/`ÜZERİNE YAZILIYOR` hükmünü sessizce tersine çevirirdi. Yazım **tek
commit**: parti sınırı Windows'un komut satırı sınırı içindi ve burada yok,
üstelik tek commit yarım kalmış bir kaydı imkânsız kılıyor.

**Hızlı başlatma kapısı.** Hazırda bekletmeyle kapanmış Windows, kovana
yazılanı dönüşte **sessizce kaybeder**. Araç bu yüzden yazmadan önce iki
bağımsız sinyale bakar — kovandan `HiberbootEnabled`, mount kökünden
`hiberfil.sys` — ve **ölçemediğinde de durur**:

```
DURDU: hızlı başlatma AÇIK (HiberbootEnabled=1) — kovana yazılan şey dönüşte kaybolur
```

Kapı ölçülmüş bir olguya dayanıyor ve makineye göre değişiyor: bu makinedeki
iki Windows kurulumundan biri `1`, öbürü `0`.

Kapı sık ateşler: bu makinedeki üç Windows kurulumundan **ikisi** hızlı
başlatmayı açık taşıyordu.

**Doğrulandı, uçtan uca:** üç bond offline kovana yazıldı (143 işlem, tek
commit), misafir açıldı, ve `btbond-sync.py status` üçünü de `eşleşiyor`
verdi — yani **Windows'un kendi kayıt defteri motoru** hivex'in yazdığı
baytları aynen sunuyor. Kalan boşluk: yazımdan sonra radyo o misafire hiç
verilmediği için **cihazların fiilen bağlandığı** görülmedi, ve **dual boot**
kolu (domain yerine disk yolu) hiç koşmadı.

## Testler

Misafir, root ve kovan gerektirmeyen iki sözleşme testi:

```
tests/test_emitters.py       # altın çıktı: yazma emitörleri aynı metni üretiyor mu
tests/test_transport.py      # taşıyıcı ve model sözleşmeleri
tests/test_emitters.py --update   # altın dosyayı KASITLI olarak yenile
```

**Neden altın çıktı.** Yazma emitörleri artık PowerShell metni değil, yazma
**işlemleri** (IR) üretiyor; renderer'lar onu metne çeviriyor. Ayrımın amacı
offline kovan yazma yolunu **ikinci bir düzen sahibi yaratmadan** eklemek — ama
yazma yolunu değiştiren her adım sessizce bozabilir: eksik bir işlem, hatalı bir
tip ya da kayan bir alan sırası çıkış kodunu değiştirmez, misafirde yanlış bir
kayıt bırakır ve cihaz `paired` görünür. Test girdileri sabitliyor ve çıktıyı
**birebir** karşılaştırıyor. `--update` yalnız çıktı kasıtlı değiştiğinde
kullanılır; diff commit'te okunur.

Testlerde **makineye özel kimlik yok** — MAC'ler uydurma, anahtarlar dolgu.

## Güvenlik

Bu deponun konusu tanımı gereği sırdır: `LinkKey`, `LTK`, `IRK`, `CSRK`. Bir
bond'u ele geçiren, o cihazın trafiğini çözebilir ve cihaz taklidi yapabilir.

- Araçlar anahtar baytını **stdout'a basmaz**; basan bir yol eklenirse açıkça
  bir bayrağın arkasına konur. Karşılaştırma gereken yerde bayt değil
  **parmak izi** (sha256'nın ilk 12 hex'i) basılır.
- Yazılan `info` dosyası 0600, dizini 0700 — `/var/lib/bluetooth`'un kendi
  düzeniyle aynı.
- `.gitignore` bond dökümlerini, kayıt defteri dışa aktarımlarını ve kopyalanmış
  BlueZ `info` dosyalarını kapsıyor; `pre-commit` kancası `gitleaks` koşturur.
  Yeni klonda bir kez: `git config core.hooksPath .githooks`

## Lisans

MIT → [LICENSE](LICENSE).

## Durum ve yol haritası

- [x] Kanal seçimi ölçülerek yapıldı (ajan ↔ offline kovan)
- [x] Misafir tarafını salt-okuma dökebilen ölçüm aracı
- [x] Windows `REG_BINARY` bond düzenini birinci elden ölç (BR/EDR + LE)
- [x] BlueZ `info` biçimini birinci elden ölç (BR/EDR ve LE ayrı)
- [x] Windows → Linux replikasyonu — iki cihazda uçtan uca doğrulandı
- [x] İki tarafın aynı anahtarı taşıdığını doğrulayan `--verify`
- [x] Windows bond'unun iki parçalı olduğunu ölç (`Keys` + `Devices`)
- [x] Linux → Windows replikasyonu — LE uçtan uca, BR/EDR kısmi
- [ ] BR/EDR'in öğrenilen alanları → "Bilinen boşluk"
- [ ] Adaptör IRK'si: Windows `CentralIRK` ↔ BlueZ yerel kimlik (RPA kullanan
      cihazlar için gerekebilir; üç test cihazının ikisi public, biri static
      random — hiçbiri dönen adres kullanmıyor, yani kol hâlâ ölçülmedi)
- [x] Tek komutluk akış (`btbond-sync.py status` / `sync`) — yön satırın
      özelliği, yazma sırası kapı olarak uygulanıyor
- [ ] TUI
- [x] Offline kovan **okuma** arka ucu (`hivebond.py`) — kapalı misafir ve
      dual boot; iki taraflı doğrulandı (altı parmak izi ajanla birebir aynı),
      bölüm ve qcow2 kolları ayrı ayrı koştu
- [x] Offline kovan **yazma** (`bluez-to-win.py --offline`) + hızlı başlatma
      kapısı — uçtan uca doğrulandı: offline yazılan üç bond, misafir
      açıldıktan sonra Windows'un kendi kayıt defterinden `eşleşiyor` döndü
- [ ] Offline yazımdan sonra radyoyu o misafire verip **cihazların bağlandığını**
      görmek (bugün doğrulanan şey kayıt, cihazın çalışması değil)
- [ ] Gerçek **dual boot** kolu: taraf kimliği domain adı değil disk yolu
- [x] Çoklu taraf: `--domain` tekrarlanabilir, kapsam kullanıcının seçimi,
      ulaşılamayan taraf atlanıyor, taraflar arası ayrışma raporlanıyor
- [x] Yazma emitörleri düzenden ayrıldı (ara temsil + renderer'lar), altın
      çıktı denkliğiyle: refactor öncesi/sonrası metin **birebir aynı**
- [x] `collect` + `distribute` iki fazlı akış (host kanonik kopya), fazlar
      arası yeniden ölçümle — tek döngülü biçim N≥2 tarafta yakınsamıyordu
- [ ] Offline taraf `status`ta görünsün — domain'in diskini kendi bulup
      mount etmesi gerekiyor (bugün mount elle yapılıyor)

## Bilinen boşluk

Windows, BR/EDR profil devnode'larını (A2DP, AVRCP, HFP) ancak cihazın **neyi
desteklediğini** bildiğinde kuruyor. O bilgi `Devices\<mac>` altındaki dört
alanda: `LMPFeatures`, `ManufacturerId`, `LmpVersion`, `LmpSubversion`. Windows
bunları cihaza bağlanınca kendisi öğrenir — ama bond kaydı yeni yazıldığında
henüz yoktur, ve onlar olmadan ses uç noktası çıkmaz.

Bunlar **hiçbir BlueZ dosyasında yok** (`info` da, `cache/<mac>` da taşımıyor);
HCI'dan okunur. `LMPFeatures` bu yoldan **çözüldü**: `btmon` (bluez-utils
içinde, ek kurulum yok) cihazın ACL'i yeniden kurulurken
`Read Remote Supported Features` olayını basıyor ve baytların little-endian
okunuşu Windows'un aynı cihaz için yazdığı QWORD'e birebir eşit çıktı.
Kalan üç alan `Read Remote Version Information`dan gelir ve o olay ölçülen
kopar-kur turunda **ateşlemedi** — çekirdek onu koşulsuz istemiyor.
Ölçüldüğünde beş profil düğümü de doğdu ve ses geldi, yani
tetikleyici oldukları doğrulandı — ama hangisinin tek başına yettiği
ayrılmadı.

Bu boşluk **yalnız BR/EDR profillerini** etkiler: bond, kimlik doğrulaması ve
LE tarafının tamamı bu alanlar olmadan da çalışıyor.
