// DEPA console: confirmation prompts and live task progress. Everything works without it too.
"use strict";

document.addEventListener("submit", (event) => {
  const message = (event.submitter && event.submitter.dataset.confirm) || event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

const log = document.getElementById("task-log");
if (log && log.dataset.stream) {
  const source = new EventSource(log.dataset.stream);
  source.addEventListener("line", (event) => {
    const item = document.createElement("li");
    item.textContent = JSON.parse(event.data);
    log.append(item);
    log.scrollTop = log.scrollHeight;
  });
  source.addEventListener("end", () => {
    source.close();
    window.location.reload();
  });
}
