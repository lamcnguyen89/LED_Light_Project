"use strict";

const gallery = document.querySelector("#gallery");
const message = document.querySelector("#message");
const uploadForm = document.querySelector("#upload-form");
const uploadButton = document.querySelector("#upload-button");
const dialog = document.querySelector("#delete-dialog");
const confirmDelete = document.querySelector("#confirm-delete");
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
let pendingDelete = null;
let revision = null;
let refreshing = false;
let mutating = false;

function showMessage(text, error = false) {
  message.textContent = text;
  message.classList.toggle("error", error);
  message.hidden = false;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options, headers: { "X-CSRF-Token": csrfToken, ...options.headers }
  });
  let result;
  try { result = await response.json(); }
  catch { throw new Error("The server did not return a valid response. Try refreshing."); }
  if (!response.ok && !result.errors) {
    throw new Error(result.error || "The request failed.");
  }
  return result;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fileSize(bytes) {
  return bytes < 1024 * 1024 ? (bytes / 1024).toFixed(1) + " KiB" :
    (bytes / (1024 * 1024)).toFixed(1) + " MiB";
}

function renderFiles(files) {
  gallery.replaceChildren();
  document.querySelector("#count").textContent = files.length;
  document.querySelector("#empty").hidden = files.length > 0;
  for (const file of files) {
    const card = element("article", "card");
    const preview = element("div", "preview");
    const previewUrl = "/preview/" + encodeURIComponent(file.name) + "?v=" + file.modified;
    if (file.error) {
      preview.append(element("p", "preview-error", "Preview unavailable"));
    } else {
      const image = element("img");
      image.src = previewUrl;
      image.alt = file.name;
      image.loading = "lazy";
      image.addEventListener("error", () => {
        preview.replaceChildren(element("p", "preview-error", "Preview unavailable"));
      }, { once: true });
      preview.append(image);
    }
    const body = element("div", "card-body");
    body.append(element("h3", "filename", file.name));
    const dimensions = file.error ? "Unreadable image" : file.width + " × " + file.height +
      (file.frames > 1 ? " · " + file.frames + " frames" : "");
    body.append(element("p", "file-meta", dimensions + " · " + fileSize(file.size)));
    const bottom = element("div", "card-bottom");
    const matrixGroup = element("div");
    if (!file.error) {
      const matrix = element("img", "matrix");
      matrix.src = previewUrl + "&matrix=1";
      matrix.alt = "20 by 15 matrix preview of " + file.name;
      matrix.loading = "lazy";
      matrixGroup.append(matrix, element("p", "matrix-label", "MATRIX · FIRST FRAME"));
    }
    const button = element("button", "delete-button", "Delete");
    button.type = "button";
    button.setAttribute("aria-label", "Delete " + file.name);
    button.addEventListener("click", () => {
      if (mutating) return;
      pendingDelete = file.name;
      document.querySelector("#delete-description").textContent = file.name;
      dialog.showModal();
      document.querySelector("#cancel-delete").focus();
    });
    bottom.append(matrixGroup, button);
    body.append(bottom);
    card.append(preview, body);
    gallery.append(card);
  }
}

async function refresh(force = false) {
  if (refreshing || mutating) return;
  refreshing = true;
  try {
    const result = await api("/api/files");
    const nextRevision = JSON.stringify(result.files);
    if (force || nextRevision !== revision) {
      renderFiles(result.files);
      revision = nextRevision;
    }
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    refreshing = false;
    document.querySelector("#loading").hidden = true;
  }
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (mutating) return;
  mutating = true;
  uploadButton.disabled = true;
  uploadButton.textContent = "Uploading…";
  try {
    const result = await api("/api/upload", { method: "POST", body: new FormData(uploadForm) });
    const lines = [];
    if (result.saved.length) lines.push("Added: " + result.saved.join(", "));
    for (const error of result.errors) lines.push(error.name + ": " + error.error);
    showMessage(lines.join("\n"), result.errors.length > 0);
    if (result.saved.length) uploadForm.reset();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    mutating = false;
    uploadButton.disabled = false;
    uploadButton.textContent = "Upload files";
    await refresh(true);
  }
});

document.querySelector("#cancel-delete").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => { pendingDelete = null; });
confirmDelete.addEventListener("click", async () => {
  if (!pendingDelete || mutating) return;
  const name = pendingDelete;
  mutating = true;
  confirmDelete.disabled = true;
  try {
    await api("/api/files/" + encodeURIComponent(name), { method: "DELETE" });
    dialog.close();
    showMessage("Deleted " + name + ".");
  } catch (error) {
    showMessage(error.message, true);
    dialog.close();
  } finally {
    mutating = false;
    confirmDelete.disabled = false;
    await refresh(true);
  }
});
document.querySelector("#refresh").addEventListener("click", () => refresh(true));
refresh();
setInterval(() => {
  if (!document.hidden && !dialog.open) refresh();
}, 5000);

const brightnessSlider = document.querySelector("#brightness");
const brightnessValue = document.querySelector("#brightness-value");
const brightnessStatus = document.querySelector("#brightness-status");
let queuedBrightness = null;
let sendingBrightness = false;
let brightnessTimer = null;

function showBrightness() {
  brightnessValue.value = Math.round(Number(brightnessSlider.value) / 255 * 100) + "%";
}

async function sendBrightness() {
  if (sendingBrightness || queuedBrightness === null) return;
  sendingBrightness = true;
  const value = queuedBrightness;
  queuedBrightness = null;
  try {
    await api("/api/brightness", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brightness: value })
    });
    if (queuedBrightness === null) brightnessStatus.textContent = "Brightness saved";
  } catch (error) {
    brightnessStatus.textContent = error.message;
  } finally {
    sendingBrightness = false;
    // Serialize requests and keep only the newest pending slider position.
    if (queuedBrightness !== null) scheduleBrightness();
  }
}

function scheduleBrightness() {
  if (brightnessTimer !== null) return;
  brightnessTimer = setTimeout(() => {
    brightnessTimer = null;
    sendBrightness();
  }, 50);
}

brightnessSlider.addEventListener("input", () => {
  showBrightness();
  queuedBrightness = Number(brightnessSlider.value);
  brightnessStatus.textContent = "Adjusting brightness…";
  scheduleBrightness();
});
brightnessSlider.addEventListener("change", () => {
  // Flush the final position immediately when the slider is released.
  if (brightnessTimer !== null) clearTimeout(brightnessTimer);
  brightnessTimer = null;
  queuedBrightness = Number(brightnessSlider.value);
  sendBrightness();
});

async function loadBrightness() {
  try {
    const result = await api("/api/brightness");
    brightnessSlider.value = result.brightness;
    showBrightness();
    brightnessSlider.disabled = false;
    brightnessStatus.textContent = "Drag to adjust the lights";
  } catch (error) {
    brightnessStatus.textContent = error.message;
  }
}
loadBrightness();
