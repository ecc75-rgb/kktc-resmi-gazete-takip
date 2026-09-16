# KKTC Resmî Gazete Takibi

KKTC Devlet Basımevi ana sayfasını GitHub Actions ile yaklaşık 5 dakikada bir kontrol eder. Yeni bir Resmî Gazete sayısı yayımlandığında Telegram'a sayı, tarih, içerik özeti ve PDF bağlantısını gönderir.

## Çalışma şekli

- Bilgisayarın açık olmak zorunda değildir.
- Başlangıç kaydı: **176 — 15.09.2026**
- Aynı sayı için tekrar bildirim göndermez.
- Arka arkaya birden fazla sayı yayımlanırsa hepsini sırayla bildirir.
- Repo etkinlik nedeniyle 60 gün sonunda uyumasın diye 45 günde bir otomatik etkinlik kaydı yeniler.
- GitHub zamanlanmış işleri yoğunluğa göre birkaç dakika gecikebilir; tam saniyesinde çalışma garantisi yoktur.

## Gerekli GitHub Secrets

Repo içinde **Settings → Secrets and variables → Actions → New repository secret** yolundan şu iki secret eklenmelidir:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Secret değerlerini hiçbir dosyaya veya mesaja açık şekilde yazmayın.

## Test

**Actions → KKTC Resmi Gazete Takibi → Run workflow** bölümünde `send_test` seçeneğini açıp çalıştırın. Telegram'a “Test başarılı” mesajı gelirse kurulum tamamdır.

İzlenen site: https://basimevi.gov.ct.tr/
