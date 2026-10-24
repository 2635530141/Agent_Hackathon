// Two-handle range: keeps the handles from crossing and updates the fill and label.
// Without this script the inputs still submit, and the server rejects low > high.
document.querySelectorAll("[data-range-pair]").forEach((pair) => {
  const [low, high] = pair.querySelectorAll('input[type="range"]');
  const output = pair.querySelector("output");
  const min = Number(low.min);
  const span = Number(low.max) - min;

  function update(moved) {
    if (Number(low.value) > Number(high.value)) {
      moved.value = moved === low ? high.value : low.value;
    }
    pair.style.setProperty("--low", (low.value - min) / span);
    pair.style.setProperty("--high", (high.value - min) / span);
    // Both handles at the top: lift the low one so it can still be dragged down.
    low.style.zIndex = Number(low.value) === Number(low.max) ? 3 : "";
    const text = low.value === high.value ? low.value : `${low.value}–${high.value}`;
    output.textContent = `${text} ${pair.dataset.unit}`;
  }

  low.addEventListener("input", () => update(low));
  high.addEventListener("input", () => update(high));
  update(low);
});
