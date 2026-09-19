// PCT CRM — job card field form: signature pad, photo capture, GPS, repeatable rows

// ---- Signature pad (canvas-based) -----------------------------------------
function initSignaturePad(canvasId, hiddenInputId, clearBtnId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const hiddenInput = document.getElementById(hiddenInputId);
  let drawing = false;
  let hasDrawn = false;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    // A canvas inside a hidden container (e.g. a wizard step not yet
    // shown) measures 0x0 — sizing the drawing buffer to that would make
    // every stroke silently draw onto nothing, producing a signature
    // that LOOKS captured (the hidden field gets some short, broken
    // data: value) but is actually blank. Skip until it's really visible.
    if (rect.width === 0 || rect.height === 0) return;
    const ratio = window.devicePixelRatio || 1;
    // Preserve whatever's already drawn across a resize (e.g. a device
    // rotation while signing), rather than silently wiping it.
    const prior = hasDrawn ? canvas.toDataURL('image/png') : null;
    canvas.width = rect.width * ratio;
    canvas.height = rect.height * ratio;
    ctx.scale(ratio, ratio);
    ctx.strokeStyle = '#0a0f1e';
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (prior) {
      const img = new Image();
      img.onload = () => ctx.drawImage(img, 0, 0, rect.width, rect.height);
      img.src = prior;
    }
  }
  resize();
  // Re-measure whenever the canvas's actual on-screen size changes —
  // most importantly, the moment it becomes visible after being hidden.
  if ('ResizeObserver' in window) {
    new ResizeObserver(resize).observe(canvas);
  }

  function pos(e) {
    const rect = canvas.getBoundingClientRect();
    const point = e.touches ? e.touches[0] : e;
    return { x: point.clientX - rect.left, y: point.clientY - rect.top };
  }
  function start(e) {
    drawing = true; hasDrawn = true;
    const p = pos(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    e.preventDefault();
  }
  function move(e) {
    if (!drawing) return;
    const p = pos(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    e.preventDefault();
  }
  function end() {
    if (!drawing) return;
    drawing = false;
    if (hiddenInput) hiddenInput.value = canvas.toDataURL('image/png');
  }

  canvas.addEventListener('mousedown', start);
  canvas.addEventListener('mousemove', move);
  window.addEventListener('mouseup', end);
  canvas.addEventListener('touchstart', start, { passive: false });
  canvas.addEventListener('touchmove', move, { passive: false });
  canvas.addEventListener('touchend', end);

  const clearBtn = document.getElementById(clearBtnId);
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      hasDrawn = false;
      if (hiddenInput) hiddenInput.value = '';
    });
  }
}

// ---- GPS capture -------------------------------------------------------------
function captureGPS(latFieldId, lngFieldId, statusId) {
  const status = document.getElementById(statusId);
  if (!navigator.geolocation) {
    if (status) status.textContent = 'GPS not supported on this device.';
    return;
  }
  if (status) status.textContent = 'Locating…';
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      document.getElementById(latFieldId).value = pos.coords.latitude;
      document.getElementById(lngFieldId).value = pos.coords.longitude;
      if (status) status.textContent = `Captured: ${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}`;
    },
    () => { if (status) status.textContent = 'Could not get location — check device permissions.'; },
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

// ---- Photo capture (camera or file, stored as base64 data URLs, tagged
// before/after so field documentation and any regulatory record-keeping
// can tell what a site looked like before treatment vs after) --------------
const capturedPhotos = [];

function initPhotoCapture(inputId, previewId, hiddenFieldId, photoType) {
  const input = document.getElementById(inputId);
  const preview = document.getElementById(previewId);
  const hidden = document.getElementById(hiddenFieldId);
  if (!input) return;

  input.addEventListener('change', () => {
    Array.from(input.files).forEach(file => {
      if (file.size > 5 * 1024 * 1024) {
        showToast(file.name + ' is over 5MB and was skipped.', 'error');
        return;
      }
      const reader = new FileReader();
      reader.onload = (e) => {
        capturedPhotos.push({ src: e.target.result, type: photoType });
        renderPhotoPreview(preview, hidden);
      };
      reader.readAsDataURL(file);
    });
    input.value = '';
  });
}

function renderPhotoPreview(preview, hidden) {
  const section = (label, type) => {
    const items = capturedPhotos
      .map((p, i) => [p, i])
      .filter(([p]) => p.type === type);
    if (!items.length) return '';
    return `<div style="margin-bottom:8px;">
      <div class="muted" style="font-size:10.5px;font-weight:700;text-transform:uppercase;margin-bottom:4px;">${label} (${items.length})</div>
      <div>${items.map(([p, i]) =>
        `<div style="position:relative;display:inline-block;margin:4px;">
           <img src="${p.src}" style="width:70px;height:70px;object-fit:cover;border-radius:8px;border:1px solid var(--border);">
           <button type="button" onclick="removePhoto(${i}, '${preview.id}', '${hidden.id}')"
             style="position:absolute;top:-6px;right:-6px;background:var(--danger);color:#fff;border:none;
             border-radius:50%;width:18px;height:18px;font-size:11px;cursor:pointer;">×</button>
         </div>`).join('')}</div>
    </div>`;
  };
  preview.innerHTML = section('Before', 'before') + section('After', 'after');
  hidden.value = JSON.stringify(capturedPhotos);
}

function removePhoto(index, previewId, hiddenId) {
  capturedPhotos.splice(index, 1);
  renderPhotoPreview(document.getElementById(previewId), document.getElementById(hiddenId));
}

// ---- Repeatable row builders (pests found / treatments / chemicals) ----------
function addRepeatRow(containerId, rowHtml) {
  const container = document.getElementById(containerId);
  const div = document.createElement('div');
  div.className = 'repeat-row';
  div.innerHTML = rowHtml;
  container.appendChild(div);
}

function removeRepeatRow(btn) {
  btn.closest('.repeat-row').remove();
}

function collectRepeatRows(containerId, fields) {
  const rows = document.querySelectorAll('#' + containerId + ' .repeat-row');
  const out = [];
  rows.forEach(row => {
    const obj = {};
    let hasValue = false;
    fields.forEach(f => {
      const el = row.querySelector('[data-field="' + f + '"]');
      const val = el ? el.value : '';
      obj[f] = val;
      if (val) hasValue = true;
    });
    if (hasValue) out.push(obj);
  });
  return out;
}
