document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.querySelector('.menu-toggle');
    const menu = document.querySelector('.menu');

    // トグルボタンでメニュー開閉
    toggle.addEventListener('click', (e) => {
        e.stopPropagation(); // クリックイベントがwindowに届かないように
        menu.classList.toggle('show');
    });

    // メニュー自体をクリックしても閉じない
    menu.addEventListener('click', (e) => {
        e.stopPropagation();
    });

    // メニュー外をクリックしたら閉じる
    window.addEventListener('click', () => {
        menu.classList.remove('show');
    });
});

