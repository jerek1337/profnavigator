/*
 * Виджет «ПрофНавигатор FinTech» для школьного сайта.
 * Вставьте перед </body>:
 *   <script src="https://ВАШ_АДРЕС/widget.js" defer></script>
 * Необязательно: data-label="Текст кнопки"
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) return;
  var base = script.src.replace(/\/widget\.js(\?.*)?$/, "");
  var label = script.getAttribute("data-label") || "Узнай свою профессию";

  function mount() {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-expanded", "false");
    button.style.cssText = "position:fixed;right:20px;bottom:20px;z-index:2147483000;padding:12px 18px;border:0;border-radius:24px;background:#1D3FB8;color:#fff;font:600 15px/1.2 system-ui,sans-serif;box-shadow:0 6px 18px rgba(19,38,107,.35);cursor:pointer";
    var frame = null;
    button.addEventListener("click", function () {
      if (!frame) {
        frame = document.createElement("iframe");
        frame.src = base + "/?embed=1&source=site";
        frame.title = "ПрофНавигатор FinTech";
        frame.style.cssText = "position:fixed;right:20px;bottom:76px;z-index:2147483000;width:min(400px,calc(100vw - 40px));height:min(640px,calc(100vh - 110px));border:0;border-radius:12px;box-shadow:0 12px 40px rgba(19,38,107,.3);background:#F4F7FC";
        document.body.appendChild(frame);
        button.setAttribute("aria-expanded", "true");
        return;
      }
      var open = frame.style.display !== "none";
      frame.style.display = open ? "none" : "block";
      button.setAttribute("aria-expanded", String(!open));
    });
    document.body.appendChild(button);
  }

  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
})();
