/* Toggle visibility of password fields via an eye icon.
   Wraps each <input type=password> in a field-styled container so the
   reveal/hide button sits flush inside the input border, matching the
   .inp look. */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("input[type=password]").forEach((input) => {
    if (input.dataset.pwEye) return;
    input.dataset.pwEye = "1";

    // Preserve any classes/width the input already has
    const wrapper = document.createElement("div");
    wrapper.className = "pw-toggle-wrap inline-block";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    // Input keeps its text styling but loses its border/bg so the wrapper
    // provides a single unified border with the button sitting inside it.
    input.classList.add("pw-toggle-input");
    input.style.paddingRight = "2.5rem";
    input.style.borderRight = "none";
    input.style.borderRadius = "var(--radius-sm) 0 0 var(--radius-sm)";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle-btn";
    btn.title = "Show password";
    let shown = false;
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      shown = !shown;
      input.type = shown ? "text" : "password";
      btn.title = shown ? "Hide password" : "Show password";
    });
    wrapper.appendChild(btn);
  });
});
