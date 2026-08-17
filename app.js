/* =========================================================
   NẠP CODE ESP32 — app.js
   Nạp firmware (.bin) vào bo ESP32 qua cổng USB bằng Web Serial.
   Dùng thư viện esptool-js đã đóng gói sẵn trong vendor/esptool-bundle.js
   ========================================================= */

(function () {
  "use strict";

  /* ---------- Lấy các phần tử giao diện ---------- */
  const $ = (id) => document.getElementById(id);

  const els = {
    unsupported: $("unsupportedBanner"),
    eyebrow: $("eyebrow"),
    title: $("title"),
    subtitle: $("subtitle"),
    footer: $("footer"),
    status: $("status"),
    statusText: $("statusText"),
    firmwareList: $("firmwareList"),
    cardsLoading: $("cardsLoading"),
    connectBtn: $("connectBtn"),
    reselectBtn: $("reselectBtn"),
    connInfo: $("connInfo"),
    chipInfo: $("chipInfo"),
    baud: $("baud"),
    eraseAll: $("eraseAll"),
    flashBtn: $("flashBtn"),
    progress: $("progress"),
    ledbar: $("ledbar"),
    progressPhase: $("progressPhase"),
    progressPct: $("progressPct"),
    console: $("console"),
    consoleEmpty: $("consoleEmpty"),
    clearLogBtn: $("clearLogBtn"),
  };

  /* ---------- Trạng thái ---------- */
  let config = null;
  let cardEls = [];
  let selected = null;

  let transport = null;
  let esploader = null;
  let grantedPort = null;
  let chipName = "ESP32";

  let connected = false;
  let flashing = false;
  let busyFlag = false;

  const LED_COUNT = 40;
  let leds = [];
  let curLine = null; // dòng console đang ghi dở (cho các dấu chấm "...")

  /* =========================================================
     NHẬT KÝ (console)
     ========================================================= */
  function hideEmpty() {
    if (els.consoleEmpty && els.consoleEmpty.parentNode) {
      els.consoleEmpty.remove();
    }
  }
  function scrollConsole() {
    els.console.scrollTop = els.console.scrollHeight;
  }
  function appendLine(text, type) {
    hideEmpty();
    curLine = null; // thông điệp của mình luôn ở dòng mới
    const el = document.createElement("div");
    el.className = "line line--" + (type || "info");
    el.textContent = text;
    els.console.appendChild(el);
    scrollConsole();
  }
  function ensureCurLine() {
    if (!curLine) {
      hideEmpty();
      curLine = document.createElement("div");
      curLine.className = "line line--sys";
      els.console.appendChild(curLine);
    }
    return curLine;
  }
  // Terminal cho esptool-js: nhận log tiếng Anh của quá trình nạp
  const terminal = {
    clean() { /* không xóa để giữ nhật ký của học sinh */ },
    writeLine(data) {
      const el = ensureCurLine();
      el.textContent += (data || "");
      curLine = null;
      scrollConsole();
    },
    write(data) {
      const el = ensureCurLine();
      el.textContent += (data || "");
      scrollConsole();
    },
  };
  function clearLog() {
    els.console.innerHTML = "";
    curLine = null;
    const empty = document.createElement("div");
    empty.className = "console__empty";
    empty.id = "consoleEmpty";
    empty.textContent = "Nhật ký sẽ hiện ở đây khi bạn kết nối và nạp code.";
    els.console.appendChild(empty);
    els.consoleEmpty = empty;
  }

  /* =========================================================
     TRẠNG THÁI & THANH LED
     ========================================================= */
  function setStatus(state, text) {
    els.status.dataset.state = state;
    els.statusText.textContent = text;
  }
  function setConnectButton(isConnected) {
    const label = els.connectBtn.querySelector(".btn__label");
    if (isConnected) {
      label.textContent = "Ngắt kết nối";
      els.connectBtn.classList.add("is-connected");
    } else {
      label.textContent = "Kết nối bo mạch";
      els.connectBtn.classList.remove("is-connected");
    }
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function buildLeds() {
    els.ledbar.innerHTML = "";
    leds = [];
    for (let i = 0; i < LED_COUNT; i++) {
      const d = document.createElement("span");
      d.className = "led";
      els.ledbar.appendChild(d);
      leds.push(d);
    }
  }
  function paintProgress(pct) {
    pct = clamp(Math.round(pct), 0, 100);
    const lit = Math.round((pct / 100) * LED_COUNT);
    const done = els.progress.classList.contains("is-done");
    for (let i = 0; i < LED_COUNT; i++) {
      const on = i < lit;
      leds[i].classList.toggle("on", on);
      leds[i].classList.toggle("lead", !done && on && i === lit - 1 && pct > 0 && pct < 100);
      if (on) {
        if (done) {
          leds[i].style.setProperty("--led-color", "var(--success)");
          leds[i].style.setProperty("--led-glow", "rgba(53,217,154,.6)");
        } else {
          const hue = Math.round((i / LED_COUNT) * 360);
          leds[i].style.setProperty("--led-color", "hsl(" + hue + " 85% 55%)");
          leds[i].style.setProperty("--led-glow", "hsla(" + hue + " 90% 55% / .55)");
        }
      } else {
        leds[i].style.removeProperty("--led-color");
        leds[i].style.removeProperty("--led-glow");
      }
    }
    els.progressPct.textContent = pct + "%";
    els.progress.setAttribute("aria-valuenow", String(pct));
  }
  function setPhase(text) { els.progressPhase.textContent = text; }
  function resetProgress() {
    els.progress.classList.remove("is-done", "is-error");
    setPhase("Sẵn sàng");
    paintProgress(0);
  }
  function doneProgress() {
    els.progress.classList.remove("is-error");
    els.progress.classList.add("is-done");
    setPhase("Hoàn tất ✓");
    paintProgress(100);
  }
  function errorProgress() {
    els.progress.classList.remove("is-done");
    els.progress.classList.add("is-error");
    setPhase("Lỗi");
  }

  /* =========================================================
     BẬT/TẮT CÁC NÚT
     ========================================================= */
  function updateFlashEnabled() {
    els.flashBtn.disabled = busyFlag || flashing || !connected || !selected;
  }
  function setBusyUI(busy) {
    busyFlag = busy;
    els.connectBtn.disabled = busy;
    els.reselectBtn.disabled = busy;
    els.baud.disabled = busy;
    els.eraseAll.disabled = busy;
    cardEls.forEach((c) => {
      c.setAttribute("aria-disabled", busy ? "true" : "false");
      c.tabIndex = busy ? -1 : 0;
    });
    updateFlashEnabled();
  }

  /* =========================================================
     CẤU HÌNH & THẺ CHƯƠNG TRÌNH
     ========================================================= */
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  async function loadConfig() {
    try {
      const resp = await fetch("config.json", { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      config = await resp.json();
    } catch (e) {
      config = { firmwares: [] };
      appendLine("Không đọc được config.json: " + (e.message || e), "error");
    }
    applyConfigText();
    renderCards();
  }
  function applyConfigText() {
    if (!config) return;
    if (config.eyebrow) els.eyebrow.textContent = config.eyebrow;
    if (config.title) { els.title.textContent = config.title; document.title = config.title; }
    if (config.subtitle) els.subtitle.textContent = config.subtitle;
    if (config.footer) els.footer.textContent = config.footer;
    if (config.defaultBaudrate) {
      const v = String(config.defaultBaudrate);
      if ([...els.baud.options].some((o) => o.value === v)) els.baud.value = v;
    }
  }
  function renderCards() {
    els.firmwareList.innerHTML = "";
    cardEls = [];
    const list = (config && config.firmwares) || [];
    list.forEach((fw, idx) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "card";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", "false");
      b.dataset.id = fw.id || String(idx);
      const glyph = (fw.name || "?").trim().charAt(0).toUpperCase() || "•";
      b.innerHTML =
        '<span class="card__glyph">' + escapeHtml(glyph) + "</span>" +
        '<span class="card__body">' +
          '<span class="card__name">' + escapeHtml(fw.name || "(không tên)") + "</span>" +
          '<span class="card__desc">' + escapeHtml(fw.description || "") + "</span>" +
        "</span>" +
        '<span class="card__badge">' + escapeHtml(fw.chip || "ESP32") + "</span>" +
        '<svg class="card__check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
      b.addEventListener("click", () => {
        if (b.getAttribute("aria-disabled") === "true") return;
        selectCard(fw, b);
      });
      els.firmwareList.appendChild(b);
      cardEls.push(b);
    });
    if (!cardEls.length) {
      els.firmwareList.innerHTML =
        '<p class="cards__loading">Chưa có chương trình nào. Giáo viên hãy thêm vào file config.json.</p>';
    }
  }
  function selectCard(fw, el) {
    selected = fw;
    cardEls.forEach((c) => c.setAttribute("aria-selected", c === el ? "true" : "false"));
    updateFlashEnabled();
  }

  /* =========================================================
     KẾT NỐI
     ========================================================= */
  function getBaud() {
    return Number(els.baud.value) || (config && config.defaultBaudrate) || 115200;
  }
  async function obtainPort(forcePrompt) {
    if (!forcePrompt && grantedPort) return grantedPort;
    const p = await navigator.serial.requestPort();
    grantedPort = p;
    return p;
  }
  async function safeDisconnect() {
    if (transport) {
      try { await transport.disconnect(); } catch (_) {}
    }
    transport = null;
    esploader = null;
  }
  function handleConnectError(e, wasForced) {
    const msg = (e && e.message) ? e.message : String(e);
    appendLine("Không kết nối được với bo mạch.", "error");
    if (/already open|Failed to open|The port is already|access/i.test(msg)) {
      appendLine("Cổng đang bị chương trình khác chiếm (Arduino IDE, Serial Monitor…). Hãy đóng chúng rồi thử lại.", "warn");
    } else if (/No known devices|Timed out|Failed to connect|packet|invalid head|Sync|sync/i.test(msg)) {
      appendLine("Không giao tiếp được với chip. Thử: giữ nút BOOT trên bo khi bấm Kết nối, đổi dây USB (dùng cáp truyền dữ liệu, không phải cáp chỉ sạc), hoặc hạ Tốc độ nạp về 115200.", "warn");
    } else {
      appendLine("Chi tiết: " + msg, "sys");
    }
    if (!wasForced) grantedPort = null; // lần sau cho chọn lại cổng
  }

  async function connect(forcePrompt) {
    if (flashing) return;
    setBusyUI(true);
    setStatus("working", "Đang kết nối…");
    appendLine("Đang kết nối với bo mạch…", "info");
    await safeDisconnect();

    let port;
    try {
      port = await obtainPort(!!forcePrompt);
    } catch (e) {
      appendLine('Bạn chưa chọn cổng. Cắm bo mạch rồi bấm "Kết nối bo mạch".', "warn");
      setStatus("idle", "Chưa kết nối");
      setBusyUI(false);
      return;
    }

    try {
      const baud = getBaud();
      transport = new window.esptoolPackage.Transport(port, false);
      esploader = new window.esptoolPackage.ESPLoader({
        transport: transport,
        baudrate: baud,
        terminal: terminal,
        debugLogging: false,
      });
      const desc = await esploader.main(); // kết nối + vào bootloader + nạp stub
      chipName = (esploader.chip && esploader.chip.CHIP_NAME) ? esploader.chip.CHIP_NAME : "ESP32";

      connected = true;
      els.chipInfo.textContent = "Đã nhận: " + (desc || chipName);
      els.connInfo.hidden = false;
      setStatus("connected", "Đã kết nối · " + chipName);
      appendLine("Kết nối thành công: " + (desc || chipName), "success");
      setConnectButton(true);
    } catch (e) {
      connected = false;
      await safeDisconnect();
      handleConnectError(e, !!forcePrompt);
      setStatus("error", "Kết nối thất bại");
      setConnectButton(false);
      els.connInfo.hidden = true;
    } finally {
      setBusyUI(false);
      updateFlashEnabled();
    }
  }

  async function disconnect() {
    if (flashing) return;
    await safeDisconnect();
    connected = false;
    els.connInfo.hidden = true;
    setStatus("idle", "Chưa kết nối");
    appendLine("Đã ngắt kết nối.", "sys");
    setConnectButton(false);
    updateFlashEnabled();
  }

  /* =========================================================
     NẠP FIRMWARE
     ========================================================= */
  async function postFlashReset() {
    // Sau khi nạp, bo đã khởi động lại và rời chế độ nạp.
    // Ngắt kết nối để lần nạp sau bắt đầu sạch sẽ.
    await safeDisconnect();
    connected = false;
    els.connInfo.hidden = true;
    setConnectButton(false);
    updateFlashEnabled();
    appendLine('Muốn nạp lại hoặc nạp chương trình khác? Bấm "Kết nối bo mạch" rồi "Nạp code".', "sys");
  }
  function handleFlashError(e) {
    const msg = (e && e.message) ? e.message : String(e);
    appendLine("Nạp thất bại.", "error");
    if (/Timed out|packet|invalid|Failed to|MD5|md5|compress|Sync|sync/i.test(msg)) {
      appendLine("Đường truyền bị lỗi giữa chừng. Thử: hạ Tốc độ nạp về 115200, đổi dây USB, hoặc rút ra cắm lại rồi nạp lại.", "warn");
    } else {
      appendLine("Chi tiết: " + msg, "sys");
    }
  }

  async function flash() {
    if (!connected || !esploader) {
      appendLine("Bạn cần kết nối bo mạch trước (Bước 02).", "warn");
      return;
    }
    if (!selected) {
      appendLine("Bạn chưa chọn chương trình để nạp (Bước 01).", "warn");
      return;
    }
    if (flashing) return;

    flashing = true;
    setBusyUI(true);
    setStatus("working", "Đang nạp…");
    resetProgress();

    // --- Giai đoạn 1: tải file .bin (nếu lỗi thì KHÔNG ngắt kết nối) ---
    let fileArray, sizes, prefix, totalAll;
    try {
      appendLine("Đang tải file chương trình: " + selected.name, "info");
      fileArray = [];
      sizes = [];
      const parts = selected.parts || [];
      if (!parts.length) throw new Error('Chương trình "' + selected.name + '" chưa khai báo file (parts) trong config.json.');
      for (const part of parts) {
        const resp = await fetch(part.path, { cache: "no-store" });
        if (!resp.ok) {
          throw new Error("Không tải được " + part.path + " (HTTP " + resp.status + "). Giáo viên đã tải file này lên chưa?");
        }
        const data = new Uint8Array(await resp.arrayBuffer());
        if (data.length === 0) throw new Error("File " + part.path + " bị rỗng.");
        fileArray.push({ data: data, address: part.offset || 0 });
        sizes.push(data.length);
        appendLine("• " + part.path + " — " + formatBytes(data.length) + " @ 0x" + (part.offset || 0).toString(16), "sys");
      }
      totalAll = sizes.reduce((a, b) => a + b, 0);
      prefix = []; let acc = 0;
      for (const s of sizes) { prefix.push(acc); acc += s; }
    } catch (e) {
      errorProgress();
      handleFlashError(e);
      setStatus("connected", "Đã kết nối · " + chipName);
      flashing = false;
      setBusyUI(false);
      updateFlashEnabled();
      return; // giữ nguyên kết nối, không cần nối lại
    }

    // --- Giai đoạn 2: ghi vào bo (nếu lỗi thì bo có thể cần nối lại) ---
    try {
      const erase = els.eraseAll.checked;
      setPhase(erase ? "Đang xóa flash…" : "Đang ghi vào bo…");
      if (erase) appendLine("Đang xóa toàn bộ flash (có thể mất khoảng 10 giây)…", "info");

      await esploader.writeFlash({
        fileArray: fileArray,
        flashMode: selected.flashMode || "keep",
        flashFreq: selected.flashFreq || "keep",
        flashSize: selected.flashSize || "keep",
        eraseAll: erase,
        compress: true,
        reportProgress: (fileIndex, written, total) => {
          const part = prefix[fileIndex] + (total ? (written / total) * sizes[fileIndex] : 0);
          const pct = (part / totalAll) * 100;
          if ((selected.parts || []).length > 1) {
            setPhase("Đang ghi phần " + (fileIndex + 1) + "/" + selected.parts.length + "…");
          } else {
            setPhase("Đang ghi vào bo…");
          }
          paintProgress(pct);
        },
      });

      setPhase("Đang khởi động lại bo…");
      appendLine("Ghi xong. Đang khởi động lại bo mạch…", "info");
      try { await esploader.after("hard_reset"); } catch (_) {}

      doneProgress();
      setStatus("connected", "Nạp xong ✓");
      appendLine('HOÀN TẤT! Chương trình "' + selected.name + '" đã được nạp. Bo mạch đang chạy chương trình mới.', "success");
      await postFlashReset();
    } catch (e) {
      errorProgress();
      setStatus("error", "Nạp thất bại");
      handleFlashError(e);
      await postFlashReset();
    } finally {
      flashing = false;
      setBusyUI(false);
      updateFlashEnabled();
    }
  }

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  }

  /* =========================================================
     KHỞI ĐỘNG
     ========================================================= */
  function wireEvents() {
    els.connectBtn.addEventListener("click", () => {
      if (connected) disconnect(); else connect(false);
    });
    els.reselectBtn.addEventListener("click", () => connect(true));
    els.flashBtn.addEventListener("click", () => flash());
    els.clearLogBtn.addEventListener("click", () => clearLog());
  }

  function init() {
    buildLeds();
    paintProgress(0);
    wireEvents();

    const hasSerial = !!(navigator && navigator.serial && typeof navigator.serial.requestPort === "function");
    const hasLib = !!(window.esptoolPackage && window.esptoolPackage.ESPLoader);

    if (!hasSerial || !hasLib) {
      els.unsupported.hidden = false;
      els.connectBtn.disabled = true;
      els.flashBtn.disabled = true;
      els.baud.disabled = true;
      els.eraseAll.disabled = true;
      if (hasSerial && !hasLib) {
        appendLine("Không tải được thư viện nạp (vendor/esptool-bundle.js). Hãy kiểm tra file có tồn tại trên GitHub không.", "error");
      }
      // vẫn tải danh sách để xem trước
      loadConfig();
      return;
    }

    loadConfig();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
