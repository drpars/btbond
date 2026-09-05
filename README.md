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
> BlueZ dosyalarında yok → "Bilinen boşluk".
>
> **Offline kovan kanalı da koştu** (2026-09-04): kapalı bir misafirin kovanı
> okunuyor **ve yazılıyor** — üç bond offline yazıldı, misafir açıldı ve
> Windows'un kendi kayıt defteri üçünü de aynen verdi. TUI var
> (`btbond tui`) ve kapalı tarafları da gösteriyor.

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
okunup yazılabiliyor.

**Offline kovan** (`hivebond`) kapalı misafir ve dual boot için — **iki
yönde de okuma ve yazma** (`btbond to-guest --offline`, `btbond to-host
--offline`). Zincir: domain kapalı → disk host'ta → bölüm mount → `hivex` →
`ControlSet00N\Services\BTHPORT\Parameters`. Mount'u araç kendisi yapıyor
(`sidemount`): disk keşfi + `qemu-nbd` + içerikle bölüm seçimi + garantili
çözme → "TUI".

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

**`LEFlags` cihaza göre değişiyor ve TÜRETİLEMEDİ** — ölçüldü:

| cihaz | değer | bitler |
|---|---|---|
| Xbox Wireless Controller | `0x10030000` | 16, 17, **28** |
| ROG GLADIUS III WL | `0x000B0000` | 16, 17, **19** |

Aynı alt anahtardaki diğer beş alan iki cihazda **birebir aynı**. Bit 28 için
*"LE Secure Connections"* / *"IRK var"* / *"public adres"* adaylarının üçü de
aynı veriyi açıklıyor, bit 19 için *"legacy"* / *"CSRK var"* / *"random adres"*
de öyle — çünkü iki cihazın bütün LE özellikleri **birlikte** farklı ve n=2'de
hiçbir bit tek bir özelliğe bağlanamıyor. Ayırmak üçüncü bir LE cihaz ister.

Bu yüzden araç **sabit yazmıyor** (bir cihazın değerini bir başkasına yazmak
ölçülmüş biçimde yanlış olurdu). Sıra: `--le-flags` verildiyse o, **hedefte
varsa korunur**, ikisi de yoksa alan **hiç yazılmaz** — ve hangisi olduğu
rapor satırında yazılı. Alanın yokluğunun neyi bozduğu da ölçülmedi, o yüzden
sessiz kalınmıyor.

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

**Çift kipli cihaz tek dosyaya, iki bölüm birden.** Windows aynı MAC'in
klasik anahtarını `Keys\<adaptör>` **değeri**, LE bond'unu aynı yolun **alt
anahtarı** olarak tutuyor, yani bir cihaz ikisinde birden durabiliyor. BlueZ
tarafında karşılığı **tek** `info` dosyası: `[LinkKey]` ve `[LongTermKey]`
yan yana, `SupportedTechnologies=BR/EDR;LE;`. `bluezbond.bond_info` bu satırın
tek sahibi ve değeri eldekinden türetiyor. Ölçüldü (2026-09-04): daha önce iki
ayrı döngü aynı dosyaya iki kez yazıyor ve LE yazımı `[LinkKey]`i **siliyordu**
— hata yok, çıkış kodu 0.

Satırın biçimi tahmin değil: BlueZ bu alanı `g_key_file_set_string_list` ile
yazıyor ve listeyi `BR/EDR` → `LE` sırasında kuruyor (5.87 `device.c`,
`update_technologies`); GLib her öğeden sonra ayırıcıyı bastığı için iki öğe
`BR/EDR;LE;` veriyor. Tek öğeli karşılıkları (`BR/EDR;`, `LE;`) gerçek bond
dosyalarıyla birebir aynı. **Gerçek bir çift kipli cihazda uçtan uca
sınanmadı** — açık kalan biçim değil, BlueZ'in dosyayı okuyup cihazın iki
teknolojiyle de bağlanması.

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
- **Uzak cihaz bilgisi (`remote-info`):** `btmon` (bluez-utils) **ve**
  `hcitool` (Arch'ta `bluez-deprecated-tools`). İkincisi opsiyonel değil:
  çekirdek `Read Remote Version Information`ı **hiç yollamıyor** (ölçüldü —
  `bluetooth.ko`da opcode `0x041d` sıfır kez geçiyor ve olayın işleyicisi
  yok), yani komutu araç kendisi yollamak zorunda. Yoksa araç susmaz,
  eksikliği **söyler**.
- Bond'ları okumak/yazmak root gerektirir (`/var/lib/bluetooth` 0700; kovan
  için mount)

`textual` ve `hivex` yokluğu **sessiz değil**: ilkinde `btbond tui` tek satırla
hangi paketin gerektiğini söyler, ikincisinde offline kanal aynı şekilde düşer.

## Kurulum

```
pip install .                 # ya da: pip install '.[tui]'  (TUI için textual)
btbond --help
```

Arch'ta paket olarak: `packaging/PKGBUILD`. Sistem bağımlılıklarının **gerçek
listesi orada** — `pyproject.toml`ın `dependencies`i bilerek boş, çünkü
ihtiyaçların çoğu PyPI paketi değil (`bluez`, `hcitool`, `virsh`, `qemu-nbd`,
`hivex`) ve pip onları çözemez; oraya yazmak pip'e tutamayacağı bir söz
verdirirdi.

```
cd packaging && makepkg -si
```

`makepkg` koşturuldu (2026-09-04): özet doğrulandı, `check()` içinde dört test
paketi de geçti, `/usr/bin/btbond` taşıyan paket üretildi. Kaynak bir sürüm
**etiketine** bakıyor (dala değil), çünkü `pkgver`in bir şeye karşılık gelmesi
kaynağın değişmez olmasını gerektiriyor.

Kurmadan da koşar — depo kökünden `python -m btbond <alt-komut>`. Araç kendini
alt süreç olarak çağırdığında (TUI'nin yazıcıları, `sync`in fazları) hangisinin
kullanıldığını tek yer çözüyor: kurulu `btbond` PATH'te ise o, değilse
`python -m btbond`. Onay ekranında görünen komut **fiilen koşan** komuttur.

**Dosya düzeni:**

```
btbond/            # paket — tek giriş noktası `btbond.cli:main`
├── cli.py         #   ön kapı: bayrak TANIMLAMAZ, alt komuta devreder
├── runner.py      #   aracın kendini çağırma yolunun tek sahibi
├── sync.py        #   status / collect / distribute / sync / handover / remote-info
├── tui.py         #   `btbond tui`  (Textual)
├── tohost.py      #   `btbond to-host`   — Windows → BlueZ
├── toguest.py     #   `btbond to-guest`  — BlueZ → Windows
├── hivebond.py    #   `btbond hive`      — offline kovan kanalının tek sahibi
├── guestdump.py   #   `btbond guest-dump`
├── sidemount.py   #   mount zinciri + `btbond cleanup`
├── bondsync.py    #   iki taraflı durum, diff modeli ve YAZMA KAPISI
├── bluezbond.py   #   BlueZ `info` biçiminin tek sahibi
├── winbond.py     #   Windows kayıt defteri düzeninin tek sahibi
├── hcicapture.py  #   btmon/hcitool ile uzak cihaz bilgisi
└── agentexec.py   #   `qemu-guest-agent` kanalı
tests/             # root, misafir, terminal ve kovan GEREKTİRMEZ
packaging/PKGBUILD
```

## Kullanım

**Önce durum: `btbond status`.** İki tarafı okur, cihaz cihaz
karşılaştırır ve her satırın hükmünü verir — `eşleşiyor`, `yalnız host'ta`,
`yalnız misafirde`, `ANAHTAR FARKLI`. Radyonun nerede olduğunu iki bağımsız
kanaldan söyler (host'ta `hciN` düğümü; domain'in canlı XML'inde hostdev).

```
sudo btbond status               # tablo
     btbond status --json        # {"sides": […], "cross": […]}
sudo btbond sync --dry-run       # topla + dağıt, ne yapılacak
sudo btbond sync --handover      # yaz, sonra radyoyu devret (tek domain)
sudo btbond sync --handover --capture-hci   # + HCI'dan uzak bilgi topla
```

**Akış iki fazlı, ve sıra zorunlu: `collect` sonra `distribute`.**

```
sudo btbond collect                    # taraflardan host'a
sudo btbond distribute --domain a --domain b   # host'tan taraflara
sudo btbond sync                       # ikisi, sırayla
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

**Kapsam varsayılanda HERKES; `--domain` daraltır.**

```
sudo btbond status                    # libvirt'teki bütün domain'ler
sudo btbond status --domain win11     # yalnız biri
```

Argümansız koşu `virsh list --all`daki her domain'i taraf sayar — koşanı ajandan,
kapalıyı kovandan (kendiliğinden bağlayıp) okur. Sebebi fizik: tek radyo, tek
`BD_ADDR`, çevre birim **merkez adresi başına tek bond** tutuyor; yani "hangi
taraflar" sorusunun doğal cevabı *hepsi*. `--domain` verilince kapsam daraltılır
ve **dokunulmayan** taraflar `KAPSAM:` satırında adlandırılır — daraltma
kullanıcının seçimi, ama sessiz değil. Windows olmayan bir domain zarar görmez:
diski salt-okuma denenir, Windows kurulumu yoksa satır `ULAŞILAMADI (Windows
kurulumu bulunamadı)` der.

Tek hedefli araçlar (`handover`, `btbond to-host`, `btbond to-guest`,
`btbond guest-dump`) `--domain` verilmezse **tek** tanımlı domain'i alır;
birden çok tanımlıysa tahmin etmez, adlarını listeleyip `--domain` ister.

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

**Uzak cihaz bilgisi: `remote-info`.** Windows BR/EDR profil devnode'larını
ancak cihazın `LMPFeatures` / `LmpVersion` / `LmpSubversion` / `ManufacturerId`
alanlarını bildiğinde kuruyor, ve bu dördü **hiçbir BlueZ dosyasında yok** —
cihazdan HCI ile öğrenilir.

```
sudo btbond remote-info                      # devirsiz topla
sudo btbond sync --handover --capture-hci    # devrin içinde topla
```

Devir **gerekmiyor**: iki HCI olayı da var olan bir bağlantıda istenince
ateşliyor (ölçüldü — üç cihaz, BR/EDR ve LE). `remote-info` bond'lu cihazlara
bağlanır, komutları yollar, olayları yakalar ve
`$XDG_STATE_HOME/btbond/remote-info.json`a biriktirir; `btbond to-guest` onu
**kendiliğinden** okur (`--no-remote-info` kapatır). Devir yolu hâlâ en verimli
an — adaptör sıfırdan kurulurken cihazlar zaten taze bağlanır —, ama yalnız
radyo **host'a gelirken**: ters yönde cihazlar misafirin içinde bağlanır ve host
denetleyicisi hiçbir olay görmez.

`LMPFeatures` LE cihazlarda yazılmaz, çünkü Windows onu LE için tutmuyor
(ölçüldü). Ham log `.gitignore`da: yakalama bir eşleştirmeye denk gelirse
anahtar dağıtımı da o log'a girer.

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
btbond to-host --dry-run                 # ne yazılacak (anahtar basılmaz)
sudo btbond to-host                      # /var/lib/bluetooth'a yaz
vfioctl guest --name <domain> usb --detach 8087:0032   # radyoyu host'a al
bluetoothctl devices Bonded                     # bond'lar yüklendi mi
bluetoothctl connect <cihaz-mac>                # asıl sınama
```

Var olan bir `info` dosyası **üzerine yazılmaz**; `--force` verilirse önce
`info.bak-<zaman>` olarak yedeklenir. `--only <mac>` tek cihazı seçer.

**SDP önbelleği de taşınır** (varsayılan açık, `--no-service-cache` kapatır).
Misafirde eşleştirilmiş bir cihaz host'a geldiğinde host'ta SDP kaydı hiç
olmuyordu — ters yön onu arayıp bulamıyordu. Windows'un `CachedServices` /
`DynamicCachedServices` kayıtları artık `cache/<mac>` dosyasının
`[ServiceRecords]` bölümüne yazılıyor. Önbellek bond DEĞİL — eksikse BlueZ
SDP'yi yeniden sorar —, o yüzden kural üç dallı: olmayan handle **eklenir**,
aynısı duruyorsa **dokunulmaz** (dosya hiç açılmaz), farklı bir değer duruyorsa
yalnız `--force` ile değişir. Başka bölümler (`[General]`, `[Endpoints]`,
`[Attributes]`) ve listede olmayan handle'lar korunur; silme yok. Yazım atomik,
var olan dosya `<mac>.bak-<zaman>` olarak yedeklenir.

> ÖLÇÜLDÜ (2026-09-05): gerçek misafir kovanından okunan beş kayıt geçici bir
> köke yazıldı ve BlueZ'in **kendi** ürettiği `cache/` dosyasıyla **5/5 bayt
> bayt** aynı çıktı (58/58/61/99/77 bayt); ikinci tur sıfır yazım yaptı.

**Linux → Windows replikasyonu.** Aynı kural, ayna simetrisi: **hedef tarafta
radyo yokken yazılır.** BlueZ bond'ları adaptör kurulurken okur, Windows ise
BTHPORT sürücüsü başlarken — yani radyo devri her iki tarafta da "taze oku"
anıdır.

```
sudo btbond to-guest --dry-run            # ne yazılacak
sudo btbond to-guest                      # misafirin kayıt defterine yaz
vfioctl guest --name <domain> usb --attach 8087:0032   # radyoyu misafire ver
sudo btbond to-guest --remove --only <mac>       # bond'u misafirden sil
```

Misafirde zaten olan bond **üzerine yazılmaz**; `--force` gerekir. Betik
misafire `-EncodedCommand` ile gider ve Windows'un komut satırı sınırı aşılırsa
`guest-exec` *"Failed to execute helper program (Invalid argument)"* ile düşer;
bu yüzden yazım partilere bölünür.

```
sudo btbond to-host --offline /mnt/win      # kapalı misafirden topla
sudo btbond to-host --stop-bluetooth        # radyo host'tayken, devirsiz
```

`--stop-bluetooth` başlatmayı `finally`de yapar (yazım düşse bile Bluetooth
kapalı kalmaz) ve sonra `bluetoothctl devices Bonded` ile yazılanların gerçekten
okunduğunu gösterir — hüküm dosyanın varlığı değil, yığının onu görmesi.
Ölçülmüş ayrıntı: `start` döner dönmez sorulunca adaptör henüz kurulmamış
oluyor ve sayı **düşük** okunuyor (3 → 1, bir saniye sonra 3); araç eski sayıya
dönene kadar yoklar.

**Doğrulama** — iki tarafın aynı anahtar materyalini taşıdığını radyoyu
oynatmadan söyler. Yönsüzdür: iki tarafı karşılaştırır, hangi yönde replike
edildiğinden bağımsızdır. Karşılaştırma sha256'nın ilk 12 hex'i üzerinden yapılır,
yani çıktı anahtar sızdırmaz ve bayt sırasını da adlandırır:

```
sudo btbond to-host --verify
  BR/EDR xx:…  "Soundcore Life Q10"
    LinkKey  fp=3de37ff1c11a  EŞLEŞİYOR (aynı sıra)
```

**Yapı dökümü** — misafirdeki bond'ların yalnız *şeklini* basan salt-okuma araç
(`ad : tip len=N`, baytlar basılmaz):

```
btbond guest-dump [domain]      # varsayılan: win11-nvme
```

**Kapalı misafir / dual boot — offline kovan** (`hivebond`).
Misafir **kapalı** olmalı; disk host'ta blok aygıtı olarak görünmeli.

```
# doğrudan bir bölüm (dual boot, ya da kapalı passthrough disk)
sudo mount -t ntfs3 -o ro /dev/<bölüm> /mnt/win
btbond hive --discover                    # zaten bağlı Windows'ları listele
sudo btbond hive /mnt/win                 # mount kökünü verin, kovanı bulur
sudo btbond hive /mnt/win --dump          # ham satırlar (ajanla aynı biçim)

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

**Aynı kanaldan yazma** (`btbond to-guest --offline`) — mount `rw` olmalı:

```
sudo mount -t ntfs3 /dev/<bölüm> /mnt/win
sudo btbond to-guest --offline /mnt/win --dry-run
sudo btbond to-guest --offline /mnt/win
```

Hedefin **mevcut durumu da bu kanaldan** okunur; yanlış kanaldan okumak
`ATLANDI`/`ÜZERİNE YAZILIYOR` hükmünü sessizce tersine çevirirdi. Yazım **tek
commit**: parti sınırı Windows'un komut satırı sınırı içindi ve burada yok,
üstelik tek commit yarım kalmış bir kaydı imkânsız kılıyor.

**Kovan yedeği — offline yazımın ön koşulu.** `hivex` commit'i `SYSTEM`
dosyasını **yerinde** yeniden yazar; bozulan kovan Windows'u açılmaz hâle
getirir ve geri dönecek bir şey bırakmaz. Passthrough disk ve dual boot'ta
qcow2 snapshot'ı da yoktur (bu makinede ölçüldü: misafirin Windows'u ham bir
NVMe bölümünde). O yüzden yazımdan önce kovanın kopyası **host tarafına**
alınır — misafirin diskine değil: bölüm `ro` bağlanmış olabilir ve oraya
bırakılan dosya Windows'a görünür.

```
sudo btbond to-guest --offline /mnt/win                      # /var/backup/btbond
sudo btbond to-guest --offline /mnt/win --backup-dir /yol    # başka yer
sudo btbond to-guest --offline /mnt/win --no-backup          # geri dönüş YOK
```

Yedek **alınamazsa yazma hiç başlamaz**: yedeksiz yazmak, yedek isteyip
alamamaktan ayrı bir karardır. Kopya kovanın tamamıdır, yani Windows'un bütün
sırlarını taşır — dizin `0700`, dosya `0600` açılır, silmek kullanıcının işi.

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
commit), misafir açıldı, ve `btbond status` üçünü de `eşleşiyor`
verdi — yani **Windows'un kendi kayıt defteri motoru** hivex'in yazdığı
baytları aynen sunuyor. Kalan boşluk: yazımdan sonra radyo o misafire hiç
verilmediği için **cihazların fiilen bağlandığı** görülmedi, ve **dual boot**
kolu (domain yerine disk yolu) hiç koşmadı.

## TUI

```
sudo btbond tui                          # tanımlı bütün domain'ler
sudo btbond tui --domain win11-nvme      # tek hedef
```

**Bir yön sihirbazı değil, bir diff görünümü.** Açılışta "nereden nereye" diye
sorulmuyor: yönü veri söylüyor, ve global bir yön kipi tam o tek belirsizlikte
(`ANAHTAR FARKLI`) yeniyi eskiyle sessizce ezerdi. Satır başına hüküm, satırın
ima ettiği yön, ve `Enter` o yönde replikasyon. Ayrışan satırda `Enter`
**iki parmak izini ekrana koyup sorar** — karar verebilmek için gereken iki
değer ancak orada yan yana durur.

| tuş | ne yapar |
|---|---|
| `r` | tazele (taraf başına ~1 sn ajan; kapalı taraf ~0,2 sn kovan) |
| `d` | seçili satırın ayrıntısı (parmak izleri, adres tipi) |
| `Enter` | satırın ima ettiği yönde replike et — kapıdan geçerse |
| `s` | **topla + dağıt** (iki fazlı akış, kapsamın tamamı) |
| `h` | **radyoyu devret** — yalnız cihazları o tarafta *kullanmak* için |
| `?` / `q` | yardım / çık |

**İki kullanıcı dostu varsayılan, ikisi de kapatılabilir:**

- **Kapalı misafir kendiliğinden bağlanıp okunur** (`--no-auto-mount` ile
  kapatılır). Disk keşfediliyor (imaj XML'den, PCI passthrough sysfs'ten),
  salt-okuma bağlanıyor, okunuyor, hemen çözülüyor. Yazım anında **RW yeniden
  bağlanır**, bitince çözülür. Koşan VM'in diski **asla** bağlanmaz. Ölçüldü:
  üç kapalı domain 0,5 sn — ajandan hızlı.
- **`→ host` yazımı için radyo devri gerekmez** (`--no-stop-bluetooth` ile
  kapatılır). Host radyoyu tutuyorsa `bluetoothd` **durdurulur → yazılır →
  başlatılır**; adaptör yeniden kurulur ve anahtarları taze okur. Bedeli host
  BT bağlantılarının birkaç saniye düşmesi — radyoyu bir VM'e verip geri
  almanın yanında ucuz. Sıra önemli: yazımdan *sonra* restart, koşan
  bluetoothd'nin dosyayı bellekten ezme riskini kaldırmaz; *önce* durdurmak
  kaldırır.

**Kapının asıl sorusu** bu yüzden *"hedef radyoyu tutuyor mu"* değil,
*"bu yazımdan sonra hedef anahtarları TAZE okuyabilecek mi"* — ve bunun iki
cevabı var: radyo sonradan gelir, ya da yığın yeniden başlar. Misafir tarafında
yığını yeniden başlatmanın karşılığı (Windows BT cihazını PnP'den kapat/aç)
**ölçülmedi**, o yüzden `→ misafir` için kaçış yok; ama orada normal durumda
misafir radyoyu tutmuyor ve kapı zaten geçiyor.

**Yetenek eşitliği — ölçüt "birebir aynı" değil.** Duruma bakarak verilen bir
karar CLI'a özel kalmaz; bir *ölçüm kolu* ise CLI'da kalır. O yüzden TUI'de
offline taraf, iki fazlı akış ve devir **var**; `--json` (makine çıktısı) ve
`--key-order` / `--authreq` / `--le-flags` (ölçülmemiş kolları açan deney
bayrakları) **yok** — onları tek tuşa bağlamak kullanıcıya ölçülmemiş bir şeyi
yaptırmak olurdu.

`s` ve `h` mantığı TUI'de **yeniden yazılmadı**: `btbond` çağrılıyor,
tıpkı onun yazıcıları çağırdığı gibi. Faz sırası, faz arası yeniden ölçüm ve
devrin `vfioctl` çağrısı tek sahipte kalıyor.

**Her taraf görünür, ve dört durum ayrı:**

| satır | ne demek |
|---|---|
| `eşleşiyor` / `yalnız …` | taraf **okundu** (ajan ya da kovan) |
| `OKUNDU — bond yok` | taraf okundu, eşleşmiş cihaz yok — bu bir **ölçüm** |
| `ÖLÇÜLMEDİ (kapalı)` | taraf **var**, diski bulundu ama okunamadı (otomatik bağlama kapalı ya da düştü) |
| `ULAŞILAMADI` | disk da bulunamadı |

*"Bond yok"* ile *"ölçmedim"* ayrı satırlar. Disk keşfi salt-okuma: imaj dosyası
domain XML'inden, PCI passthrough `/sys/bus/pci/devices/<adres>/nvme/*/nvme*n*`
üzerinden (bu makinede `0000:02:00.0` → `/dev/nvme1n1`, yalnız domain
**kapalıyken**; koşarken cihaz `vfio-pci`'de). Kapsam: yalnız NVMe.

**Mount zincirinin güvenceleri** (`sidemount`) — zincir ayrıcalıklı ve
durumlu, araç ortasında ölürse geriye bağlı bir nbd ve mount'lu bir dosya
sistemi kalır; o yüzden üç güvence, üçü zorunlu: (1) context manager, `__exit__`
istisnada da koşar ve umount + disconnect'i **ayrı ayrı** dener; (2) her mount
`/run/btbond/mounted.json`a **PID ile** yazılır, `/run` tmpfs olduğu için
yeniden başlatmada kendiliğinden temizlenir, çökme sonrası `btbond cleanup`
ölü PID'lerin kayıtlarını çözer — canlı sürecinkine dokunmaz;
(3) domain **kapalı değilse reddedilir**. Ölçüldü: gerçek blok aygıtı ve qcow2,
istisna ortasında çözme, bayat kayıt temizliği, koşan-domain reddi — 19/19.
Elle mount hâlâ mümkün: `--offline DOMAIN=MOUNT`.

**Tazeleme açık ve bloklamıyor, çünkü ölçüldü:**

| yarı | süre |
|---|---|
| host (dosyalar) | **0,6 ms** |
| radyonun yeri (iki kanal) | 14 ms |
| misafir (`guest-exec` turu) | **1073 ms** |

Misafir yarısı ~1800× baskın, yani bluetui'nin canlı D-Bus okuması burada
**taklit edilemez**. Ölçüm bir iş parçacığında koşuyor ve başlık verinin hangi
saatte alındığını söylüyor; bir yazımdan sonra tablo **BAYAT** işaretlenir
(kendiliğinden tazelenmez — bir saniyelik tur, ve ekranda hangi verinin
durduğu bilinmek zorunda).

Kapı TUI'de **yeniden yazılmadı**: `bondsync.write_gate` çağrılıyor, aynı
fonksiyon `btbond`un fazlarını da kesiyor. İki kopya tutulsaydı biri
donar, ve donmuş olan yıkıcı tarafta durur.

Toolkit **Textual** (Python), ve sebebi mimari: uygulama modeli `import`
ediyor, yani `--json` şeklinin ikinci bir tüketicisi ve düzenin/kapının ikinci
bir sahibi doğmuyor. Rust/Ratatui bir süreç sınırı ve bir ayrıştırıcı daha
eklerdi.

## Testler

Misafir, root, terminal ve kovan gerektirmeyen beş sözleşme testi:

```
tests/test_emitters.py       # altın çıktı: yazma emitörleri aynı metni üretiyor mu
tests/test_transport.py      # taşıyıcı ve model sözleşmeleri
tests/test_hcicapture.py     # btmon/hcitool ayrıştırması
tests/test_servicecache.py   # SDP önbelleği, kovan yedeği, bağlı Windows keşfi
tests/test_tui.py            # TUI kararları (Textual'ın başsız sürücüsü)
tests/test_emitters.py --update   # altın dosyayı KASITLI olarak yenile
```

`test_servicecache.py`'nin dördü de **sessizce** bozulan sınıfta: ters çevrilmiş
bir SDP sarmalı BlueZ'e ayrıştırma hatası olarak gider, alınmamış bir kovan
yedeği ancak kovan bozulunca fark edilir, çözülmemiş bir `\040` kaçışı yanlış
diske bakar, ve düşen bir rol-LTK bölümü zaten sessizdi.

TUI testi arayüzün **kararlarını** ölçüyor, çizimini değil: hükümler modelden
mi geliyor, kapı yıkıcı yolu kesiyor mu, `ANAHTAR FARKLI` kendiliğinden
koşmuyor mu, ve yazımdan sonra tablo bayat işaretleniyor mu.

**Bir denetim de tuşa BASIYOR, ve sebebi ödenmiş bir hata** (2026-09-04):
yazma testlerinin tamamı `app.action_replicate()`i doğrudan çağırıyordu, yani
kararı sınıyor ama **tuşu** sınamıyordu. O boşlukta `Enter` ölüydü — odak
`DataTable`da ve widget tuşu kendi seçim eylemine harcıyordu, uygulama bağı
hiç koşmuyor ve aynı sebeple alt çubukta da görünmüyordu. Çare bağın
`priority=True` olması; onu ancak tuşa basan bir test koruyabilir.

`sidemount` bu pakette **değil**: gerçek disk, root ve `nbd` istiyor. Ölçümü
elle yapıldı (2026-09-04, 19/19) ve arşivde kayıtlı; kapalı bir domain ile
yeniden koşturulabilir.

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
- [x] Tek komutluk akış (`btbond status` / `sync`) — yön satırın
      özelliği, yazma sırası kapı olarak uygulanıyor
- [x] TUI (`btbond tui`) — diff görünümü, açık/bloklamayan tazeleme,
      kapı tek sahipten; toolkit kararı Textual (model import edilir)
- [x] Offline kovan **okuma** arka ucu (`hivebond`) — kapalı misafir ve
      dual boot; iki taraflı doğrulandı (altı parmak izi ajanla birebir aynı),
      bölüm ve qcow2 kolları ayrı ayrı koştu
- [x] Offline kovan **yazma** (`btbond to-guest --offline`) + hızlı başlatma
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
- [x] Offline taraf `status`ta ve TUI'de görünür (`--offline DOMAIN=MOUNT`),
      diski keşfediliyor, ve okunmamış taraf `ÖLÇÜLMEDİ` diye ayrılıyor
- [x] Mount otomasyonu (`sidemount`): keşif + `qemu-nbd` + içerikle bölüm
      + garantili çözme; CLI ve TUI'de varsayılan
- [x] Ters yönde offline (`win-to-bluez --offline`) — kapalı taraftan toplama
- [x] `→ host` yazımında radyo devri yerine bluetoothd stop/start — kapı artık
      "hedef taze okuyabilecek mi" diye soruyor
- [ ] `→ misafir` için aynısı: Windows BT yığınını PnP'den kapat/aç (ölçülmedi)
- [x] Öğrenilen dört alan araçla toplanıyor (`remote-info`, devir gerekmez) ve
      `btbond to-guest` onları **yazıyor** — eskiden elle yazılıyorlardı
- [x] Aracın yazdığı kayıt, Windows'un hiç görmediği bir cihazda profil
      devnode'larını **sürücüleriyle** doğuruyor ve ses geliyor — eksik parça
      `DynamicCachedServices`ti, altın kayıtla diff bulup tek değişkenli test
      doğruladı (2026-09-04)

## Bilinen boşluk

Windows, BR/EDR profil devnode'larını (A2DP, AVRCP, HFP) ancak cihazın **neyi
desteklediğini** bildiğinde kuruyor. O bilgi `Devices\<mac>` altındaki dört
alanda: `LMPFeatures`, `ManufacturerId`, `LmpVersion`, `LmpSubversion`. Windows
bunları cihaza bağlanınca kendisi öğrenir — ama bond kaydı yeni yazıldığında
henüz yoktur, ve onlar olmadan ses uç noktası çıkmaz.

Bunlar **hiçbir BlueZ dosyasında yok** (`info` da, `cache/<mac>` da taşımıyor);
HCI'dan okunur — ve dördü de artık **araçla toplanıp yazılıyor**
(`remote-info`, yukarıda).

**Dördü de iki taraflı doğrulandı** (2026-09-04, üç cihaz, iki taşıyıcı):
host'un HCI'dan okuduğu değerler, aynı cihazların Windows kayıt defterindeki
karşılıklarına **birebir eşit** çıktı (10 alan karşılaştırıldı, 0 fark).
`LMPFeatures` little-endian QWORD; `LmpVersion`/`LmpSubversion`/
`ManufacturerId` DWORD.

Yol boyunca çözülen asıl soru şuydu: `Read Remote Version Information` olayı
neden hiç ateşlemiyordu? Cevap "çekirdek koşulsuz istemiyor" değil, **hiç
istemiyor** — `bluetooth.ko`da o opcode (`0x041d`) sıfır kez geçiyor ve olayın
işleyicisi yok, oysa kardeşlerinin hepsi var. Komut kullanıcı alanından
yollanınca (`hcitool cmd`) olay geliyor, ve bu var olan bir bağlantıda da
çalışıyor.

**Boşluk kapandı (2026-09-04), ve kapatan şey bu dört alan değildi.** Dördü
yazılıyken bile Windows A2DP sürücüsünü bağlamıyor, link ~20 sn'de düşüyordu.
Windows'un kendi eşleştirdiği bir kayıtla diff alınınca (46 değer aynı) tek
yapısal fark kaldı: **`DynamicCachedServices`**. Windows servis düğümlerini
`CachedServices`ten değil oradan açıyor; içerik aynı SDP kayıtları, yalnız
dış sarmalın uzunluk kodlaması farklı (`35 LL` ↔ `36 00LL`). Dönüşüm beş
gerçek kayıtta bayt bayt doğrulandı, ve tek değişkenli uçtan uca testte
sürücüler BTHPORT başlar başlamaz bağlandı, link tuttu, ses uç noktaları
doğdu — aracın yazdığı kayıttan. Ayrıntı → `winbond.DYNAMIC_NOTU`.

Bu boşluk **yalnız BR/EDR profillerini** etkiliyordu: bond, kimlik doğrulaması
ve LE tarafının tamamı bu alanlar olmadan da çalışıyor.
