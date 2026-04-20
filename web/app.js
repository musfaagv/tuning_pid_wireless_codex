const MAX_POINTS = 500;
const RENDER_EVERY = 10;
const LAST_ROWS = 20;

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const frameCountEl = document.getElementById("frameCount");
const logEl = document.getElementById("log");
const cmdInput = document.getElementById("cmdInput");
const sendCmdBtn = document.getElementById("sendCmdBtn");
const csvStartBtn = document.getElementById("csvStartBtn");
const csvStopBtn = document.getElementById("csvStopBtn");
const tableHead = document.querySelector("#dataTable thead");
const tableBody = document.querySelector("#dataTable tbody");

let ws;
let columns = [];
let frame = 0;
let plot;
let seriesData = [];
let lastRows = [];

function log(msg) {
  const ts = new Date().toLocaleTimeString();
  logEl.textContent = `[${ts}] ${msg}\n` + logEl.textContent;
}

function setConnected(ok) {
  statusDot.classList.toggle("online", ok);
  statusDot.classList.toggle("offline", !ok);
  statusText.textContent = ok ? "Connected" : "Disconnected";
}

function resetPlotData() {
  seriesData = Array.from({ length: columns.length }, () => []);
  if (plot) {
    plot.destroy();
    plot = null;
  }
  if (columns.length >= 2) {
    const opts = {
      width: Math.min(window.innerWidth - 60, 1120),
      height: 340,
      series: columns.map((name, idx) => ({ label: idx === 0 ? "x" : name })),
      scales: { x: { time: false } },
    };
    plot = new uPlot(opts, seriesData, document.getElementById("plot"));
  }
}

function renderTable() {
  tableBody.innerHTML = "";
  for (const row of lastRows) {
    const tr = document.createElement("tr");
    row.forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });
    tableBody.appendChild(tr);
  }
}

function applyHeader(newCols) {
  columns = newCols;
  tableHead.innerHTML = "";
  const tr = document.createElement("tr");
  columns.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    tr.appendChild(th);
  });
  tableHead.appendChild(tr);
  lastRows = [];
  resetPlotData();
}

function addTelemetry(values) {
  if (!columns.length || values.length !== columns.length) return;
  values.forEach((v, i) => {
    const n = Number(v);
    seriesData[i].push(Number.isFinite(n) ? n : null);
    if (seriesData[i].length > MAX_POINTS) seriesData[i].shift();
  });

  lastRows.unshift(values);
  if (lastRows.length > LAST_ROWS) lastRows.pop();

  frame += 1;
  frameCountEl.textContent = `Frame: ${frame}`;
  if (plot && frame % RENDER_EVERY === 0) {
    plot.setData(seriesData);
    renderTable();
  }
}

function send(payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify(payload));
}

function connect() {
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${wsProto}://${location.host}/ui`);

  ws.onopen = () => {
    setConnected(true);
    log("UI websocket connected");
  };

  ws.onclose = () => {
    setConnected(false);
    log("UI websocket disconnected, retry in 1s");
    setTimeout(connect, 1000);
  };

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === "status") {
      setConnected(Boolean(msg.espConnected));
      frameCountEl.textContent = `Frame: ${msg.frameCount ?? frame}`;
      log(`Status: espConnected=${msg.espConnected}, csv=${msg.csvEnabled}`);
    } else if (msg.type === "header") {
      applyHeader(msg.columns);
      log(`HEADER diterima (${msg.columns.length} variabel)`);
    } else if (msg.type === "telemetry") {
      addTelemetry(msg.values);
    } else if (msg.type === "notice") {
      log(msg.message);
    }
  };
}

sendCmdBtn.addEventListener("click", () => {
  const value = cmdInput.value.trim();
  if (!value) return;
  send({ type: "command", value });
  log(`Command sent: ${value}`);
  cmdInput.value = "";
});

cmdInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    sendCmdBtn.click();
  }
});

csvStartBtn.addEventListener("click", () => send({ type: "csv", action: "start" }));
csvStopBtn.addEventListener("click", () => send({ type: "csv", action: "stop" }));

connect();
