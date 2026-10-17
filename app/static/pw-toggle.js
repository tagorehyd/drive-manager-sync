/* Toggle visibility of password fields via an eye icon. */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("input[type=password]").forEach((input) => {
    if (input.dataset.pwEye) return;
    input.dataset.pwEye = "1";
    input.style.paddingRight = "28px";
    const wrap = document.createElement("div");
    wrap.className = "relative";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "absolute right-2 top-1/2 -mt-1 p-1 text-slate-500 hover:text-slate-800 focus:outline-none";
    btn.innerHTML =
      '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
      '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zm20-3c1.5-.5 3-1 4.5-1.5S20.5 5 19 4c-1.5-1-3-2-4-2s-2.5 1-4 2c-1.5 1-3 2-4 2-1.5 1-3 2-4.5 1.5-1.5.5-2 1-2 2s.5 1.5 2 2 2 1 3 1 4 .5 4 .5z"/>' +
      '<path d="M18 8a3 3 0 11-6 0 3 3 0 016 0z"/>' +
      '</svg>';
    btn.title = "Show password";
    let shown = false;
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      shown = !shown;
      input.type = shown ? "text" : "password";
      btn.title = shown ? "Hide password" : "Show password";
    });
    wrap.appendChild(btn);
  });
});
