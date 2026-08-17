# Nạp code ESP32 qua trình duyệt

Webapp tĩnh cho phép **học sinh nạp firmware (.bin) vào bo ESP32 trực tiếp từ trình duyệt**, chỉ bằng cáp USB — không cần cài Arduino IDE hay driver nạp. Bạn (giáo viên) biên dịch chương trình thành file `.bin`, tải lên GitHub, học sinh mở trang và bấm nạp.

Hoạt động nhờ **Web Serial API** + thư viện **esptool-js** (đã đóng gói sẵn trong `vendor/`, không cần mạng ngoài / CDN).

---

## 1. Học sinh cần gì để nạp được

- **Máy tính** (Windows, macOS, Linux, Chromebook). **Điện thoại không nạp được.**
- **Trình duyệt**: Google Chrome, Microsoft Edge hoặc Cốc Cốc. *(Firefox và Safari chưa hỗ trợ Web Serial.)*
- **Cáp USB truyền dữ liệu** (không phải cáp chỉ sạc).
- Với bo dùng chip **CH340/CH9102**: một số máy cần cài driver một lần. Chip **CP2102** thường tự nhận.

Trang phải chạy qua **HTTPS** (GitHub Pages có sẵn) hoặc **localhost**. Mở trực tiếp file `index.html` bằng `file://` sẽ **không** nạp được.

---

## 2. Cấu trúc thư mục

```
├── index.html          ← trang chính
├── style.css           ← giao diện
├── app.js              ← xử lý nạp
├── config.json         ← DANH SÁCH CHƯƠNG TRÌNH (bạn chỉnh file này)
├── vendor/
│   └── esptool-bundle.js   ← thư viện nạp (không cần sửa)
├── firmware/           ← ĐẶT CÁC FILE .bin CỦA BẠN VÀO ĐÂY
│   ├── led.bin
│   ├── servo.bin
│   └── oled.bin
└── libraries/          ← (tùy chọn) nơi chia sẻ mã nguồn / thư viện Arduino
```

Bạn chỉ cần đụng tới **`config.json`** và thư mục **`firmware/`**.

---

## 3. Chuẩn bị file `.bin` (phần quan trọng nhất)

Có 2 cách. **Cách A dễ thành công nhất cho học sinh**, kể cả với bo mới tinh.

> 💡 **Có công cụ tự động:** để khỏi gõ lệnh thủ công cho từng chương trình, chạy `python tool_gop_firmware.py` (cần `pip install esptool`). Tool có giao diện: bạn chỉ trỏ vào thư mục build của từng sketch, chọn dòng chip, nó tự tìm file, tự gộp và xuất thẳng vào `firmware/`, đồng thời cập nhật luôn `config.json`. Phần bên dưới giải thích cách làm thủ công để bạn hiểu bản chất.

### Cách A — File gộp (merged), nạp tại `0x0` ✅ khuyên dùng

File gộp chứa sẵn bootloader + bảng phân vùng + chương trình, nạp ở địa chỉ `0x0`, chạy được trên mọi bo. Đây là mặc định trong `config.json` mẫu.

Bước 1 — Cài `esptool` (chỉ làm 1 lần, cần Python):
```bash
pip install esptool
```

Bước 2 — Lấy 4 file thành phần. Trong Arduino IDE:
- Bật **File → Preferences → “Show verbose output during: upload”**.
- Nạp thử sketch một lần cho bo, rồi đọc dòng lệnh `esptool` trong cửa sổ Output. Bạn sẽ thấy các file và **địa chỉ (offset)** mà Arduino dùng, ví dụ:
  ```
  0x1000  ....bootloader.bin
  0x8000  ....partitions.bin
  0xe000  ....boot_app0.bin
  0x10000 ....ino.bin        ← đây là chương trình của bạn
  ```

Bước 3 — Gộp thành 1 file (ví dụ cho ESP32 “thường”, flash 4MB):
```bash
esptool --chip esp32 merge-bin -o led.bin \
  --flash-mode dio --flash-freq 40m --flash-size 4MB \
  0x1000  bootloader.bin \
  0x8000  partitions.bin \
  0xe000  boot_app0.bin \
  0x10000 ten_sketch.ino.bin
```
Nếu chạy đúng sẽ báo: `... ready to flash to offset 0x0`. Đổi tên `led.bin` cho từng chương trình rồi bỏ vào thư mục `firmware/`.

> **esptool bản cũ (v4 trở về trước)** dùng gạch dưới: `merge_bin`, `--flash_mode`, `--flash_freq`, `--flash_size`. Bản mới (v5) dùng gạch ngang như trên. Gõ `esptool version` để biết bạn đang dùng bản nào.

> **Lưu ý theo dòng chip:** ESP32 “thường” bootloader ở `0x1000`. Còn **ESP32-S3 / C3 / C6 / S2** bootloader ở `0x0` — hãy dùng đúng offset mà dòng verbose hiển thị.

Với file gộp, nên hướng dẫn học sinh **bật ô “Xóa toàn bộ flash trước khi nạp”** cho chắc.

### Cách B — File chương trình đơn, nạp tại `0x10000`

Nếu bo đã từng nạp chương trình Arduino (đã có sẵn bootloader), bạn có thể chỉ dùng file app.

- Trong Arduino IDE: **Sketch → Export Compiled Binary**. File `*.ino.bin` nằm trong thư mục build (KHÔNG lấy file `*.bootloader.bin`).
- Đưa file đó vào `firmware/`, rồi trong `config.json` đặt `offset` là `65536` (tức `0x10000`).
- **Không** bật “Xóa toàn bộ flash” ở cách này (sẽ xóa mất bootloader).

---

## 4. Thêm / sửa chương trình trong `config.json`

Mở `config.json` và sửa danh sách `firmwares`. Mỗi chương trình là một mục:

```json
{
  "id": "led",
  "name": "Đèn LED WS2812B",
  "description": "Điều khiển dải đèn LED RGB nhiều hiệu ứng.",
  "chip": "ESP32",
  "flashMode": "keep",
  "flashFreq": "keep",
  "flashSize": "keep",
  "parts": [
    { "path": "firmware/led.bin", "offset": 0 }
  ]
}
```

- `name`, `description`: chữ hiện trên thẻ cho học sinh chọn.
- `chip`: nhãn hiển thị (ESP32, ESP32-S3…).
- `parts`: danh sách file cần nạp và **offset** (địa chỉ). File gộp → `0`. File app đơn → `65536`.
- `flashMode/Freq/Size`: để `"keep"` là an toàn (giữ theo thông số trong file).

**Nạp nhiều file rời (cách B đầy đủ)** cho một chương trình:
```json
"parts": [
  { "path": "firmware/bootloader.bin", "offset": 4096 },
  { "path": "firmware/partitions.bin", "offset": 32768 },
  { "path": "firmware/boot_app0.bin",  "offset": 57344 },
  { "path": "firmware/led.bin",        "offset": 65536 }
]
```
(4096 = 0x1000, 32768 = 0x8000, 57344 = 0xe000, 65536 = 0x10000)

Bạn cũng có thể đổi các dòng ở đầu file: `title`, `subtitle`, `eyebrow` (tên lớp), `footer`, và `defaultBaudrate`.

---

## 5. Đưa lên GitHub Pages

1. Tạo repository mới trên GitHub (ví dụ `nap-code-esp32`).
2. Tải toàn bộ các file trong thư mục này lên repo (kéo–thả trên web hoặc dùng git).
3. Vào **Settings → Pages**.
4. Mục **Build and deployment → Source**: chọn **Deploy from a branch**.
5. Chọn nhánh **main** và thư mục **/ (root)**, bấm **Save**.
6. Chờ khoảng 1 phút, GitHub sẽ cho địa chỉ dạng:
   `https://<tên-của-bạn>.github.io/nap-code-esp32/`
7. Gửi địa chỉ đó cho học sinh.

> File `.nojekyll` đã có sẵn để GitHub phục vụ đúng các thư mục như `vendor/`.

**Cập nhật chương trình sau này:** chỉ cần thay file `.bin` trong `firmware/` (và sửa `config.json` nếu cần), commit lại. Học sinh tải lại trang là có bản mới.

---

## 6. Chạy thử trên máy trước khi đưa lên mạng

Vì `file://` không nạp được, hãy mở bằng một server cục bộ (localhost vẫn nạp được đầy đủ):

```bash
# tại thư mục dự án
python -m http.server 8000
```
Rồi mở `http://localhost:8000/` bằng Chrome/Edge.

---

## 7. Xử lý sự cố

| Hiện tượng | Cách khắc phục |
|---|---|
| Không thấy cổng nào khi bấm Kết nối | Đổi cáp USB (dùng cáp truyền dữ liệu); cài driver CH340/CP2102; thử cổng USB khác. |
| Kết nối được nhưng báo lỗi sync/timeout | **Giữ nút BOOT** trên bo trong lúc bấm Kết nối; hạ **Tốc độ nạp** về 115200. |
| Cổng bị chiếm / không mở được | Đóng Arduino IDE và Serial Monitor rồi thử lại. |
| Nạp xong nhưng bo không chạy chương trình | Thường do dùng file app đơn nạp vào bo mới. Hãy dùng **file gộp (Cách A)** và bật “Xóa toàn bộ flash”. |
| Nạp tới giữa chừng thì lỗi | Hạ tốc độ về 115200, đổi cáp, rút ra cắm lại rồi nạp lại. |
| Học sinh mở bằng điện thoại / Safari | Không hỗ trợ. Dùng máy tính với Chrome/Edge/Cốc Cốc. |
| Nút “Chọn cổng khác” | Dùng khi cắm nhiều bo hoặc chọn nhầm cổng. |

---

*Thư viện esptool-js được đóng gói sẵn trong `vendor/esptool-bundle.js` nên webapp không phụ thuộc mạng ngoài.*
