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
  if (previewText) previewText.innerHTML = text || 'Текст публикации появится здесь';
  const button = document.getElementById('previewButton'), buttonInput = document.getElementById('buttonText');
  if (button && buttonInput) { button.textContent = buttonInput.value; button.classList.toggle('d-none', !buttonInput.value); }
}
function showFiles(input) { document.getElementById('fileList').textContent = [...input.files].map(f => f.name).join(', '); }
