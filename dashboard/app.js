/*
  ESP32 Telemetry Dashboard logic.
  - Connects to ws://<host>:8765/ui
  - Receives HEADER + batched rows
  - Renders uPlot chart + last-20 table
*/

const statusEl = document.getElementById("status");
const pauseBtn = document.getElementById("pauseBtn");
const zoomInBtn = document.getElementById("zoomInBtn");
const zoomOutBtn = document.getElementById("zoomOutBtn");
const recordStartBtn = document.getElementById("recordStartBtn");
const recordStopBtn = document.getElementById("recordStopBtn");
const sendCmdBtn = document.getElementById("sendCmdBtn");
const cmdInput = document.getElementById("cmdInput");

const xModeSelect = document.getElementById("xModeSelect");
const visiblePointsInput = document.getElementById("visiblePointsInput");
const yScaleModeSelect = document.getElementById("yScaleModeSelect");
const yMinInput = document.getElementById("yMinInput");
const yMaxInput = document.getElementById("yMaxInput");
const variableToggles = document.getElementById("variableToggles");

const tableHead = document.querySelector("#dataTable thead");
const tableBody = document.querySelector("#dataTable tbody");

let socket = null;
let paused = false;
let frameCount = 0;
let header = [];
let rows = [];
let visibility = {};
let plot = null;
let zoomFactor = 1;

// Distinct colors for up to 15 telemetry variables.
const palette = [
  "#4aa8ff", "#73d13d", "#ffec3d", "#ff7875", "#b37feb",
  "#ffa940", "#36cfc9", "#95de64", "#ffd666", "#ff85c0",
  "#5cdbd3", "#adc6ff", "#d3f261", "#ffd8bf", "#ffadd2"
];

function setStatus(text) {
  statusEl.textContent = `Status: ${text}`;
}

function buildWsUrl() {
  // Dashboard is served on :8000, websocket bridge on :8765 on same host.
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.hostname}:8765/ui`;
}

function connect() {
  socket = new WebSocket(buildWsUrl());

  socket.onopen = () => setStatus("connected");
  socket.onclose = () => {
    setStatus("disconnected, retrying...");
    setTimeout(connect, 1500);
  };
  socket.onerror = () => setStatus("socket error");

  socket.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    if (payload.type === "header") {
      onHeader(payload.header || []);
      return;
    }

    if (payload.type === "batch") {
      onBatch(payload.rows || []);
      return;
    }

    if (payload.type === "recording") {
      setStatus(`recording: ${payload.status}`);
      return;
    }

    if (payload.type === "cmd") {
      setStatus(`cmd: ${payload.status} (${payload.cmd || ""})`);
    }
  };
}

function onHeader(nextHeader) {
  header = nextHeader;
  rows = [];
  frameCount = 0;

  // Initialize all telemetry variables (excluding millis) to visible.
  visibility = {};
  header.slice(1).forEach((name) => {
    visibility[name] = true;
  });

  rebuildVariableToggles();
  rebuildTableHeader();
  rebuildPlot();
}

function onBatch(batchRows) {
  if (!Array.isArray(batchRows) || batchRows.length === 0) {
    return;
  }

  for (const row of batchRows) {
    rows.push(row);
    frameCount += 1;
  }

  const maxRows = Math.max(50, Number(visiblePointsInput.value) * 5 || 1000);
  if (rows.length > maxRows) {
    rows = rows.slice(rows.length - maxRows);
  }

  if (!paused) {
    render();
  }
}

function rebuildVariableToggles() {
  variableToggles.innerHTML = "";

  header.slice(1).forEach((name) => {
    const wrap = document.createElement("label");
    wrap.className = "toggle-item";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = visibility[name] !== false;
    cb.addEventListener("change", () => {
      visibility[name] = cb.checked;
      rebuildPlot();
      render();
    });

    const span = document.createElement("span");
    span.textContent = name;

    wrap.appendChild(cb);
    wrap.appendChild(span);
    variableToggles.appendChild(wrap);
  });
}

function rebuildTableHeader() {
  tableHead.innerHTML = "";
  const tr = document.createElement("tr");
  header.forEach((name) => {
    const th = document.createElement("th");
    th.textContent = name;
    tr.appendChild(th);
  });
  tableHead.appendChild(tr);
}

function updateTable() {
  tableBody.innerHTML = "";

  // Show latest 20 rows in same order/columns as telemetry CSV.
  const latest = rows.slice(-20);
  for (const row of latest) {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    });
    tableBody.appendChild(tr);
  }
}

function createSeries() {
  // First series is x values; hidden stroke because it's the axis domain only.
  const series = [{ label: xModeSelect.value === "millis" ? "millis" : "frame", stroke: "transparent" }];

  let colorIdx = 0;
  header.slice(1).forEach((name) => {
    if (!visibility[name]) return;
    const color = palette[colorIdx % palette.length];
    colorIdx += 1;

    series.push({
      label: name,
      stroke: color,
      width: 2,
      points: { show: true, size: 3, stroke: color, fill: color },
    });
  });

  return series;
}

function chartData() {
  const visiblePoints = Math.max(20, Number(visiblePointsInput.value) || 200);
  const slice = rows.slice(-Math.floor(visiblePoints * zoomFactor));

  const xByMillis = xModeSelect.value === "millis";

  // Build x-axis array as frame index or millis field.
  const xVals = slice.map((row, idx) => {
    if (xByMillis) {
      return Number(row[0]);
    }
    return frameCount - slice.length + idx;
  });

  const data = [xVals];

  header.slice(1).forEach((name, hIdx) => {
    if (!visibility[name]) return;
    data.push(slice.map((row) => Number(row[hIdx + 1])));
  });

  return data;
}

function yScaleConfig() {
  if (yScaleModeSelect.value === "manual") {
    return {
      auto: false,
      range: [Number(yMinInput.value), Number(yMaxInput.value)],
    };
  }
  return { auto: true };
}

function rebuildPlot() {
  if (plot) {
    plot.destroy();
    plot = null;
  }

  if (header.length === 0) {
    return;
  }

  plot = new uPlot(
    {
      width: document.getElementById("plot").clientWidth,
      height: 360,
      series: createSeries(),
      scales: {
        x: { time: false },
        y: yScaleConfig(),
      },
      axes: [
        {
          grid: { show: true, stroke: "rgba(180,180,180,0.2)", width: 1 },
          stroke: "#9fb0d9",
        },
        {
          grid: { show: true, stroke: "rgba(180,180,180,0.2)", width: 1 },
          stroke: "#9fb0d9",
        },
      ],
      cursor: {
        drag: {
          x: true,
          y: false,
          setScale: true, // Enables drag/pan style interactions on x-axis.
        },
      },
      legend: {
        show: true,
      },
    },
    chartData(),
    document.getElementById("plot")
  );
}

function render() {
  updateTable();
  if (!plot) {
    rebuildPlot();
    return;
  }

  // Update scales and data every render cycle (250 ms from server batch cadence).
  plot.setData(chartData());
  plot.setScale("y", yScaleConfig());
}

pauseBtn.addEventListener("click", () => {
  paused = !paused;
  pauseBtn.textContent = paused ? "Resume" : "Pause";
  if (!paused) {
    render();
  }
});

zoomInBtn.addEventListener("click", () => {
  zoomFactor = Math.max(0.5, zoomFactor * 0.8);
  render();
});

zoomOutBtn.addEventListener("click", () => {
  zoomFactor = Math.min(5, zoomFactor * 1.25);
  render();
});

recordStartBtn.addEventListener("click", () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send("REC:start");
  }
});

recordStopBtn.addEventListener("click", () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send("REC:stop");
  }
});

sendCmdBtn.addEventListener("click", () => {
  const cmd = cmdInput.value.trim();
  if (!cmd) return;
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(`CMD:${cmd}`);
    cmdInput.value = "";
  }
});

[xModeSelect, visiblePointsInput, yScaleModeSelect, yMinInput, yMaxInput].forEach((el) => {
  el.addEventListener("change", () => {
    rebuildPlot();
    render();
  });
});

window.addEventListener("resize", () => {
  rebuildPlot();
  render();
});

// Boot sequence.
connect();
