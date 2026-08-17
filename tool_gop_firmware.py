#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CÔNG CỤ GỘP FIRMWARE ESP32
--------------------------
Biến các file .bin do Arduino biên dịch (bootloader + partitions + boot_app0 + app)
thành MỘT file .bin nạp tại địa chỉ 0x0, để dùng với webapp "Nạp code ESP32" trên
trình duyệt. Chỉ cần trỏ vào thư mục build của từng sketch, tool tự tìm file, tự chạy
esptool merge-bin và xuất thẳng vào thư mục firmware/.

Yêu cầu: Python 3 + esptool  (cài bằng:  pip install esptool)
Chạy:    python tool_gop_firmware.py
"""

import os
import re
import sys
import glob
import json
import queue
import threading
import subprocess
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ----------------------------------------------------------------------------
# HẰNG SỐ
# ----------------------------------------------------------------------------

# Offset bootloader theo dòng chip (theo mã nguồn ESP-IDF). Mặc định 0x0.
BOOTLOADER_OFFSET = {
    "esp32":   0x1000,
    "esp32s2": 0x1000,
    "esp32p4": 0x2000,
    "esp32s3": 0x0,
    "esp32c3": 0x0,
    "esp32c6": 0x0,
    "esp32h2": 0x0,
    "esp32c2": 0x0,
}
DANH_SACH_CHIP = ["esp32", "esp32s3", "esp32c3", "esp32c6", "esp32s2", "esp32h2", "esp32c2", "esp32p4"]

# Offset cố định (giống nhau cho mọi chip khi dùng sơ đồ phân vùng mặc định của Arduino)
OFFSET_PARTITIONS = 0x8000
OFFSET_BOOT_APP0  = 0xe000
OFFSET_APP        = 0x10000


# ----------------------------------------------------------------------------
# PHẦN LÕI (tách riêng khỏi giao diện để dễ kiểm thử)
# ----------------------------------------------------------------------------

def tim_file_thanh_phan(build_dir):
    """Tìm bootloader/partitions/app trong thư mục build (và thư mục con 1 cấp).
    Trả về dict {'bootloader':..., 'partitions':..., 'app':...}.
    Ném ValueError kèm thông báo tiếng Việt nếu thiếu file."""
    build_dir = Path(build_dir)
    if not build_dir.is_dir():
        raise ValueError("Thư mục build không tồn tại: %s" % build_dir)

    # Gom tất cả file .bin trong thư mục và các thư mục con 1 cấp
    cac_bin = list(build_dir.glob("*.bin")) + list(build_dir.glob("*/*.bin"))
    if not cac_bin:
        raise ValueError("Không thấy file .bin nào trong:\n%s\n\n"
                         "Hãy chọn đúng thư mục build (nơi Arduino xuất file sau khi "
                         "Sketch → Export Compiled Binary)." % build_dir)

    bootloader = next((f for f in cac_bin if f.name.endswith(".bootloader.bin")), None)
    partitions = next((f for f in cac_bin if f.name.endswith(".partitions.bin")), None)

    # File app = .bin nhưng KHÔNG phải bootloader/partitions/boot_app0
    ung_vien_app = [f for f in cac_bin
                    if not f.name.endswith(".bootloader.bin")
                    and not f.name.endswith(".partitions.bin")
                    and "boot_app0" not in f.name.lower()]
    app = None
    # Ưu tiên file kết thúc bằng .ino.bin; nếu nhiều thì lấy file lớn nhất (app thường lớn nhất)
    uu_tien = [f for f in ung_vien_app if f.name.endswith(".ino.bin")]
    nguon = uu_tien if uu_tien else ung_vien_app
    if nguon:
        app = max(nguon, key=lambda f: f.stat().st_size)

    thieu = []
    if bootloader is None: thieu.append("bootloader (*.bootloader.bin)")
    if partitions is None: thieu.append("bảng phân vùng (*.partitions.bin)")
    if app is None:        thieu.append("chương trình (*.ino.bin)")
    if thieu:
        raise ValueError("Trong thư mục build thiếu: %s.\n\n"
                         "Hãy chọn đúng thư mục build của sketch." % ", ".join(thieu))

    return {"bootloader": str(bootloader), "partitions": str(partitions), "app": str(app)}


def tim_boot_app0(build_dir=None):
    """Tìm boot_app0.bin: ưu tiên trong thư mục build, sau đó tìm trong thư mục
    cài đặt lõi ESP32 của Arduino trên Windows/macOS/Linux. Trả về đường dẫn hoặc None."""
    # 1) Ngay trong thư mục build (một số cấu hình có sẵn)
    if build_dir:
        for p in list(Path(build_dir).glob("**/boot_app0.bin")):
            return str(p)

    # 2) Các vị trí cài lõi ESP32 thường gặp
    home = Path.home()
    goc_kha_di = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Arduino15",   # Windows
        home / "AppData" / "Local" / "Arduino15",                 # Windows (dự phòng)
        home / "Library" / "Arduino15",                           # macOS
        home / ".arduino15",                                      # Linux
        home / ".config" / "arduino15",                           # Linux (dự phòng)
    ]
    ket_qua = []
    for goc in goc_kha_di:
        if goc and goc.exists():
            ket_qua += glob.glob(str(goc / "packages" / "esp32" / "hardware" / "esp32" /
                                      "*" / "tools" / "partitions" / "boot_app0.bin"))
    if not ket_qua:
        return None

    # Chọn phiên bản lõi mới nhất
    def khoa_phien_ban(path):
        m = re.search(r"hardware[\\/]+esp32[\\/]+([0-9][^\\/]*)[\\/]", path)
        if not m:
            return (0,)
        so = re.findall(r"\d+", m.group(1))
        return tuple(int(x) for x in so) if so else (0,)

    ket_qua.sort(key=khoa_phien_ban)
    return ket_qua[-1]


def phat_hien_esptool():
    """Tìm esptool dùng chính Python đang chạy. Trả về (lenh_goi, phien_ban_tuple) hoặc None.
    lenh_goi ví dụ: [sys.executable, '-m', 'esptool']."""
    ung_vien = [
        [sys.executable, "-m", "esptool"],
        ["esptool"],
        ["esptool.py"],
    ]
    for goi in ung_vien:
        try:
            out = subprocess.run(goi + ["version"], capture_output=True, text=True, timeout=25)
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        chuoi = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", chuoi)
        if out.returncode == 0 and m:
            pb = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
            return (goi, pb)
    return None


def tao_lenh_merge(lenh_goi, phien_ban, chip, out_path, cac_phan,
                   flash_mode="keep", flash_freq="keep", flash_size="keep"):
    """Dựng danh sách tham số cho esptool merge-bin.
    cac_phan: list các cặp (offset_int, duong_dan_file), theo thứ tự offset tăng dần.
    esptool v5 dùng gạch ngang (merge-bin, --flash-mode); v4 dùng gạch dưới."""
    gach_ngang = phien_ban[0] >= 5
    ten_merge = "merge-bin" if gach_ngang else "merge_bin"
    fm = "--flash-mode" if gach_ngang else "--flash_mode"
    ff = "--flash-freq" if gach_ngang else "--flash_freq"
    fs = "--flash-size" if gach_ngang else "--flash_size"

    cmd = list(lenh_goi) + ["--chip", chip, ten_merge, "-o", str(out_path),
                            fm, flash_mode, ff, flash_freq, fs, flash_size]
    for offset, duong_dan in cac_phan:
        cmd += [hex(offset), str(duong_dan)]
    return cmd


def cap_nhat_config_json(config_path, muc):
    """Cập nhật config.json an toàn: tìm theo 'id', có thì cập nhật parts/chip
    (giữ nguyên name/description cũ nếu có), chưa có thì thêm mới.
    muc: dict gồm id, name, description, chip, parts."""
    config_path = Path(config_path)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {
            "eyebrow": "Lớp học STEM",
            "title": "Nạp code ESP32",
            "subtitle": "Cắm bo mạch vào máy tính, chọn chương trình rồi bấm nạp. Không cần cài phần mềm.",
            "footer": "Mở trang bằng Chrome, Edge hoặc Cốc Cốc trên máy tính. Điện thoại không nạp được.",
            "defaultBaudrate": 115200,
            "firmwares": [],
        }

    ds = data.setdefault("firmwares", [])
    cu = next((fw for fw in ds if fw.get("id") == muc["id"]), None)
    if cu is None:
        ds.append({
            "id": muc["id"],
            "name": muc["name"],
            "description": muc.get("description", ""),
            "chip": muc.get("chip", ""),
            "flashMode": "keep", "flashFreq": "keep", "flashSize": "keep",
            "parts": muc["parts"],
        })
    else:
        cu["parts"] = muc["parts"]
        if muc.get("chip"):
            cu["chip"] = muc["chip"]
        # chỉ ghi đè name/description nếu đang trống
        if not cu.get("name"):
            cu["name"] = muc["name"]
        if not cu.get("description") and muc.get("description"):
            cu["description"] = muc["description"]

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ----------------------------------------------------------------------------
# GIAO DIỆN
# ----------------------------------------------------------------------------

BG      = "#0f1729"
PANEL   = "#16213d"
FG      = "#e6edf7"
MUTED   = "#8fa3c4"
ACCENT  = "#2ad4f0"
OKC     = "#35d99a"
WARN    = "#f9bd3b"
ERRC    = "#fb6f86"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Công cụ gộp firmware ESP32")
        self.configure(bg=BG)
        self.geometry("860x680")
        self.minsize(760, 600)

        self.esptool = None          # (lenh_goi, phien_ban)
        self.boot_app0 = tk.StringVar()
        self.chip = tk.StringVar(value="esp32")
        self.thu_muc_dich = tk.StringVar()
        self.cap_nhat_config = tk.BooleanVar(value=True)

        # ô nhập cho khu vực "thêm chương trình"
        self.v_build = tk.StringVar()
        self.v_ten   = tk.StringVar()
        self.v_mota  = tk.StringVar()
        self.v_file  = tk.StringVar()

        self.log_queue = queue.Queue()
        self.dang_chay = False

        self._dung_style()
        self._dung_giao_dien()
        self._mac_dinh_ban_dau()
        self.after(100, self._doc_log_queue)

    # ---- style ----
    def _dung_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED)
        st.configure("Head.TLabel", background=BG, foreground=FG, font=("Segoe UI", 11, "bold"))
        st.configure("TButton", background=PANEL, foreground=FG, borderwidth=0, padding=6)
        st.map("TButton", background=[("active", "#22345c")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#04222b",
                     font=("Segoe UI", 10, "bold"), padding=8)
        st.map("Accent.TButton", background=[("active", "#63e3f6"), ("disabled", "#294a55")])
        st.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, padding=4)
        st.configure("TCombobox", fieldbackground=PANEL, foreground=FG, padding=4)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                     rowheight=26, borderwidth=0)
        st.configure("Treeview.Heading", background="#1d2c4f", foreground=FG,
                     font=("Segoe UI", 9, "bold"))
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])

    # ---- dựng widget ----
    def _dung_giao_dien(self):
        pad = dict(padx=12, pady=6)

        head = ttk.Frame(self)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="Công cụ gộp firmware ESP32", style="Head.TLabel").pack(anchor="w")
        ttk.Label(head, style="Muted.TLabel",
                  text="Gộp bootloader + phân vùng + boot_app0 + chương trình → 1 file .bin nạp tại 0x0."
                  ).pack(anchor="w")

        # Cấu hình chung ------------------------------------------------------
        cfg = ttk.Frame(self, style="Panel.TFrame")
        cfg.pack(fill="x", **pad)
        for i in range(4):
            cfg.columnconfigure(i, weight=(1 if i in (1, 3) else 0))

        ttk.Label(cfg, text="Dòng chip:", background=PANEL).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        cbo = ttk.Combobox(cfg, textvariable=self.chip, values=DANH_SACH_CHIP, state="readonly", width=12)
        cbo.grid(row=0, column=1, sticky="w", padx=8)
        cbo.bind("<<ComboboxSelected>>", lambda e: self._cap_nhat_nhan_offset())
        self.lbl_offset = ttk.Label(cfg, text="", background=PANEL, foreground=MUTED)
        self.lbl_offset.grid(row=0, column=2, columnspan=2, sticky="w", padx=8)

        ttk.Label(cfg, text="boot_app0.bin:", background=PANEL).grid(row=1, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(cfg, textvariable=self.boot_app0).grid(row=1, column=1, columnspan=2, sticky="ew", padx=8)
        b0 = ttk.Frame(cfg, style="Panel.TFrame"); b0.grid(row=1, column=3, sticky="e", padx=8)
        ttk.Button(b0, text="Tự tìm", command=self._tu_tim_boot_app0).pack(side="left", padx=2)
        ttk.Button(b0, text="Duyệt…", command=self._duyet_boot_app0).pack(side="left", padx=2)

        ttk.Label(cfg, text="Thư mục firmware/:", background=PANEL).grid(row=2, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(cfg, textvariable=self.thu_muc_dich).grid(row=2, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(cfg, text="Duyệt…", command=self._duyet_thu_muc_dich).grid(row=2, column=3, sticky="e", padx=8)

        ttk.Checkbutton(cfg, text="Tự cập nhật config.json (ở thư mục cha của firmware/)",
                        variable=self.cap_nhat_config).grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))

        # Danh sách chương trình ---------------------------------------------
        ttk.Label(self, text="Danh sách chương trình", style="Head.TLabel").pack(anchor="w", padx=12, pady=(8, 0))
        khung_ds = ttk.Frame(self, style="Panel.TFrame")
        khung_ds.pack(fill="both", expand=True, padx=12, pady=6)

        cols = ("ten", "file", "build")
        self.tree = ttk.Treeview(khung_ds, columns=cols, show="headings", height=6, selectmode="browse")
        self.tree.heading("ten", text="Tên hiển thị")
        self.tree.heading("file", text="File xuất")
        self.tree.heading("build", text="Thư mục build")
        self.tree.column("ten", width=200)
        self.tree.column("file", width=130)
        self.tree.column("build", width=440)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(khung_ds, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y", pady=8)
        self.tree.configure(yscrollcommand=sb.set)

        hang_nut = ttk.Frame(self)
        hang_nut.pack(fill="x", padx=12)
        ttk.Button(hang_nut, text="🗑 Xóa dòng đang chọn", command=self._xoa_dong).pack(side="left")

        # dữ liệu ẩn cho từng dòng: iid -> dict
        self.du_lieu_dong = {}

        # Khu vực thêm chương trình ------------------------------------------
        them = ttk.Frame(self, style="Panel.TFrame")
        them.pack(fill="x", padx=12, pady=6)
        them.columnconfigure(1, weight=1)
        them.columnconfigure(3, weight=1)

        ttk.Label(them, text="Thư mục build:", background=PANEL).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(them, textvariable=self.v_build).grid(row=0, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(them, text="Duyệt…", command=self._duyet_build).grid(row=0, column=3, sticky="e", padx=8)

        ttk.Label(them, text="Tên hiển thị:", background=PANEL).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(them, textvariable=self.v_ten).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Label(them, text="Tên file xuất:", background=PANEL).grid(row=1, column=2, sticky="e", padx=8)
        ttk.Entry(them, textvariable=self.v_file, width=18).grid(row=1, column=3, sticky="w", padx=8)

        ttk.Label(them, text="Mô tả (tùy chọn):", background=PANEL).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(them, textvariable=self.v_mota).grid(row=2, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(them, text="➕ Thêm vào danh sách", command=self._them_chuong_trinh).grid(row=2, column=3, sticky="e", padx=8)

        # Nút chạy + log ------------------------------------------------------
        thanh = ttk.Frame(self); thanh.pack(fill="x", padx=12, pady=(8, 4))
        self.btn_gop = ttk.Button(thanh, text="Gộp tất cả", style="Accent.TButton", command=self._bat_dau_gop)
        self.btn_gop.pack(side="left")
        self.lbl_trang_thai = ttk.Label(thanh, text="", style="Muted.TLabel")
        self.lbl_trang_thai.pack(side="left", padx=12)

        khung_log = ttk.Frame(self, style="Panel.TFrame")
        khung_log.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.txt = tk.Text(khung_log, height=8, bg="#0b1424", fg="#cfe0f5", insertbackground=FG,
                           relief="flat", wrap="word", font=("Consolas", 9))
        self.txt.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        sb2 = ttk.Scrollbar(khung_log, orient="vertical", command=self.txt.yview)
        sb2.pack(side="left", fill="y", pady=8)
        self.txt.configure(yscrollcommand=sb2.set, state="disabled")
        self.txt.tag_configure("ok", foreground=OKC)
        self.txt.tag_configure("err", foreground=ERRC)
        self.txt.tag_configure("warn", foreground=WARN)
        self.txt.tag_configure("info", foreground="#9fd0ff")

    # ---- khởi tạo mặc định ----
    def _mac_dinh_ban_dau(self):
        self._cap_nhat_nhan_offset()

        # thư mục firmware/ mặc định: cạnh file script hoặc trong thư mục hiện tại
        goc = Path(__file__).resolve().parent
        ung_vien = [goc / "firmware", Path.cwd() / "firmware"]
        dat = next((p for p in ung_vien if p.exists()), ung_vien[0])
        self.thu_muc_dich.set(str(dat))

        # tự tìm boot_app0
        self._log("Đang dò tìm esptool và boot_app0.bin…", "info")
        threading.Thread(target=self._khoi_tao_nen, daemon=True).start()

    def _khoi_tao_nen(self):
        es = phat_hien_esptool()
        if es:
            self.esptool = es
            self.log_queue.put(("ok", "Đã tìm thấy esptool v%d.%d.%d." % es[1]))
        else:
            self.log_queue.put(("err", "CHƯA thấy esptool. Hãy cài bằng lệnh:  pip install esptool"))

        ba0 = tim_boot_app0()
        if ba0:
            self.boot_app0.set(ba0)
            self.log_queue.put(("ok", "Đã tìm thấy boot_app0.bin:\n  %s" % ba0))
        else:
            self.log_queue.put(("warn", "Chưa tự tìm được boot_app0.bin — hãy bấm “Duyệt…” để chọn, "
                                        "hoặc để trống nếu sơ đồ phân vùng của bạn không dùng file này."))

    # ---- tiện ích ----
    def _cap_nhat_nhan_offset(self):
        off = BOOTLOADER_OFFSET.get(self.chip.get(), 0x0)
        self.lbl_offset.config(text="bootloader @ %s  ·  partitions @ 0x8000  ·  boot_app0 @ 0xe000  ·  app @ 0x10000"
                                    % hex(off))

    def _log(self, msg, tag=None):
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n", (tag,) if tag else ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _doc_log_queue(self):
        try:
            while True:
                tag, msg = self.log_queue.get_nowait()
                if tag == "__done__":
                    self._ket_thuc_gop(msg)
                else:
                    self._log(msg, tag)
        except queue.Empty:
            pass
        self.after(100, self._doc_log_queue)

    # ---- các nút duyệt ----
    def _duyet_boot_app0(self):
        f = filedialog.askopenfilename(title="Chọn boot_app0.bin",
                                       filetypes=[("File bin", "*.bin"), ("Tất cả", "*.*")])
        if f:
            self.boot_app0.set(f)

    def _tu_tim_boot_app0(self):
        ba0 = tim_boot_app0()
        if ba0:
            self.boot_app0.set(ba0)
            self._log("Đã tìm thấy boot_app0.bin:\n  %s" % ba0, "ok")
        else:
            messagebox.showwarning("Không tìm thấy",
                                   "Chưa tự tìm được boot_app0.bin.\nHãy bấm “Duyệt…” để chọn thủ công.")

    def _duyet_thu_muc_dich(self):
        d = filedialog.askdirectory(title="Chọn thư mục firmware/ (nơi xuất file .bin)")
        if d:
            self.thu_muc_dich.set(d)

    def _duyet_build(self):
        d = filedialog.askdirectory(title="Chọn thư mục build của sketch")
        if not d:
            return
        self.v_build.set(d)
        # tự dò file để gợi ý tên
        try:
            tim_file_thanh_phan(d)  # kiểm tra hợp lệ
        except ValueError as e:
            messagebox.showwarning("Kiểm tra thư mục build", str(e))
            return
        ten_thu_muc = Path(d).name
        # loại phần "build" nếu người dùng trỏ vào .../build/<board>
        goi_y = ten_thu_muc
        if goi_y.lower() in ("build",) or re.match(r"^[a-z0-9]+\.[a-z0-9]+\.[a-z0-9]+$", goi_y):
            goi_y = Path(d).parent.name
            if goi_y.lower() == "build":
                goi_y = Path(d).parent.parent.name
        if not self.v_ten.get():
            self.v_ten.set(goi_y)
        if not self.v_file.get():
            slug = re.sub(r"[^a-z0-9]+", "_", goi_y.lower()).strip("_") or "chuong_trinh"
            self.v_file.set(slug + ".bin")

    def _them_chuong_trinh(self):
        build = self.v_build.get().strip()
        ten = self.v_ten.get().strip()
        ten_file = self.v_file.get().strip()
        if not build or not ten or not ten_file:
            messagebox.showwarning("Thiếu thông tin", "Cần điền Thư mục build, Tên hiển thị và Tên file xuất.")
            return
        if not ten_file.lower().endswith(".bin"):
            ten_file += ".bin"
        try:
            files = tim_file_thanh_phan(build)
        except ValueError as e:
            messagebox.showwarning("Thư mục build không hợp lệ", str(e))
            return

        slug = re.sub(r"[^a-z0-9]+", "_", Path(ten_file).stem.lower()).strip("_") or "ct"
        iid = self.tree.insert("", "end", values=(ten, ten_file, build))
        self.du_lieu_dong[iid] = {
            "id": slug, "name": ten, "description": self.v_mota.get().strip(),
            "ten_file": ten_file, "build": build, "files": files,
        }
        # dọn ô nhập
        for v in (self.v_build, self.v_ten, self.v_mota, self.v_file):
            v.set("")
        self._log("Đã thêm: %s  →  %s" % (ten, ten_file), "info")

    def _xoa_dong(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.du_lieu_dong.pop(iid, None)

    # ---- chạy gộp ----
    def _bat_dau_gop(self):
        if self.dang_chay:
            return
        if not self.esptool:
            messagebox.showerror("Thiếu esptool", "Chưa tìm thấy esptool.\nCài bằng lệnh:  pip install esptool")
            return
        if not self.du_lieu_dong:
            messagebox.showwarning("Chưa có chương trình", "Hãy thêm ít nhất một chương trình vào danh sách.")
            return

        thu_muc_dich = self.thu_muc_dich.get().strip()
        if not thu_muc_dich:
            messagebox.showwarning("Thiếu thư mục đích", "Hãy chọn thư mục firmware/ để xuất file.")
            return
        Path(thu_muc_dich).mkdir(parents=True, exist_ok=True)

        boot_app0 = self.boot_app0.get().strip()
        if boot_app0 and not Path(boot_app0).is_file():
            messagebox.showwarning("boot_app0 không hợp lệ", "Đường dẫn boot_app0.bin không tồn tại.")
            return

        # gom công việc theo thứ tự trong bảng
        cong_viec = [self.du_lieu_dong[iid] for iid in self.tree.get_children("")]

        self.dang_chay = True
        self.btn_gop.config(state="disabled")
        self.lbl_trang_thai.config(text="Đang gộp…")
        self._log("─" * 48, "info")

        t = threading.Thread(target=self._chay_gop_nen,
                             args=(cong_viec, thu_muc_dich, boot_app0, self.chip.get()),
                             daemon=True)
        t.start()

    def _chay_gop_nen(self, cong_viec, thu_muc_dich, boot_app0, chip):
        lenh_goi, phien_ban = self.esptool
        off_boot = BOOTLOADER_OFFSET.get(chip, 0x0)
        so_ok = 0

        for cv in cong_viec:
            ten = cv["name"]; ten_file = cv["ten_file"]; files = cv["files"]
            out_path = str(Path(thu_muc_dich) / ten_file)
            self.log_queue.put(("info", "▶ Đang gộp: %s" % ten))

            cac_phan = [(off_boot, files["bootloader"]),
                        (OFFSET_PARTITIONS, files["partitions"])]
            if boot_app0:
                cac_phan.append((OFFSET_BOOT_APP0, boot_app0))
            cac_phan.append((OFFSET_APP, files["app"]))
            cac_phan.sort(key=lambda x: x[0])

            cmd = tao_lenh_merge(lenh_goi, phien_ban, chip, out_path, cac_phan)
            try:
                kq = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except subprocess.SubprocessError as e:
                self.log_queue.put(("err", "  ✗ Lỗi khi chạy esptool: %s" % e))
                continue

            if kq.returncode == 0 and Path(out_path).exists():
                kich_thuoc = Path(out_path).stat().st_size
                self.log_queue.put(("ok", "  ✓ Xong: %s  (%.0f KB)" % (out_path, kich_thuoc / 1024)))
                so_ok += 1
                if self.cap_nhat_config.get():
                    try:
                        config_path = Path(thu_muc_dich).parent / "config.json"
                        ten_fw_dir = Path(thu_muc_dich).name
                        cap_nhat_config_json(config_path, {
                            "id": cv["id"], "name": ten, "description": cv.get("description", ""),
                            "chip": chip.upper().replace("ESP32", "ESP32"),
                            "parts": [{"path": "%s/%s" % (ten_fw_dir, ten_file), "offset": 0}],
                        })
                        self.log_queue.put(("info", "    ↳ đã cập nhật config.json"))
                    except Exception as e:
                        self.log_queue.put(("warn", "    ↳ không cập nhật được config.json: %s" % e))
            else:
                loi = (kq.stderr or kq.stdout or "").strip()
                loi = loi.splitlines()[-1] if loi else "không rõ nguyên nhân"
                self.log_queue.put(("err", "  ✗ Thất bại: %s" % loi))

        self.log_queue.put(("__done__", "Hoàn tất: %d/%d chương trình thành công." % (so_ok, len(cong_viec))))

    def _ket_thuc_gop(self, msg):
        self.dang_chay = False
        self.btn_gop.config(state="normal")
        self.lbl_trang_thai.config(text="")
        tag = "ok" if msg.startswith("Hoàn tất: %d" % len(self.tree.get_children(""))) else "info"
        self._log("─" * 48, "info")
        self._log(msg, "ok")


if __name__ == "__main__":
    App().mainloop()
