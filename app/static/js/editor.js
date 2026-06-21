function wrapText(before, after) {
  const el = document.getElementById('postText');
  const start = el.selectionStart, end = el.selectionEnd;
  el.value = el.value.slice(0, start) + before + el.value.slice(start, end) + after + el.value.slice(end);
  el.focus(); el.selectionStart = start + before.length; el.selectionEnd = end + before.length; preview();
}
function preview() {
  const text = document.getElementById('postText').value;
  const counter = document.getElementById('counter'), previewText = document.getElementById('previewText');
  if (counter) counter.textContent = text.length;
  if (previewText) {
    previewText.replaceChildren();
    if (!text) previewText.textContent = 'Текст публикации появится здесь';
    else {
      const parsed = new DOMParser().parseFromString(text, 'text/html');
      parsed.body.childNodes.forEach(node => previewText.appendChild(safePreviewNode(node)));
    }
  }
  const button = document.getElementById('previewButton'), buttonInput = document.getElementById('buttonText');
  if (button && buttonInput) { button.textContent = buttonInput.value; button.classList.toggle('d-none', !buttonInput.value); }
}

function safePreviewNode(node) {
  if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
  const allowed = ['B', 'STRONG', 'I', 'EM', 'U', 'S', 'A', 'BR'];
  if (!allowed.includes(node.nodeName)) return document.createTextNode(node.textContent || '');
  const clean = document.createElement(node.nodeName.toLowerCase());
  if (node.nodeName === 'A') {
    try {
      const url = new URL(node.getAttribute('href'), window.location.origin);
      if (['http:', 'https:'].includes(url.protocol)) clean.href = url.href;
    } catch (_) {}
    clean.target = '_blank'; clean.rel = 'noopener noreferrer';
  }
  node.childNodes.forEach(child => clean.appendChild(safePreviewNode(child)));
  return clean;
}
function showFiles(input) { document.getElementById('fileList').textContent = [...input.files].map(f => f.name).join(', '); }
