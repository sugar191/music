function showOverlay() {
    const overlay = document.getElementById("overlay");
    if (overlay) overlay.style.display = "flex";
}

function hideOverlay() {
    const overlay = document.getElementById("overlay");
    if (overlay) overlay.style.display = "none";
}

document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('.menu-toggle');
  const menu = document.getElementById('menu');

  if (!toggle || !menu) return;

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.classList.toggle('show');
  });

  // メニュー外クリックで閉じる
  window.addEventListener('click', () => {
    menu.classList.remove('show');
  });

  // メニュー内クリックで閉じない
  menu.addEventListener('click', (e) => e.stopPropagation());
});
