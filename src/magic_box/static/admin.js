const scanButton = document.querySelector("#scan-button");
const scanStatus = document.querySelector("#scan-status");
const uidInput = document.querySelector("#uid-input");
const nameInput = document.querySelector("[name='name']");
const teachFlow = document.querySelector("[data-teach-flow]");
const modePanel = document.querySelector("[data-mode-panel]");
const modeMessage = document.querySelector("[data-mode-message]");
const lastTagCard = document.querySelector("[data-last-tag-card]");
const lastTagUid = document.querySelector("[data-last-tag-uid]");
const lastTagMeta = document.querySelector("[data-last-tag-meta]");
const useLastTagButton = document.querySelector("[data-use-last-tag]");
const eventList = document.querySelector("[data-event-list]");
const testBoxButton = document.querySelector("[data-test-box]");
const diagnosticResults = document.querySelector("[data-diagnostic-results]");
let currentLastTagUid = lastTagUid?.textContent?.trim() || "";

if (modePanel) {
  const modeButtons = modePanel.querySelectorAll("[data-mode-action]");

  modeButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.modeAction;
      if (!action) {
        return;
      }
      const originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = action === "setup" ? "Switching..." : "Starting...";
      if (modeMessage) {
        modeMessage.textContent =
          action === "setup"
            ? "Switching to setup mode so browser scanning can use the NFC reader..."
            : "Switching to playback mode so the box can listen for character tags...";
      }

      try {
        const response = await fetch(`/api/mode/${encodeURIComponent(action)}`, { method: "POST" });
        const payload = await response.json();
        updateModePanel(payload);
        if (modeMessage) {
          modeMessage.textContent = payload.message || (response.ok ? "Mode updated." : "Mode switch failed.");
        }
        if (scanStatus) {
          scanStatus.textContent =
            payload.mode === "setup"
              ? "Setup mode is active. Ready to scan from the browser."
              : "Playback mode is active. Use Setup scan at the top before scanning a tag.";
        }
      } catch (error) {
        if (modeMessage) {
          modeMessage.textContent = `Mode switch failed: ${error}`;
        }
      } finally {
        button.disabled = false;
        button.textContent = originalLabel;
        refreshModeStatus();
      }
    });
  });

  refreshModeStatus();

  async function refreshModeStatus() {
    try {
      const response = await fetch("/api/mode/status");
      const payload = await response.json();
      updateModePanel(payload);
    } catch (_error) {
      return;
    }
  }

  function updateModePanel(payload) {
    if (!payload) {
      return;
    }
    if (modeMessage && payload.message) {
      modeMessage.textContent = payload.message;
    }
    modeButtons.forEach((button) => {
      button.disabled = payload.available === false;
      const isActive = button.dataset.modeAction === payload.mode;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }
}

if (scanButton && scanStatus && uidInput) {
  scanButton.addEventListener("click", async () => {
    const originalLabel = scanButton.textContent;
    scanButton.disabled = true;
    scanButton.textContent = "Scanning...";
    scanStatus.textContent = "Scanning... hold a tag on the reader.";
    try {
      const response = await fetch("/api/scan?timeout=20");
      const payload = await response.json();
      if (payload.ok) {
        uidInput.value = payload.uid;
        scanStatus.textContent = `Found ${payload.uid}.`;
        updateLastTag(payload.last_tag);
        setTeachStep("name");
        nameInput?.focus();
      } else {
        scanStatus.textContent = payload.message || "No tag found.";
        if (payload.mode_status && modePanel) {
          modePanel.querySelectorAll("[data-mode-action]").forEach((button) => {
            const isActive = button.dataset.modeAction === payload.mode_status.mode;
            button.classList.toggle("is-active", isActive);
            button.setAttribute("aria-pressed", isActive ? "true" : "false");
          });
          if (modeMessage) {
            modeMessage.textContent = payload.mode_status.message || scanStatus.textContent;
          }
        }
      }
    } catch (error) {
      scanStatus.textContent = `Scan failed: ${error}`;
    } finally {
      scanButton.disabled = false;
      scanButton.textContent = originalLabel;
    }
  });
}

useLastTagButton?.addEventListener("click", () => {
  if (!currentLastTagUid || !uidInput) {
    return;
  }
  uidInput.value = currentLastTagUid;
  setTeachStep("name");
  nameInput?.focus();
});

testBoxButton?.addEventListener("click", async () => {
  const originalLabel = testBoxButton.textContent;
  testBoxButton.disabled = true;
  testBoxButton.textContent = "Testing...";
  renderDiagnosticResults([{ label: "Box test", state: "warn", message: "Playing a chime and checking the box..." }]);
  try {
    const response = await fetch("/api/diagnostics", { method: "POST" });
    const payload = await response.json();
    renderDiagnosticResults(payload.checks || []);
    updateLastTag(payload.last_tag);
    renderEvents(payload.events || []);
  } catch (error) {
    renderDiagnosticResults([{ label: "Box test", state: "bad", message: `Test failed: ${error}` }]);
  } finally {
    testBoxButton.disabled = false;
    testBoxButton.textContent = originalLabel;
  }
});

document.querySelectorAll("[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const message = form.dataset.confirm || "Are you sure?";
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });
});

refreshDeviceState();
window.setInterval(refreshDeviceState, 5000);

async function refreshDeviceState() {
  if (!lastTagCard && !eventList) {
    return;
  }
  try {
    const response = await fetch("/api/device-state");
    const payload = await response.json();
    updateLastTag(payload.last_tag);
    renderEvents(payload.events || []);
  } catch (_error) {
    return;
  }
}

function updateLastTag(lastTag) {
  if (!lastTagCard || !lastTagUid || !lastTagMeta) {
    return;
  }
  if (!lastTag || !lastTag.available) {
    currentLastTagUid = "";
    lastTagCard.classList.add("is-empty");
    lastTagCard.classList.remove("is-known", "is-new");
    lastTagUid.textContent = "No tag seen yet";
    lastTagMeta.textContent = "Place a sticker on the reader or use Scan.";
    if (useLastTagButton) {
      useLastTagButton.hidden = true;
    }
    return;
  }

  currentLastTagUid = lastTag.uid || "";
  lastTagCard.classList.remove("is-empty");
  lastTagCard.classList.toggle("is-known", lastTag.known === true);
  lastTagCard.classList.toggle("is-new", lastTag.known !== true);
  lastTagUid.textContent = currentLastTagUid;
  lastTagMeta.textContent = `${lastTag.status_label || "Tag"} · ${lastTag.seen_label || "just now"} · ${lastTag.source || "reader"}`;
  if (useLastTagButton) {
    useLastTagButton.hidden = lastTag.can_add !== true;
  }
}

function renderEvents(events) {
  if (!eventList) {
    return;
  }
  if (!events.length) {
    eventList.innerHTML = '<li class="empty-event">No events yet.</li>';
    return;
  }
  eventList.innerHTML = events
    .map(
      (event) => `
        <li class="event-${escapeHtml(event.level || "info")}">
          <span>${escapeHtml(event.created_label || "")}</span>
          <strong>${escapeHtml(event.message || "")}</strong>
        </li>
      `,
    )
    .join("");
}

function renderDiagnosticResults(checks) {
  if (!diagnosticResults) {
    return;
  }
  diagnosticResults.hidden = false;
  diagnosticResults.innerHTML = checks
    .map(
      (check) => `
        <div class="diagnostic-check diagnostic-${escapeHtml(check.state || "warn")}">
          <strong>${escapeHtml(check.label || "Check")}</strong>
          <span>${escapeHtml(check.message || "")}</span>
        </div>
      `,
    )
    .join("");
}

nameInput?.addEventListener("input", () => {
  if (nameInput.value.trim()) {
    setTeachStep("sound");
  }
});

function setTeachStep(activeStep) {
  if (!teachFlow) {
    return;
  }
  const order = ["scan", "name", "sound"];
  const activeIndex = order.indexOf(activeStep);
  teachFlow.querySelectorAll("[data-teach-step]").forEach((item) => {
    const stepIndex = order.indexOf(item.dataset.teachStep);
    item.classList.toggle("is-active", stepIndex === activeIndex);
    item.classList.toggle("is-complete", activeIndex > stepIndex);
  });
}

document.querySelectorAll(".upload-form").forEach((form) => {
  const input = form.querySelector("[data-upload-input]");
  const trigger = form.querySelector("[data-upload-trigger]");
  const status = form.querySelector("[data-upload-status]");
  const message = form.querySelector("[data-upload-message]");
  const progress = form.querySelector("[data-upload-progress]");

  if (!input || !trigger || !status || !message || !progress) {
    return;
  }

  trigger.addEventListener("click", () => {
    if (form.dataset.uploading === "true") {
      return;
    }
    input.click();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.files.length > 0) {
      uploadAudio(form, input, trigger, status, message, progress);
    } else {
      input.click();
    }
  });

  input.addEventListener("change", () => {
    if (input.files.length > 0) {
      uploadAudio(form, input, trigger, status, message, progress);
    }
  });
});

function uploadAudio(form, input, trigger, status, message, progress) {
  if (form.dataset.uploading === "true") {
    return;
  }

  const fileCount = input.files.length;
  const formData = new FormData(form);
  form.dataset.uploading = "true";
  input.disabled = true;
  trigger.setAttribute("aria-disabled", "true");
  status.hidden = false;
  progress.max = 100;
  progress.value = 0;
  message.textContent = `Uploading ${fileCount} file${fileCount === 1 ? "" : "s"}...`;

  const request = new XMLHttpRequest();
  request.open("POST", form.action);
  request.setRequestHeader("X-Requested-With", "XMLHttpRequest");

  request.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) {
      progress.removeAttribute("value");
      message.textContent = "Uploading...";
      return;
    }
    const percent = Math.round((event.loaded / event.total) * 100);
    progress.value = percent;
    message.textContent = percent >= 100 ? "Processing audio..." : `Uploading... ${percent}%`;
  });

  request.addEventListener("load", () => {
    const payload = parseJson(request.responseText);
    if (request.status >= 200 && request.status < 300) {
      progress.value = 100;
      message.textContent = "Upload complete. Refreshing...";
      const redirect = payload?.redirect || request.responseURL || window.location.href;
      window.location.assign(redirect);
      window.setTimeout(() => window.location.reload(), 150);
      return;
    }
    resetUploadForm(form, input, trigger);
    message.textContent = payload?.message || "Upload failed.";
  });

  request.addEventListener("error", () => {
    resetUploadForm(form, input, trigger);
    message.textContent = "Upload failed. Check the connection and try again.";
  });

  request.addEventListener("abort", () => {
    resetUploadForm(form, input, trigger);
    message.textContent = "Upload canceled.";
  });

  request.send(formData);
}

function resetUploadForm(form, input, trigger) {
  delete form.dataset.uploading;
  input.disabled = false;
  input.value = "";
  trigger.removeAttribute("aria-disabled");
}

function parseJson(value) {
  try {
    return JSON.parse(value);
  } catch (_error) {
    return null;
  }
}

const recorders = new Map();
const browserRecordingAvailable =
  window.isSecureContext === true &&
  Boolean(navigator.mediaDevices?.getUserMedia) &&
  Boolean(window.MediaRecorder);

document.querySelectorAll("[data-record-panel]").forEach((panel) => {
  const uid = panel.dataset.uid;
  const button = panel.querySelector("[data-record-button]");
  const status = panel.querySelector("[data-record-status]");
  const titleInput = panel.querySelector("[data-record-title]");

  if (!uid || !button || !status || !titleInput) {
    return;
  }

  if (!browserRecordingAvailable) {
    panel.remove();
    return;
  }

  panel.hidden = false;

  button.addEventListener("click", async () => {
    const active = recorders.get(uid);
    if (active) {
      active.recorder.stop();
      button.disabled = true;
      status.textContent = "Saving recording...";
      return;
    }

    if (!window.isSecureContext) {
      status.textContent = "Browser recording is not available on this page.";
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = "This browser cannot access a microphone on this page.";
      return;
    }

    if (!window.MediaRecorder) {
      status.textContent = "This browser does not support in-page audio recording.";
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = chooseMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks = [];

      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      });

      recorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        recorders.delete(uid);
        button.textContent = "Record";

        const blobType = recorder.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: blobType });
        const extension = extensionForMime(blobType);
        const formData = new FormData();
        const title = titleInput.value.trim() || "voice-message";
        formData.append("title", title);
        formData.append("recording", blob, `${title}.${extension}`);

        try {
          const response = await fetch(`/characters/${encodeURIComponent(uid)}/recordings`, {
            method: "POST",
            body: formData,
          });
          const payload = await response.json();
          status.textContent = payload.message || "Recording saved.";
          if (response.ok) {
            window.setTimeout(() => window.location.reload(), 900);
          }
        } catch (error) {
          status.textContent = `Recording upload failed: ${error}`;
        } finally {
          button.disabled = false;
        }
      });

      recorder.start();
      recorders.set(uid, { recorder });
      button.textContent = "Stop";
      status.textContent = "Recording...";
    } catch (error) {
      status.textContent = `Microphone unavailable: ${error}`;
    }
  });
});

function chooseMimeType() {
  const options = [
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/mp4",
    "audio/webm",
  ];
  return options.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function extensionForMime(mimeType) {
  if (mimeType.includes("ogg")) {
    return "ogg";
  }
  if (mimeType.includes("mp4")) {
    return "mp4";
  }
  return "webm";
}

const bluetoothPanel = document.querySelector("[data-bluetooth-panel]");

if (bluetoothPanel) {
  const refreshButton = bluetoothPanel.querySelector("[data-bluetooth-refresh]");
  const scanButtonBluetooth = bluetoothPanel.querySelector("[data-bluetooth-scan]");
  const powerButton = bluetoothPanel.querySelector("[data-bluetooth-power]");
  const statusLine = bluetoothPanel.querySelector("[data-bluetooth-status]");
  const adapterValue = bluetoothPanel.querySelector("[data-bluetooth-adapter]");
  const messageValue = bluetoothPanel.querySelector("[data-bluetooth-message]");
  const outputValue = bluetoothPanel.querySelector("[data-bluetooth-output]");
  const scanStateValue = bluetoothPanel.querySelector("[data-bluetooth-scan-state]");
  const countValue = bluetoothPanel.querySelector("[data-bluetooth-count]");
  const deviceList = bluetoothPanel.querySelector("[data-bluetooth-devices]");
  let bluetoothPowered = null;
  let bluetoothAvailable = false;

  refreshButton?.addEventListener("click", () => {
    refreshBluetoothStatus("Refreshing Bluetooth...");
  });

  scanButtonBluetooth?.addEventListener("click", async () => {
    await runBluetoothRequest("/api/bluetooth/scan", {
      label: "Scanning for Bluetooth devices...",
      busyButton: scanButtonBluetooth,
      busyText: "Scanning...",
    });
  });

  powerButton?.addEventListener("click", async () => {
    const nextPower = bluetoothPowered !== true;
    await runBluetoothRequest("/api/bluetooth/power", {
      label: nextPower ? "Turning Bluetooth on..." : "Turning Bluetooth off...",
      busyButton: powerButton,
      busyText: nextPower ? "Powering on..." : "Powering off...",
      fetchOptions: {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: nextPower }),
      },
    });
  });

  deviceList?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-bluetooth-device-action]");
    if (!button) {
      return;
    }
    const device = button.closest("[data-device-address]");
    const address = device?.dataset.deviceAddress;
    const action = button.dataset.bluetoothDeviceAction;
    if (!address || !action) {
      return;
    }

    await runBluetoothRequest(
      `/api/bluetooth/devices/${encodeURIComponent(address)}/${encodeURIComponent(action)}`,
      {
        label: `${humanizeBluetoothAction(action)} ${address}...`,
        busyButton: button,
        busyText: "Working...",
      },
    );
  });

  refreshBluetoothStatus();

  async function refreshBluetoothStatus(label = "") {
    if (label && statusLine) {
      statusLine.textContent = label;
    }
    try {
      const response = await fetch("/api/bluetooth/status");
      const payload = await response.json();
      updateBluetoothPanel(payload);
      if (label && statusLine) {
        statusLine.textContent = payload.message || "";
      }
    } catch (error) {
      if (statusLine) {
        statusLine.textContent = `Bluetooth refresh failed: ${error}`;
      }
    }
  }

  async function runBluetoothRequest(url, options = {}) {
    const fetchOptions = options.fetchOptions || {};
    const button = options.busyButton || null;
    const originalText = button?.textContent || "";

    if (statusLine && options.label) {
      statusLine.textContent = options.label;
    }
    if (button) {
      button.disabled = true;
      button.textContent = options.busyText || "Working...";
    }

    try {
      const response = await fetch(url, { method: "POST", ...fetchOptions });
      const payload = await response.json();
      updateBluetoothPanel(payload);
      if (statusLine) {
        statusLine.textContent = payload.message || (response.ok ? "Bluetooth action complete." : "Bluetooth action failed.");
      }
    } catch (error) {
      if (statusLine) {
        statusLine.textContent = `Bluetooth action failed: ${error}`;
      }
    } finally {
      if (button) {
        if (button === powerButton) {
          button.disabled = !bluetoothAvailable;
          button.textContent = bluetoothPowered ? "Power off" : "Power on";
        } else if (button === scanButtonBluetooth) {
          button.disabled = !bluetoothAvailable;
          button.textContent = originalText;
        } else {
          button.disabled = false;
          button.textContent = originalText;
        }
      }
    }
  }

  function updateBluetoothPanel(payload) {
    bluetoothPowered = payload.powered === true;
    const available = payload.available === true;
    bluetoothAvailable = available;

    if (adapterValue) {
      adapterValue.textContent = available ? (bluetoothPowered ? "on" : "off") : "unavailable";
    }
    if (messageValue) {
      messageValue.textContent = payload.message || "";
    }
    if (outputValue) {
      outputValue.textContent = payload.default_sink || "unknown";
    }
    if (scanStateValue) {
      scanStateValue.textContent = payload.discovering ? "scanning" : "idle";
    }
    if (countValue) {
      countValue.textContent = Array.isArray(payload.devices) ? String(payload.devices.length) : "0";
    }
    if (scanButtonBluetooth) {
      scanButtonBluetooth.disabled = !available;
    }
    if (powerButton) {
      powerButton.disabled = !available;
      powerButton.textContent = bluetoothPowered ? "Power off" : "Power on";
    }
    if (deviceList) {
      renderBluetoothDevices(payload.devices || [], available);
    }
  }

  function renderBluetoothDevices(devices, available) {
    if (!devices.length) {
      deviceList.innerHTML = '<p class="empty-state">No Bluetooth devices found yet.</p>';
      return;
    }

    deviceList.innerHTML = devices.map((device) => bluetoothDeviceHtml(device, available)).join("");
  }

  function bluetoothDeviceHtml(device, available) {
    const labels = [];
    if (device.connected) {
      labels.push("connected");
    }
    if (device.paired) {
      labels.push("paired");
    }
    if (device.trusted) {
      labels.push("trusted");
    }
    const labelHtml = labels.map((label) => `<span>${escapeHtml(label)}</span>`).join("");
    const typeLabel = device.audio ? "speaker" : device.icon || "device";
    const disabled = available ? "" : " disabled";

    return `
      <article class="bluetooth-device" data-device-address="${escapeHtml(device.address)}">
        <div>
          <h3>${escapeHtml(device.name || device.address)}</h3>
          <p class="muted">${escapeHtml(device.address)} · ${escapeHtml(typeLabel)}</p>
          <p class="tag-list">${labelHtml}</p>
        </div>
        <div class="bluetooth-device-actions">
          <button class="button" type="button" data-bluetooth-device-action="use-audio"${disabled}>Use for audio</button>
          <button class="button button-secondary" type="button" data-bluetooth-device-action="pair"${disabled}>Pair</button>
          <button class="button button-secondary" type="button" data-bluetooth-device-action="connect"${disabled}>Connect</button>
          <button class="button button-secondary" type="button" data-bluetooth-device-action="disconnect"${disabled}>Disconnect</button>
        </div>
      </article>
    `;
  }
}

function humanizeBluetoothAction(action) {
  if (action === "use-audio") {
    return "Trying experimental audio output for";
  }
  return `${action.charAt(0).toUpperCase()}${action.slice(1)}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
