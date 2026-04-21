/* =========================================================
   Dashboard JS untuk:
   - koneksi websocket endpoint /ui
   - render realtime uPlot (line + point markers)
   - pan X-axis only
   - sliding window (range X bergerak otomatis)
   - mode X-axis: frame index / time (seconds)
   - setting jumlah visible points dan jumlah label X
   ========================================================= */

// ---------------------------
// Konstanta dan state global
// ---------------------------
const WS_PORT = 8765;
const MAX_BUFFER_POINTS = 5000; // buffer lokal maksimal
const TABLE_ROWS = 20;          // data table 20 frame terakhir

let ws = null;
let reconnectDelayMs = 1000;

// Header telemetry sesi aktif, contoh: ["micros","ax","ay"]
let headerCols = [];

// Buffer data mentah, setiap elemen: { idx, micros, values[] }
let frames = [];
let frameCounter = 0; // counter frame global (tidak reset setiap trimming)
let microsBase = null;

// State tampilan
let paused = false;
let xMode = "index";      // "index" atau "time"
let yScaleMode = "auto";  // "auto" atau "manual"
let visibleSeries = [];
let zoomFactor = 1.0;

// Setting baru untuk X-axis
let visiblePoints = 500; // jumlah titik yang di-lock untuk tampilan
let xLabelCount = 8;     // jumlah label X-axis yang diinginkan

// Referensi instance uPlot
let plot = null;

// --------------------------------
// Ambil elemen UI yang dibutuhkan
// --------------------------------
const elEspStatus = document.getElementById("espStatus");
const elRecStatus = document.getElementById("recStatus");
const elVarToggles = document.getElementById("varToggles");
const elTableHead = document.querySelector("#dataTable thead");
const elTableBody = document.querySelector("#dataTable tbody");
const elChart = document.getElementById("chart");

const btnPause = document.getElementById("btnPause");
const btnResume = document.getElementById("btnResume");
const btnZoomIn = document.getElementById("btnZoomIn");
const btnZoomOut = document.getElementById("btnZoomOut");
const btnRecStart = document.getElementById("btnRecStart");
const btnRecStop = document.getElementById("btnRecStop");

const yMinInput = document.getElementById("yMin");
const yMaxInput = document.getElementById("yMax");
const visiblePointsInput = document.getElementById("visiblePoints");
const xLabelCountInput = document.getElementById("xLabelCount");

// ------------------------------
// Utilitas: pilih warna tiap line
// ------------------------------
function colorFor(i) {
  const palette = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be123c", "#4f46e5"];
  return palette[i % palette.length];
}

// ---------------------------------------------------------------------
// Ambil x-axis value sesuai mode:
// - index: nomor frame global
// - time: micros -> seconds relatif dari frame pertama sesi
// ---------------------------------------------------------------------
function getXValue(frame) {
  if (xMode === "time") {
    if (microsBase === null) return 0;
    return (frame.micros - microsBase) / 1_000_000.0;
  }
  return frame.idx;
}

// -----------------------------------------------------------
// Hitung split label X-axis agar jumlah label mendekati target
// -----------------------------------------------------------
function buildCustomXSplits(u, axisIdx, scaleMin, scaleMax) {
  if (!Number.isFinite(scaleMin) || !Number.isFinite(scaleMax) || scaleMax <= scaleMin) {
    return [];
  }

  const count = Math.max(2, Math.min(30, xLabelCount));
  const step = (scaleMax - scaleMin) / (count - 1);
  const splits = [];

  for (let i = 0; i < count; i++) {
    splits.push(scaleMin + step * i);
  }

  return splits;
}

// ------------------------------------------------------
// Setup header: reset data, rebuild checkbox dan tabel
// ------------------------------------------------------
function applyHeader(newHeaderCols) {
  headerCols = newHeaderCols;
  frames = [];
  frameCounter = 0;
  microsBase = null;
  zoomFactor = 1.0;

  // Buat status visibility default: semua variabel (kecuali micros) visible
  visibleSeries = headerCols.slice(1).map(() => true);

  // Rebuild toggles variabel
  elVarToggles.innerHTML = "";
  headerCols.slice(1).forEach((name, idx) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.addEventListener("change", () => {
      visibleSeries[idx] = cb.checked;
      rebuildPlot();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(` ${name}`));
    elVarToggles.appendChild(label);
  });

  // Rebuild header tabel
  const tr = document.createElement("tr");
  headerCols.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    tr.appendChild(th);
  });
  elTableHead.innerHTML = "";
  elTableHead.appendChild(tr);

  rebuildPlot();
  renderTable();
}

// -------------------------------------
// Parse 1 frame CSV menjadi object data
// -------------------------------------
function parseDataLine(line) {
  const cells = line.split(",").map((x) => x.trim());
  if (!headerCols.length || cells.length !== headerCols.length) return null;

  const micros = Number(cells[0]);
  if (!Number.isFinite(micros)) return null;

  const values = [];
  for (let i = 1; i < cells.length; i++) {
    const v = Number(cells[i]);
    values.push(Number.isFinite(v) ? v : NaN);
  }

  if (microsBase === null) {
    microsBase = micros;
  }

  return { idx: frameCounter++, micros, values };
}

// ---------------------------------
// Tambah batch data dari server
// ---------------------------------
function pushBatch(lines) {
  for (const line of lines) {
    const frame = parseDataLine(line);
    if (!frame) continue;
    frames.push(frame);
  }

  // Buffer internal dibatasi agar penggunaan memori tetap stabil
  if (frames.length > MAX_BUFFER_POINTS) {
    frames = frames.slice(frames.length - MAX_BUFFER_POINTS);
  }

  // Saat pause, data tetap di-buffer tapi render tidak diupdate
  if (!paused) {
    renderPlot();
    renderTable();
  }
}

// -------------------------------------------------
// Hitung data array untuk uPlot berdasarkan state
// -------------------------------------------------
function buildUplotData() {
  if (!headerCols.length) return [[], []];

  const xData = frames.map((f) => getXValue(f));
  const seriesData = headerCols.slice(1).map((_, varIdx) => frames.map((f) => f.values[varIdx]));

  return [xData, ...seriesData];
}

// --------------------------------------------------------
// Buat ulang objek uPlot (dipakai saat header/series berubah)
// --------------------------------------------------------
function rebuildPlot() {
  if (plot) {
    plot.destroy();
    plot = null;
  }

  if (!headerCols.length) return;

  const series = [
    { label: xMode === "time" ? "Time (s)" : "Frame", stroke: "#000" },
  ];

  headerCols.slice(1).forEach((name, idx) => {
    series.push({
      label: name,
      stroke: colorFor(idx),
      width: 2,
      show: visibleSeries[idx],
      points: {
        show: true,
        size: 4,
      },
    });
  });

  const opts = {
    width: Math.min(1200, window.innerWidth - 64),
    height: 380,
    series,
    scales: {
      x: { time: false },
      y: { auto: yScaleMode === "auto" },
    },
    cursor: {
      drag: {
        x: true,
        y: false,
      },
    },
    axes: [
      {
        label: xMode === "time" ? "Time (seconds)" : "Frame index",
        // pakai custom split agar jumlah label bisa dikonfigurasi
        splits: buildCustomXSplits,
      },
      { label: "Value" },
    ],
  };

  plot = new uPlot(opts, buildUplotData(), elChart);
  renderPlot();
}

// ---------------------------------------------------
// Atur range X sliding window yang selalu maju ke kanan
// ---------------------------------------------------
function applySlidingWindowXRange(data) {
  if (!plot) return;

  const xData = data[0];
  if (xData.length === 0) return;

  const right = xData[xData.length - 1];

  // Titik kiri mengikuti visiblePoints agar window "terkunci"
  const leftIdx = Math.max(0, xData.length - visiblePoints);
  const left = xData[leftIdx];

  if (!Number.isFinite(left) || !Number.isFinite(right)) return;

  // Tambahkan zoom factor (default 1 = pas visible points)
  const center = (left + right) / 2;
  const half = ((right - left) / 2) / zoomFactor;

  plot.setScale("x", { min: center - half, max: center + half });
}

// --------------------------------------------
// Render data terbaru ke plot yang sudah dibuat
// --------------------------------------------
function renderPlot() {
  if (!plot || !headerCols.length) return;

  const data = buildUplotData();
  plot.setData(data);

  // Atur Y manual jika mode manual dipilih
  if (yScaleMode === "manual") {
    const yMin = Number(yMinInput.value);
    const yMax = Number(yMaxInput.value);
    if (Number.isFinite(yMin) && Number.isFinite(yMax) && yMin < yMax) {
      plot.setScale("y", { min: yMin, max: yMax });
    }
  }

  // Pastikan window X selalu sliding saat data baru datang
  applySlidingWindowXRange(data);
}

// -------------------------
// Render tabel 20 data akhir
// -------------------------
function renderTable() {
  if (!headerCols.length) return;

  const rows = frames.slice(Math.max(0, frames.length - TABLE_ROWS)).reverse();
  elTableBody.innerHTML = "";

  for (const f of rows) {
    const tr = document.createElement("tr");

    const tdMicros = document.createElement("td");
    tdMicros.textContent = String(f.micros);
    tr.appendChild(tdMicros);

    for (const v of f.values) {
      const td = document.createElement("td");
      td.textContent = Number.isFinite(v) ? v.toFixed(6) : "NaN";
      tr.appendChild(td);
    }

    elTableBody.appendChild(tr);
  }
}

// --------------------------------------
// Handler pesan websocket dari server
// --------------------------------------
function handleWsMessage(text) {
  if (text.startsWith("STATUS:")) {
    if (text === "STATUS:esp_connected") elEspStatus.textContent = "ESP: connected";
    if (text === "STATUS:esp_disconnected") elEspStatus.textContent = "ESP: disconnected";
    if (text.startsWith("STATUS:recording_started")) elRecStatus.textContent = text.replace("STATUS:", "");
    if (text.startsWith("STATUS:recording_stopped")) elRecStatus.textContent = text.replace("STATUS:", "");
    return;
  }

  if (text.startsWith("HEADER:")) {
    const cols = text.slice("HEADER:".length).split(",").map((x) => x.trim()).filter(Boolean);
    if (cols.length >= 1 && cols[0] === "micros") {
      applyHeader(cols);
    }
    return;
  }

  if (text.startsWith("BATCH:\n")) {
    const payload = text.slice("BATCH:\n".length);
    const lines = payload.split("\n").map((l) => l.trim()).filter(Boolean);
    pushBatch(lines);
  }
}

// ---------------------------
// Koneksi websocket dashboard
// ---------------------------
function connectWs() {
  const host = window.location.hostname;
  const url = `ws://${host}:${WS_PORT}/ui`;

  ws = new WebSocket(url);

  ws.onopen = () => {
    reconnectDelayMs = 1000;
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      handleWsMessage(ev.data);
    }
  };

  ws.onclose = () => {
    elEspStatus.textContent = "ESP: disconnected";
    setTimeout(connectWs, reconnectDelayMs);
    reconnectDelayMs = Math.min(30_000, reconnectDelayMs * 2);
  };
}

// -------------------------------
// Wiring event listener kontrol UI
// -------------------------------
btnPause.addEventListener("click", () => {
  paused = true;
  btnPause.disabled = true;
  btnResume.disabled = false;
});

btnResume.addEventListener("click", () => {
  paused = false;
  btnPause.disabled = false;
  btnResume.disabled = true;
  renderPlot();
  renderTable();
});

btnZoomIn.addEventListener("click", () => {
  zoomFactor = Math.min(20.0, zoomFactor * 1.25);
  renderPlot();
});

btnZoomOut.addEventListener("click", () => {
  zoomFactor = Math.max(1.0, zoomFactor / 1.25);
  renderPlot();
});

btnRecStart.addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send("REC:start");
  }
});

btnRecStop.addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send("REC:stop");
  }
});

document.querySelectorAll("input[name='xmode']").forEach((el) => {
  el.addEventListener("change", (ev) => {
    xMode = ev.target.value;
    rebuildPlot();
  });
});

document.querySelectorAll("input[name='yscale']").forEach((el) => {
  el.addEventListener("change", (ev) => {
    yScaleMode = ev.target.value;
    rebuildPlot();
  });
});

visiblePointsInput.addEventListener("input", () => {
  const v = Number(visiblePointsInput.value);
  if (Number.isFinite(v)) {
    visiblePoints = Math.max(10, Math.min(5000, Math.floor(v)));
    renderPlot();
  }
});

xLabelCountInput.addEventListener("input", () => {
  const v = Number(xLabelCountInput.value);
  if (Number.isFinite(v)) {
    xLabelCount = Math.max(2, Math.min(30, Math.floor(v)));
    // perlu rebuild agar axis split config diterapkan ulang konsisten
    rebuildPlot();
  }
});

yMinInput.addEventListener("input", () => {
  if (yScaleMode === "manual") renderPlot();
});

yMaxInput.addEventListener("input", () => {
  if (yScaleMode === "manual") renderPlot();
});

window.addEventListener("resize", () => rebuildPlot());

connectWs();
