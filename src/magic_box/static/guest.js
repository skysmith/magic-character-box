const guestPanel = document.querySelector("[data-guest-recorder]");

if (guestPanel) {
  const uploadUrl = guestPanel.dataset.uploadUrl;
  const recordButton = guestPanel.querySelector("[data-guest-record]");
  const sendButton = guestPanel.querySelector("[data-guest-send]");
  const recordActions = guestPanel.querySelector("[data-guest-record-actions]");
  const recordNote = guestPanel.querySelector("[data-guest-record-note]");
  const titleInput = guestPanel.querySelector("[data-guest-title]");
  const preview = guestPanel.querySelector("[data-guest-preview]");
  const statusWrap = guestPanel.querySelector("[data-guest-upload-status]");
  const statusText = guestPanel.querySelector("[data-guest-status]");
  const progress = guestPanel.querySelector("[data-guest-progress]");
  const fileInput = guestPanel.querySelector("[data-guest-file]");
  const uploadTrigger = guestPanel.querySelector("[data-guest-upload-trigger]");
  let activeRecorder = null;
  let recordedBlob = null;
  let recordedExtension = "webm";
  const browserRecordingAvailable =
    window.isSecureContext === true &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    Boolean(window.MediaRecorder);

  if (browserRecordingAvailable) {
    if (recordActions) {
      recordActions.hidden = false;
    }
    if (recordNote) {
      recordNote.textContent = "You can record here or upload an existing voice memo.";
    }
  } else {
    recordActions?.remove();
    preview?.remove();
    if (recordNote) {
      recordNote.textContent = "Use Upload voice memo from this link.";
    }
  }

  recordButton?.addEventListener("click", async () => {
    if (!browserRecordingAvailable) {
      return;
    }

    if (activeRecorder) {
      activeRecorder.stop();
      recordButton.disabled = true;
      setStatus("Preparing preview...", 0);
      return;
    }

    if (!window.isSecureContext) {
      setStatus("Browser recording is not available on this page.", 0);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setStatus("This browser cannot record here. Use Upload voice memo instead.", 0);
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

      recorder.addEventListener("stop", () => {
        stream.getTracks().forEach((track) => track.stop());
        activeRecorder = null;
        recordedBlob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        recordedExtension = extensionForMime(recordedBlob.type);
        preview.src = URL.createObjectURL(recordedBlob);
        preview.hidden = false;
        sendButton.disabled = false;
        recordButton.disabled = false;
        recordButton.textContent = "Record again";
        setStatus("Preview it, then send it to the box.", 0);
      });

      activeRecorder = recorder;
      recordedBlob = null;
      preview.hidden = true;
      sendButton.disabled = true;
      recorder.start();
      recordButton.textContent = "Stop";
      setStatus("Recording...", 0);
    } catch (error) {
      setStatus(`Microphone unavailable: ${error}`, 0);
    }
  });

  sendButton?.addEventListener("click", () => {
    if (!browserRecordingAvailable) {
      return;
    }

    if (!recordedBlob) {
      setStatus("Record a message first.", 0);
      return;
    }
    const title = titleInput?.value?.trim() || "guest-message";
    uploadBlob(recordedBlob, `${title}.${recordedExtension}`, title);
  });

  uploadTrigger?.addEventListener("click", () => {
    fileInput?.click();
  });

  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) {
      return;
    }
    const title = titleInput?.value?.trim() || file.name.replace(/\.[^.]+$/, "") || "guest-message";
    uploadBlob(file, file.name, title);
  });

  function uploadBlob(blob, filename, title) {
    const formData = new FormData();
    formData.append("title", title);
    formData.append("recording", blob, filename);
    setBusy(true);
    setStatus("Uploading...", 0);

    const request = new XMLHttpRequest();
    request.open("POST", uploadUrl);
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) {
        setStatus("Uploading...", null);
        return;
      }
      const percent = Math.round((event.loaded / event.total) * 100);
      setStatus(percent >= 100 ? "Processing audio..." : `Uploading... ${percent}%`, percent);
    });
    request.addEventListener("load", () => {
      const payload = parseJson(request.responseText);
      if (request.status >= 200 && request.status < 300) {
        setStatus(payload?.message || "Saved. Thank you.", 100);
        if (recordButton) {
          recordButton.textContent = "Record another";
        }
        recordedBlob = null;
        if (sendButton) {
          sendButton.disabled = true;
        }
        return;
      }
      setStatus(payload?.message || "Upload failed.", 0);
    });
    request.addEventListener("error", () => {
      setStatus("Upload failed. Check the connection and try again.", 0);
    });
    request.addEventListener("loadend", () => {
      setBusy(false);
      if (fileInput) {
        fileInput.value = "";
      }
    });
    request.send(formData);
  }

  function setBusy(isBusy) {
    if (recordButton) {
      recordButton.disabled = isBusy;
    }
    if (uploadTrigger) {
      uploadTrigger.disabled = isBusy;
    }
    if (isBusy && sendButton) {
      sendButton.disabled = true;
    } else if (recordedBlob && sendButton) {
      sendButton.disabled = false;
    }
  }

  function setStatus(message, percent) {
    if (statusWrap) {
      statusWrap.hidden = false;
    }
    if (statusText) {
      statusText.textContent = message;
    }
    if (!progress) {
      return;
    }
    if (percent === null) {
      progress.removeAttribute("value");
    } else {
      progress.max = 100;
      progress.value = percent;
    }
  }
}

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

function parseJson(value) {
  try {
    return JSON.parse(value);
  } catch (_error) {
    return null;
  }
}
